"""The durable alert scheduler. Pending work lives in the `scheduled_work` table in PostgreSQL; nothing is held in memory or in a cache.

  plan      each running clock gets one PENDING row per configured alert (pre-due / at due / overdue). The row's key names the clock, the alert and
            the due date it was planned for, so planning twice - after a restart, from two processes - creates nothing new.
  claim     a worker claims a due row with a compare-and-set update and a lease. A worker that dies leaves a lease that expires; the row then runs again.
  run       BEFORE anything is sent the persisted state is read again: a clock whose subject has finished, whose due date has since moved, or for which a
            nearer alert is also due is skipped (SKIPPED_STALE) and nothing is sent. Notifications are keyed per (clock, alert, due date, recipient), so a row
            that runs twice still notifies each person once.
  retry     a failure is retried with exponential back-off up to `work_max_attempts`, then the row is FAILED_EXHAUSTED and stays visible. It is never retried forever.

The only channel is the application's own notification store. `delivery` is STORED_IN_APP when it is stored; no email or SMS is ever claimed.
"""

import logging
import os
import socket
import time
import uuid
from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import SessionLocal
from app.domain import CLOCK_LIVE, AlertKind, ClockKind, ClockStatus, NotificationEvent, WorkStatus
from app.models import User, utcnow
from app.models_applications import Application
from app.models_clocks import Clock, ScheduledWork
from app.models_compliance import Obligation
from app.models_committee import CommitteeCase
from app.models_decisions import DecisionCase
from app.models_scrutiny import Notification, ScrutinyCase
from app.rbac import Role
from app.security import as_utc
from app.services import clock_sources, clocks, notifications

log = logging.getLogger("app.scheduler")

_CLAIMABLE = (WorkStatus.PENDING.value, WorkStatus.RETRY_WAIT.value)


class DeliveryError(Exception):
    """A notification could not be stored; the work is retried (bounded)."""


def worker_id() -> str:
    return f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:6]}"


# ---- planning ---------------------------------------------------------------------------------------------


def _schedule_of(clock: Clock) -> tuple[list[int], bool, list[int]]:
    alerts = (clock.policy_snapshot or {}).get("alerts") or {}
    return list(alerts.get("pre_due_hours") or []), bool(alerts.get("at_due")), list(alerts.get("overdue_hours") or [])


def plan_alerts(db: Session, clock: Clock, settings: Settings) -> int:
    """Create the PENDING rows for a running clock's configured alerts. Idempotent. A clock whose policy configures no alerts gets none."""
    if clock.status != ClockStatus.RUNNING.value:
        return 0
    due = clocks.effective_due(clock)
    if due is None:
        return 0
    pre, at_due, overdue = _schedule_of(clock)
    plan = [(AlertKind.PRE_DUE, h, due - timedelta(hours=h)) for h in pre] + ([(AlertKind.DUE, 0, due)] if at_due else []) + [(AlertKind.OVERDUE, h, due + timedelta(hours=h)) for h in overdue]
    made = 0
    for kind, hours, run_at in plan:
        key = f"{clock.id}:{kind.value}:{hours}:{int(due.timestamp())}"
        if db.scalar(select(ScheduledWork.id).where(ScheduledWork.dedupe_key == key)) is not None:
            continue
        row = ScheduledWork(dedupe_key=key, clock_id=clock.id, alert_kind=kind.value, offset_hours=hours, planned_for_due=due, run_at=run_at, max_attempts=settings.work_max_attempts,
                            policy_ref={"policy_key": clock.policy_key, "rule_version_id": (clock.policy_snapshot or {}).get("rule_version_id"), "deadline_type": clock.deadline_type})
        try:
            if db.bind.dialect.name == "postgresql":
                with db.begin_nested():
                    db.add(row)
                    db.flush()
            else:
                db.add(row)
                db.flush()
            made += 1
        except IntegrityError:
            continue  # planned concurrently by another process: exactly what the key is for
    return made


def plan_all(db: Session, settings: Settings) -> int:
    return sum(plan_alerts(db, c, settings) for c in db.scalars(select(Clock).where(Clock.status == ClockStatus.RUNNING.value)).all())


# ---- claiming, leases, retries ------------------------------------------------------------------------------


def claim_next(db: Session, who: str, settings: Settings, now: datetime | None = None) -> ScheduledWork | None:
    now = now or utcnow()
    q = select(ScheduledWork.id).where(ScheduledWork.status.in_(_CLAIMABLE), ScheduledWork.run_at <= now).order_by(ScheduledWork.run_at).limit(5)
    if db.bind.dialect.name == "postgresql":
        q = q.with_for_update(skip_locked=True)
    for wid in db.scalars(q).all():
        res = db.execute(update(ScheduledWork).where(ScheduledWork.id == wid, ScheduledWork.status.in_(_CLAIMABLE)).values(
            status=WorkStatus.RUNNING.value, attempts=ScheduledWork.attempts + 1, locked_by=who, lease_expires_at=now + timedelta(seconds=settings.work_lease_seconds), updated_at=now))
        if res.rowcount == 1:  # compare-and-set: exactly one worker wins
            db.commit()
            return db.get(ScheduledWork, wid, populate_existing=True)
    db.rollback()
    return None


def reap_expired(db: Session, settings: Settings, now: datetime | None = None) -> int:
    """Return work whose worker vanished to the queue. This is what makes a restart lose nothing."""
    now = now or utcnow()
    n = 0
    q = select(ScheduledWork).where(ScheduledWork.status == WorkStatus.RUNNING.value, ScheduledWork.lease_expires_at < now)
    if db.bind.dialect.name == "postgresql":
        q = q.with_for_update(skip_locked=True)
    for w in db.scalars(q).all():
        if w.attempts >= w.max_attempts:
            w.status, w.last_error, w.processed_at = WorkStatus.FAILED_EXHAUSTED.value, "worker_lost", now
        else:
            w.status, w.last_error, w.run_at = WorkStatus.RETRY_WAIT.value, "worker_lost", now
        w.locked_by = w.lease_expires_at = None
        w.updated_at = now
        n += 1
    db.commit()
    return n


def _fail(db: Session, work_id: uuid.UUID, code: str, settings: Settings, now: datetime) -> None:
    w = db.get(ScheduledWork, work_id, populate_existing=True)
    if w is None or w.status != WorkStatus.RUNNING.value:
        return
    w.last_error, w.locked_by, w.lease_expires_at, w.updated_at = code[:200], None, None, now
    if w.attempts >= w.max_attempts:
        w.status, w.processed_at = WorkStatus.FAILED_EXHAUSTED.value, now
    else:
        w.status, w.run_at = WorkStatus.RETRY_WAIT.value, now + timedelta(seconds=settings.work_retry_backoff_seconds * (2 ** max(0, w.attempts - 1)))
    db.commit()


# ---- who is told ----------------------------------------------------------------------------------------------


def _unique(pairs: list[tuple[User, str]]) -> list[tuple[User, str]]:
    seen, out = set(), []
    for u, k in pairs:
        if u.id not in seen:
            seen.add(u.id)
            out.append((u, k))
    return out


def recipients_for(db: Session, clock: Clock) -> list[tuple[User, str]]:
    """Who should be told RIGHT NOW, from the persisted state (not from what was true when the alert was planned)."""
    inst = [(u, "institution") for u in notifications.institution_users(db, clock.institution_id)]
    kind = clock.kind
    if kind == ClockKind.COMPLIANCE.value:
        o = db.get(Obligation, clock.subject_id, populate_existing=True)
        verifier = [(o.verifier, "verifier")] if o is not None and o.verifier.is_active else []
        if o is not None and o.status == "EVIDENCE_SUBMITTED":
            return verifier  # the institution has acted; the outstanding work is the verifier's
        return _unique(inst + verifier)
    if kind == ClockKind.DOCUMENT_VALIDITY.value:
        return _unique(inst + [(u, "compliance_officer") for u in notifications.users_with_role(db, Role.COMPLIANCE_OFFICER)])
    if kind == ClockKind.INSTITUTION_RESPONSE.value:
        sc = db.scalar(select(ScrutinyCase).where(ScrutinyCase.application_id == clock.application_id))
        return _unique(inst + ([(u, "assigned_reviewer") for u in notifications.assigned_reviewers(db, sc.id)] if sc else []))
    if kind == ClockKind.APPEAL.value:
        return _unique(inst)
    if kind == ClockKind.EVC.value:
        cc = db.get(CommitteeCase, clock.subject_id)
        return _unique(([(u, "committee_member") for u in notifications.committee_members(db, cc.id)] if cc else []) + [(u, "evc_coordinator") for u in notifications.users_with_role(db, Role.EVC_COORDINATOR)])
    if kind == ClockKind.OFFICER_STAGE.value:
        if clock.subject_type == "scrutiny_case":
            return _unique([(u, "assigned_reviewer") for u in notifications.assigned_reviewers(db, clock.subject_id)] + [(u, "scrutiny_coordinator") for u in notifications.users_with_role(db, Role.SCRUTINY_COORDINATOR)])
        return _unique([(u, "regulation_bureau") for u in notifications.users_with_role(db, Role.REGULATION_BUREAU)])
    return []


_EVENT = {AlertKind.PRE_DUE.value: NotificationEvent.CLOCK_PRE_DUE, AlertKind.DUE.value: NotificationEvent.CLOCK_DUE, AlertKind.OVERDUE.value: NotificationEvent.CLOCK_OVERDUE}
_VERB = {ClockKind.DOCUMENT_VALIDITY.value: "expires", ClockKind.APPEAL.value: "appeal window ends", ClockKind.COMPLIANCE.value: "is due"}


def _text(clock: Clock, work: ScheduledWork, due: datetime, app: Application) -> tuple[str, str]:
    verb = _VERB.get(clock.kind, "is due")
    when = due.strftime("%d %b %Y %H:%M UTC")
    if work.alert_kind == AlertKind.PRE_DUE.value:
        title = f"{clock.label}: {verb} in {work.offset_hours} hours" if work.offset_hours < 48 else f"{clock.label}: {verb} in {work.offset_hours // 24} days"
    elif work.alert_kind == AlertKind.DUE.value:
        title = f"{clock.label}: {verb} now"
    else:
        title = f"{clock.label}: past its date" if work.offset_hours == 0 else f"{clock.label}: {work.offset_hours} hours past its date"
    body = (f"Application {app.reference}. Date: {when}. This is an automatic reminder from the system. It does not change any decision or approval, and no external message was sent.")
    return title[:200], body


# ---- running one piece of work ------------------------------------------------------------------------------


def _nearer_also_due(db: Session, work: ScheduledWork, now: datetime) -> bool:
    """After an outage several alerts of one kind can be late together. Only the nearest one is worth sending."""
    live = (WorkStatus.PENDING.value, WorkStatus.RETRY_WAIT.value, WorkStatus.RUNNING.value, WorkStatus.DONE.value)
    q = select(ScheduledWork.id).where(ScheduledWork.clock_id == work.clock_id, ScheduledWork.planned_for_due == work.planned_for_due, ScheduledWork.id != work.id, ScheduledWork.run_at <= now, ScheduledWork.status.in_(live))
    if work.alert_kind == AlertKind.PRE_DUE.value:
        q = q.where(ScheduledWork.alert_kind == work.alert_kind, ScheduledWork.offset_hours < work.offset_hours)  # a smaller offset is nearer to the due date
    elif work.alert_kind == AlertKind.OVERDUE.value:
        q = q.where(ScheduledWork.alert_kind == work.alert_kind, ScheduledWork.offset_hours > work.offset_hours)
    else:  # "due now" is pointless once an overdue alert is also due
        q = q.where(ScheduledWork.alert_kind == AlertKind.OVERDUE.value)
    return db.scalar(q.limit(1)) is not None


def _finish(db: Session, work: ScheduledWork, who: str, status: WorkStatus, outcome: str, now: datetime, recipients: list | None = None) -> bool:
    res = db.execute(update(ScheduledWork).where(ScheduledWork.id == work.id, ScheduledWork.locked_by == who, ScheduledWork.status == WorkStatus.RUNNING.value).values(
        status=status.value, outcome=outcome, recipients=recipients, delivery="STORED_IN_APP" if recipients else None, processed_at=now, updated_at=now, locked_by=None, lease_expires_at=None, last_error=None))
    return res.rowcount == 1


def process(db: Session, work: ScheduledWork, who: str, settings: Settings, now: datetime | None = None) -> str:
    """Run one claimed row. Returns its outcome. Raises nothing: a failure is recorded on the row and retried within bounds."""
    now = now or utcnow()
    wid = work.id
    try:
        outcome = _run(db, work, who, now)
        db.commit()
        return outcome
    except Exception as exc:  # noqa: BLE001 - a bad row must never stop the worker; it is retried within bounds and stays visible
        db.rollback()
        log.error("work %s failed: %s", wid, type(exc).__name__)
        _fail(db, wid, type(exc).__name__, settings, now)
        return "failed"


def _run(db: Session, work: ScheduledWork, who: str, now: datetime) -> str:
    clock = db.get(Clock, work.clock_id, populate_existing=True)
    live = clock is not None and clock_sources.refresh_clock(db, clock)  # RE-READ the subject: a finished obligation, notice or case ends the alerts
    if not live:
        _finish(db, work, who, WorkStatus.SKIPPED_STALE, "skipped_subject_finished" if clock and clock.status == ClockStatus.COMPLETED.value else "skipped_clock_not_running", now)
        return "skipped_stale"
    due = clocks.effective_due(clock)
    if clock.status != ClockStatus.RUNNING.value or due is None or as_utc(work.planned_for_due) != due:
        _finish(db, work, who, WorkStatus.SKIPPED_STALE, "skipped_due_date_changed", now)
        return "skipped_stale"
    if work.alert_kind == AlertKind.PRE_DUE.value and now >= due:
        _finish(db, work, who, WorkStatus.SKIPPED_STALE, "skipped_already_past_due", now)
        return "skipped_stale"
    if work.alert_kind == AlertKind.OVERDUE.value and now < due:
        _finish(db, work, who, WorkStatus.SKIPPED_STALE, "skipped_not_yet_due", now)
        return "skipped_stale"
    if _nearer_also_due(db, work, now):
        _finish(db, work, who, WorkStatus.SKIPPED_STALE, "skipped_superseded_by_nearer_alert", now)
        return "skipped_stale"
    app = db.get(Application, clock.application_id)
    people = recipients_for(db, clock)
    event = _EVENT[work.alert_kind]
    title, body = _text(clock, work, due, app)
    scope = f"clock:{clock.id}:{work.alert_kind}:{work.offset_hours}:{int(due.timestamp())}"
    sent: list[dict] = []
    by_kind: dict[str, list[User]] = {}
    for u, k in people:
        by_kind.setdefault(k, []).append(u)
    for k, users in by_kind.items():
        link = "/app/post-approval" if k == "institution" else "/app/deadlines"
        notifications.notify(db, event, users, kind=k, scope_key=scope, title=title, body=body, link=link, application_id=app.id, policy_ref=work.policy_ref, **_refs(clock))
        sent += [{"user_id": str(u.id), "kind": k} for u in users]
    if work.alert_kind == AlertKind.OVERDUE.value:
        esc_roles = (clock.policy_snapshot or {}).get("escalate_to") or []
        for r in esc_roles:
            users = notifications.users_with_role(db, Role(r))
            if users:
                notifications.notify(db, NotificationEvent.CLOCK_ESCALATED, users, kind="escalation", scope_key=scope, title=f"Escalation — {title}"[:200], body=body, link="/app/deadlines", application_id=app.id, policy_ref=work.policy_ref, **_refs(clock))
                sent += [{"user_id": str(u.id), "kind": "escalation"} for u in users]
    # notify() isolates storage failures so the triggering change survives; here a missing row means the alert was NOT delivered, so retry instead of recording success.
    keys = {f"{(NotificationEvent.CLOCK_ESCALATED if s['kind'] == 'escalation' else event).value}:{scope}:{s['user_id']}" for s in sent}
    stored = set(db.scalars(select(Notification.dedupe_key).where(Notification.dedupe_key.in_(keys))).all()) if keys else set()
    if keys - stored:
        raise DeliveryError("notification_not_stored")
    if not sent:
        _finish(db, work, who, WorkStatus.SKIPPED_STALE, "skipped_no_recipients", now)
        return "skipped_stale"
    _finish(db, work, who, WorkStatus.DONE, "sent", now, sent)
    return "sent"


def _refs(clock: Clock) -> dict:
    refs: dict = {"clock_id": clock.id}
    if clock.subject_type == "obligation":
        refs["obligation_id"] = clock.subject_id
    return refs


def requeue_failed(db: Session) -> int:
    """An operator decision: give alerts that exhausted their retries another bounded set of attempts (for example after the cause was fixed)."""
    now = utcnow()
    res = db.execute(update(ScheduledWork).where(ScheduledWork.status == WorkStatus.FAILED_EXHAUSTED.value).values(status=WorkStatus.PENDING.value, attempts=0, run_at=now, last_error=None, locked_by=None, lease_expires_at=None, updated_at=now))
    db.commit()
    return res.rowcount


# ---- the loop ---------------------------------------------------------------------------------------------------


def run_cycle(db: Session, who: str, settings: Settings, now: datetime | None = None, max_items: int = 200) -> dict:
    """One full pass: derive missing clocks, complete finished ones, plan alerts, return orphaned work, then drain what is due. Safe to run from any number of processes."""
    now = now or utcnow()
    stats = {"clocks_created": 0, "clocks_completed": 0, "planned": 0, "syncs_confirmed": 0, "reaped": 0, "processed": 0, "sent": 0, "skipped": 0, "failed": 0}
    from app.services import validity

    validity.sync_candidates(db, settings)
    stats["clocks_created"] = clock_sources.sync(db, settings)
    stats["clocks_completed"] = clock_sources.refresh(db).completed
    stats["planned"] = plan_all(db, settings)
    db.commit()
    try:  # external syncs that are due another attempt (their own back-off is respected); a failure here never stops the alerts below
        from app.services import portal_sync

        stats["syncs_confirmed"] = portal_sync.reconcile(db, respect_backoff=True, now=now)
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        log.error("sync reconciliation failed: %s", type(exc).__name__)
    stats["reaped"] = reap_expired(db, settings, now)
    for _ in range(max_items):
        w = claim_next(db, who, settings, now)
        if w is None:
            break
        out = process(db, w, who, settings, now)
        stats["processed"] += 1
        stats["sent" if out == "sent" else "skipped" if out == "skipped_stale" else "failed"] += 1
    return stats


def run_forever(settings: Settings | None = None, stop: "callable | None" = None) -> None:  # pragma: no cover - the process loop
    settings = settings or get_settings()
    who = worker_id()
    last_sync = 0.0
    log.info("scheduler %s started", who)
    while not (stop and stop()):
        try:
            with SessionLocal() as db:
                if time.monotonic() - last_sync >= settings.scheduler_sync_seconds:
                    run_cycle(db, who, settings)
                    last_sync = time.monotonic()
                else:
                    reap_expired(db, settings)
                    for _ in range(200):
                        w = claim_next(db, who, settings)
                        if w is None:
                            break
                        process(db, w, who, settings)
        except Exception as exc:  # noqa: BLE001 - the loop must survive a database outage and resume when it returns
            log.error("scheduler cycle failed: %s", type(exc).__name__)
        time.sleep(settings.scheduler_poll_seconds)


_ = CLOCK_LIVE, DecisionCase
