"""Deadline clocks: create (once), pause / resume (only where the policy allows), complete, cancel.

A clock is created from a resolved policy, a citation by an authorised person, or a verified date. It is never recomputed afterwards: a
rule version published later, or a change to an internal service level, cannot move it. The ORIGINAL due date stays on the clock; approved
pause time is added separately (`paused_seconds`) so the history is always visible.
"""

import uuid
from datetime import datetime, timedelta

from fastapi import Request
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app import audit
from app.config import Settings, get_settings
from app.domain import CLOCK_LIVE, ClockStatus, WorkStatus
from app.models import User, utcnow
from app.models_applications import Application
from app.models_clocks import Clock, ClockEvent, ClockSegment, ScheduledWork
from app.security import as_utc
from app.services.errors import Issue, conflict, invalid, not_found


def effective_due(clock: Clock) -> datetime | None:
    """Original due date plus approved pause time. While PAUSED the date is open (it moves when the clock resumes), so it is not shown as a fixed moment."""
    if clock.due_at is None or clock.status == ClockStatus.PAUSED.value:
        return None
    return as_utc(clock.due_at) + timedelta(seconds=clock.paused_seconds or 0)


def timing(clock: Clock, now: datetime, settings: Settings) -> str:
    """Where a clock stands, derived from persisted fields at the moment of asking. Never stored."""
    if clock.status in (ClockStatus.COMPLETED.value, ClockStatus.CANCELLED.value, ClockStatus.NOT_ESTABLISHED.value, ClockStatus.PAUSED.value):
        return clock.status
    due = effective_due(clock)
    if due is None:
        return "NO_DATE"
    if now > due:
        return "OVERDUE"
    return "DUE_SOON" if due - now <= timedelta(hours=settings.clock_due_soon_hours) else "ON_TRACK"


def event(db: Session, clock: Clock, action: str, actor: User | None = None, *, from_status: str | None = None, to_status: str | None = None, detail: dict | None = None) -> None:
    db.add(ClockEvent(clock_id=clock.id, actor_id=actor.id if actor else None, action=action, from_status=from_status, to_status=to_status, detail=detail))


def create_clock(
    db: Session, *, kind: str, deadline_type: str, subject_type: str, subject_id: uuid.UUID, policy_key: str, label: str, app: Application, trigger_event: str,
    trigger_at: datetime | None, due_at: datetime | None, snapshot: dict, responsible_role: str | None = None, not_established_reason: str | None = None,
    rule_version_id: uuid.UUID | None = None, actor: User | None = None,
) -> tuple[Clock, bool]:
    """Create the clock for (subject, policy) if it does not exist; otherwise return the existing one UNCHANGED. Returns (clock, created)."""
    existing = db.scalar(select(Clock).where(Clock.subject_type == subject_type, Clock.subject_id == subject_id, Clock.policy_key == policy_key))
    if existing is not None:
        return existing, False
    status = ClockStatus.RUNNING if due_at is not None else ClockStatus.NOT_ESTABLISHED
    clock = Clock(
        kind=kind, deadline_type=deadline_type, subject_type=subject_type, subject_id=subject_id, policy_key=policy_key, label=label[:300], application_id=app.id, institution_id=app.institution_id,
        rule_version_id=rule_version_id or app.rule_version_id, trigger_event=trigger_event, trigger_at=trigger_at, start_at=trigger_at, due_at=due_at, status=status.value,
        not_established_reason=None if due_at is not None else (not_established_reason or "No deadline could be established from the configuration."), policy_snapshot=snapshot, responsible_role=responsible_role,
    )
    db.add(clock)
    db.flush()
    event(db, clock, "clock_created" if due_at is not None else "clock_not_established", actor, to_status=status.value, detail={"policy_key": policy_key, "trigger_event": trigger_event, "reason": clock.not_established_reason})
    return clock, True


# ---- state changes -----------------------------------------------------------------------------------------


def _cas(db: Session, clock: Clock, from_statuses: tuple[str, ...], **values) -> bool:
    res = db.execute(update(Clock).where(Clock.id == clock.id, Clock.status.in_(from_statuses), Clock.row_version == clock.row_version).values(row_version=clock.row_version + 1, updated_at=utcnow(), **values))
    if res.rowcount != 1:
        return False
    db.refresh(clock)
    return True


def cancel_pending_work(db: Session, clock_id: uuid.UUID, reason: str) -> int:
    now = utcnow()
    res = db.execute(update(ScheduledWork).where(ScheduledWork.clock_id == clock_id, ScheduledWork.status.in_((WorkStatus.PENDING.value, WorkStatus.RETRY_WAIT.value))).values(
        status=WorkStatus.CANCELLED.value, outcome=f"cancelled_{reason}", updated_at=now, processed_at=now))
    return res.rowcount


def complete(db: Session, clock: Clock, reason: str, detail: dict | None = None, actor: User | None = None) -> bool:
    """Mark a live clock COMPLETED because the thing it timed has happened. Pending alerts for it are cancelled. Idempotent."""
    if clock.status not in CLOCK_LIVE:
        return False
    before = clock.status
    if not _cas(db, clock, CLOCK_LIVE, status=ClockStatus.COMPLETED.value, completed_at=utcnow(), completion_reason=reason[:80], completion_detail=detail):
        db.refresh(clock)
        return False
    cancel_pending_work(db, clock.id, "completed")
    event(db, clock, "clock_completed", actor, from_status=before, to_status=ClockStatus.COMPLETED.value, detail={"reason": reason, **(detail or {})})
    return True


def cancel(db: Session, clock: Clock, reason: str, actor: User | None = None) -> bool:
    if clock.status not in CLOCK_LIVE and clock.status != ClockStatus.NOT_ESTABLISHED.value:
        return False
    before = clock.status
    if not _cas(db, clock, (*CLOCK_LIVE, ClockStatus.NOT_ESTABLISHED.value), status=ClockStatus.CANCELLED.value, completed_at=utcnow(), completion_reason=reason[:80]):
        db.refresh(clock)
        return False
    cancel_pending_work(db, clock.id, "clock_cancelled")
    event(db, clock, "clock_cancelled", actor, from_status=before, to_status=ClockStatus.CANCELLED.value, detail={"reason": reason})
    return True


def get_clock(db: Session, clock_id: uuid.UUID) -> Clock:
    clock = db.get(Clock, clock_id, populate_existing=True)
    if clock is None:
        raise not_found("Clock")
    return clock


def pause_rule(clock: Clock) -> dict:
    return (clock.policy_snapshot or {}).get("pause") or {}


def pause(db: Session, user: User, clock_id: uuid.UUID, reason_code: str | None, reason: str, reference: str | None, expected_version: int, request: Request | None = None) -> Clock:
    """Pause a clock. Allowed only when ITS OWN policy says pausing is allowed (and, if the policy lists reasons, only for one of them). Nothing else can stop a clock."""
    clock = get_clock(db, clock_id)
    rule = pause_rule(clock)
    if clock.status != ClockStatus.RUNNING.value:
        raise conflict("invalid_transition", f"Only a running clock can be paused; this one is {clock.status.replace('_', ' ').lower()}.")
    if not rule.get("allowed"):
        raise conflict("pause_not_permitted", "This clock's policy does not permit pausing. Its deadline cannot be stopped, moved or reset.")
    allowed = rule.get("reasons") or []
    if allowed and reason_code not in allowed:
        raise invalid("The policy permits pausing only for specific reasons.", [Issue("reason_code", f"Choose one of: {', '.join(allowed)}.", "reason_code")])
    if clock.row_version != expected_version:
        raise conflict("stale_version", "This clock was changed by someone else since you opened it. Reload it to see the latest state.")
    now = utcnow()
    if not _cas(db, clock, (ClockStatus.RUNNING.value,), status=ClockStatus.PAUSED.value):
        raise conflict("stale_version", "This clock was changed by someone else at the same moment. Reload it and try again.")
    db.add(ClockSegment(clock_id=clock.id, paused_at=now, reason=reason, reference=reference, paused_by_id=user.id, permitted_by={"policy_key": clock.policy_key, "reason_code": reason_code, "rule_version_id": (clock.policy_snapshot or {}).get("rule_version_id")}))
    cancel_pending_work(db, clock.id, "paused")
    event(db, clock, "clock_paused", user, from_status=ClockStatus.RUNNING.value, to_status=ClockStatus.PAUSED.value, detail={"reason_code": reason_code, "reference": reference})
    audit.record(db, "clock.paused", request=request, actor=user, target_type="clock", target_id=clock.id, detail={"policy_key": clock.policy_key, "reason_code": reason_code})
    return clock


def resume(db: Session, user: User, clock_id: uuid.UUID, expected_version: int, request: Request | None = None, settings: Settings | None = None) -> Clock:
    """Resume a paused clock. The time it was paused is added to the effective due date; the original due date is untouched."""
    from app.services import scheduler

    clock = get_clock(db, clock_id)
    if clock.status != ClockStatus.PAUSED.value:
        raise conflict("invalid_transition", "Only a paused clock can be resumed.")
    if clock.row_version != expected_version:
        raise conflict("stale_version", "This clock was changed by someone else since you opened it. Reload it to see the latest state.")
    seg = db.scalar(select(ClockSegment).where(ClockSegment.clock_id == clock.id, ClockSegment.resumed_at.is_(None)).order_by(ClockSegment.paused_at.desc()))
    now = utcnow()
    added = int((now - as_utc(seg.paused_at)).total_seconds()) if seg else 0
    if not _cas(db, clock, (ClockStatus.PAUSED.value,), status=ClockStatus.RUNNING.value, paused_seconds=(clock.paused_seconds or 0) + max(added, 0)):
        raise conflict("stale_version", "This clock was changed by someone else at the same moment. Reload it and try again.")
    if seg is not None:
        seg.resumed_at, seg.resumed_by_id = now, user.id
    event(db, clock, "clock_resumed", user, from_status=ClockStatus.PAUSED.value, to_status=ClockStatus.RUNNING.value, detail={"paused_seconds_added": added, "effective_due_at": effective_due(clock).isoformat() if effective_due(clock) else None})
    audit.record(db, "clock.resumed", request=request, actor=user, target_type="clock", target_id=clock.id, detail={"policy_key": clock.policy_key, "paused_seconds_added": added})
    scheduler.plan_alerts(db, clock, settings or get_settings())
    return clock
