"""Visit (EVC) and hearing (SHC) schedules.

A schedule is *proposed*, then *confirmed*. Rescheduling or cancelling closes the row (with a reason) and, for a reschedule, adds a new one that points back at
it: nothing is overwritten. Duplicate requests (same idempotency key) return the same schedule.

What is deliberately NOT done:
  * No availability data exists, so a member is never declared "free". The only conflict detected is the one the system itself knows about: the same
    member already holds another live schedule that starts at the same instant. (No visit duration is configured, so overlap cannot be computed.)
  * No scheduling deadline, minimum notice or maximum delay is invented.
  * A mode is accepted only if the case's frozen policy lists it. Modes the verified sources do not support (hybrid) are refused with the reason.
"""

import uuid
from datetime import UTC, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import Request
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import committee_access
from app.domain import CAction, CommitteeEvent, CommitteeStage, CState, ScheduleKind, ScheduleStatus
from app.models import User, utcnow
from app.models_committee import CommitteeAssignment, CommitteeCase, CommitteeSchedule
from app.services import committee_core as core
from app.services import committee_policy as cpolicy
from app.services import committee_texts as ctexts
from app.services.errors import Issue, conflict, invalid, not_found

_LIVE = (ScheduleStatus.PROPOSED.value, ScheduleStatus.CONFIRMED.value)
_SHC_OPEN = (CState.PREPARATION, CState.AWAITING_COMPLIANCE, CState.COMPLIANCE_RECEIVED, CState.UNDER_REVIEW)


def kind_for(stage: CommitteeStage) -> ScheduleKind:
    return ScheduleKind.VISIT if stage == CommitteeStage.EVC else ScheduleKind.HEARING  # an SHC or SAC sitting is a hearing


def active(db: Session, case_id: uuid.UUID) -> CommitteeSchedule | None:
    return db.scalar(select(CommitteeSchedule).where(CommitteeSchedule.case_id == case_id, CommitteeSchedule.status.in_(_LIVE)).execution_options(populate_existing=True))


def history(db: Session, case_id: uuid.UUID) -> list[CommitteeSchedule]:
    return list(db.scalars(select(CommitteeSchedule).where(CommitteeSchedule.case_id == case_id).order_by(CommitteeSchedule.sequence)))


def to_utc(start_local: str, tz_name: str) -> datetime:
    """Local wall-clock time + IANA zone -> an instant. Offsets inside the string are refused: the zone is stated once, explicitly."""
    try:
        tz = ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        raise invalid("Choose a valid time zone (for example Asia/Kolkata).", [Issue("timezone", "Unknown time zone.", "timezone")]) from None
    try:
        naive = datetime.fromisoformat(start_local)
    except ValueError:
        raise invalid("Enter the date and time as YYYY-MM-DDTHH:MM.", [Issue("start_local", "Not a valid date and time.", "start_local")]) from None
    if naive.tzinfo is not None:
        raise invalid("Enter the local date and time without an offset; the time zone is chosen separately.", [Issue("start_local", "Remove the offset.", "start_local")])
    return naive.replace(tzinfo=tz).astimezone(UTC)


def _validate_details(mode_def: dict, data: dict) -> list[Issue]:
    out: list[Issue] = []
    for field_name in mode_def.get("requires", []):
        if not (data.get(field_name) or "").strip():
            out.append(Issue(field_name, "This is needed for the chosen mode.", field_name))
    if mode_def.get("requires_reason") and not (data.get("mode_reason") or "").strip():
        out.append(Issue("mode_reason", f"Record the circumstance that justifies a {mode_def['label'].lower()} visit.", "mode_reason"))
    return out


def _check_member_conflicts(db: Session, case: CommitteeCase, start_at: datetime) -> None:
    members = [a.user_id for a in db.scalars(select(CommitteeAssignment).where(CommitteeAssignment.case_id == case.id, CommitteeAssignment.revoked_at.is_(None)))]
    if not members:
        return
    other = db.scalar(
        select(CommitteeSchedule.id).join(CommitteeAssignment, CommitteeAssignment.case_id == CommitteeSchedule.case_id)
        .where(CommitteeSchedule.case_id != case.id, CommitteeSchedule.status.in_(_LIVE), CommitteeSchedule.start_at == start_at, CommitteeAssignment.revoked_at.is_(None), CommitteeAssignment.user_id.in_(members)).limit(1)
    )
    if other is not None:
        raise conflict("member_conflict", "An assigned member already has another visit or hearing scheduled to start at that exact time. (The system knows only its own schedules, not anyone's general availability.)")


def _gate(db: Session, case: CommitteeCase, stage: CommitteeStage) -> None:
    if stage == CommitteeStage.EVC:
        core.require_state(case, CState.SCHEDULE_PENDING, CState.SCHEDULED)
        core.require_no_blockers(case, "no_committee_policy", "no_visit_modes")
    else:
        core.require_state(case, CState.UNDER_REVIEW) if stage == CommitteeStage.SAC else core.require_state(case, *_SHC_OPEN)
        h = core.policy(case).get("content", {}).get("sac" if stage == CommitteeStage.SAC else "shc", {}).get("hearing")
        if core.policy(case) == {}:
            core.require_no_blockers(case, "no_committee_policy")
        if h not in ("OPTIONAL", "REQUIRED"):
            raise conflict("hearing_not_permitted", "The configuration does not establish that a hearing is part of this process, so none can be scheduled.")
        if not db.scalar(select(CommitteeAssignment.id).where(CommitteeAssignment.case_id == case.id, CommitteeAssignment.revoked_at.is_(None)).limit(1)):
            raise conflict("no_members_assigned", "Members must be assigned (through the selection step) before a hearing is scheduled.")


def propose(
    db: Session, actor: User, case_id: uuid.UUID, stage: CommitteeStage, data: dict, idempotency_key: str, request: Request | None, *, reschedule_reason: str | None = None,
) -> tuple[CommitteeCase, CommitteeSchedule, bool]:
    """Propose a schedule; with `reschedule_reason`, replace the live one (which is closed as SUPERSEDED, with that reason). -> (case, schedule, replay)."""
    case, _ = committee_access.get_case(db, actor, case_id, request, stage=stage, need="coordinator")
    prior = db.scalar(select(CommitteeSchedule).where(CommitteeSchedule.idempotency_key == idempotency_key))
    if prior is not None:
        if prior.case_id != case.id:
            raise conflict("idempotency_key_reused", "That key was used for a different case.")
        return core.lock_case(db, case.id), prior, True
    case = core.lock_case(db, case.id)
    _gate(db, case, stage)
    live = active(db, case.id)
    if live is not None and reschedule_reason is None:
        raise conflict("schedule_exists", "A schedule is already proposed or confirmed. Reschedule or cancel it instead.")
    if reschedule_reason is not None and (not reschedule_reason.strip() or len(reschedule_reason.strip()) < 5):
        raise invalid("Record the reason for rescheduling.")
    if reschedule_reason is not None and live is None:
        raise conflict("nothing_to_reschedule", "There is no live schedule to replace.")
    kind = kind_for(stage)
    modes = {m["key"]: m for m in cpolicy.mode_options(case.policy_snapshot, stage)}
    mode = data.get("mode")
    if mode not in modes:
        unver = [m for m in (core.policy(case).get("content", {}).get("visit", {}).get("unverified_modes", [])) if m["key"] == mode]
        raise invalid(unver[0]["reason"] if unver else "That mode is not permitted by the configuration for this case.", [Issue("mode", "Not a permitted mode.", "mode")])
    issues = _validate_details(modes[mode], data)
    if issues:
        raise invalid("Some details needed for this mode are missing.", issues)
    start_at = to_utc(data["start_local"], data["timezone"])
    if start_at <= utcnow():
        raise invalid("The proposed time must be in the future.", [Issue("start_local", "Choose a future time.", "start_local")])
    _check_member_conflicts(db, case, start_at)
    now = utcnow()
    from_state = case.state
    superseded = live
    if live is not None:
        res = db.execute(update(CommitteeSchedule).where(CommitteeSchedule.id == live.id, CommitteeSchedule.status.in_(_LIVE)).values(
            status=ScheduleStatus.SUPERSEDED.value, closed_at=now, closed_by_id=actor.id, closed_reason=reschedule_reason.strip(), row_version=live.row_version + 1))
        if res.rowcount != 1:
            raise conflict("stale_version", "The schedule was changed by someone else at the same moment. Reload it and try again.")
        db.flush()
    seq = (db.scalar(select(func.max(CommitteeSchedule.sequence)).where(CommitteeSchedule.case_id == case.id, CommitteeSchedule.kind == kind.value)) or 0) + 1
    sch = CommitteeSchedule(
        case_id=case.id, kind=kind.value, sequence=seq, status=ScheduleStatus.PROPOSED.value, start_at=start_at, timezone=data["timezone"], mode=mode, mode_reason=(data.get("mode_reason") or "").strip() or None,
        location=(data.get("location") or "").strip() or None, access_details=(data.get("access_details") or "").strip() or None, supersedes_id=superseded.id if superseded else None,
        change_reason=reschedule_reason.strip() if reschedule_reason else None, policy_ref=cpolicy.ref(case.policy_snapshot), idempotency_key=idempotency_key, created_by_id=actor.id,
    )
    try:
        db.add(sch)
        db.flush()
    except IntegrityError:  # a concurrent proposal for the same case won the unique "one live schedule" slot
        db.rollback()
        raise conflict("schedule_exists", "A schedule was proposed at the same moment. Reload to see it.") from None
    if stage == CommitteeStage.EVC and case.state == CState.SCHEDULED.value:
        core.transition(db, case, CAction.UNSCHEDULE)
    else:
        core.touch(db, case)
    core.log_event(db, case, actor, "schedule_rescheduled" if superseded else "schedule_proposed", request=request, object_type="committee_schedule", object_id=sch.id, from_state=from_state, to_state=case.state,
                   reason=reschedule_reason, institution_visible=True, detail={"kind": kind.value, "start_at": start_at.isoformat(), "timezone": sch.timezone, "mode": mode, "sequence": seq,
                                                                                "supersedes": str(superseded.id) if superseded else None, "policy": sch.policy_ref})
    db.flush()
    app = core.application(db, case)
    ctexts.schedule_event(db, case, app, sch, CommitteeEvent.SCHEDULE_CHANGED if superseded else CommitteeEvent.SCHEDULE_PROPOSED, "Rescheduled" if superseded else "Proposed")
    return case, sch, False


def confirm(db: Session, actor: User, case_id: uuid.UUID, stage: CommitteeStage, schedule_id: uuid.UUID, row_version: int, request: Request | None) -> tuple[CommitteeCase, CommitteeSchedule]:
    case, _ = committee_access.get_case(db, actor, case_id, request, stage=stage, need="coordinator")
    case = core.lock_case(db, case.id)
    sch = db.get(CommitteeSchedule, schedule_id, populate_existing=True)
    if sch is None or sch.case_id != case.id:
        raise not_found("Schedule")
    if sch.status == ScheduleStatus.CONFIRMED.value:
        return case, sch  # a repeated confirmation changes nothing
    if sch.status != ScheduleStatus.PROPOSED.value:
        raise conflict("schedule_not_proposed", f"A {sch.status.lower()} schedule cannot be confirmed.")
    if sch.row_version != row_version:
        raise conflict("stale_version", "This schedule was changed since you opened it. Reload it to see the latest.")
    if stage == CommitteeStage.EVC:
        core.require_state(case, CState.SCHEDULE_PENDING)
    elif stage == CommitteeStage.SAC:
        core.require_state(case, CState.UNDER_REVIEW)
    else:
        core.require_state(case, *_SHC_OPEN)
    now = utcnow()
    res = db.execute(update(CommitteeSchedule).where(CommitteeSchedule.id == sch.id, CommitteeSchedule.status == ScheduleStatus.PROPOSED.value, CommitteeSchedule.row_version == row_version).values(
        status=ScheduleStatus.CONFIRMED.value, confirmed_by_id=actor.id, confirmed_at=now, row_version=row_version + 1))
    if res.rowcount != 1:
        raise conflict("stale_version", "This schedule was changed at the same moment. Reload it and try again.")
    db.refresh(sch)
    from_state = case.state
    if stage == CommitteeStage.EVC:
        core.transition(db, case, CAction.CONFIRM_SCHEDULE)
    else:
        core.touch(db, case)
    core.log_event(db, case, actor, "schedule_confirmed", request=request, object_type="committee_schedule", object_id=sch.id, from_state=from_state, to_state=case.state, institution_visible=True,
                   detail={"kind": sch.kind, "start_at": sch.start_at.isoformat(), "timezone": sch.timezone, "mode": sch.mode})
    db.flush()
    ctexts.schedule_event(db, case, core.application(db, case), sch, CommitteeEvent.SCHEDULE_CONFIRMED, "Confirmed")
    return case, sch


def cancel_schedule(db: Session, actor: User, case_id: uuid.UUID, stage: CommitteeStage, schedule_id: uuid.UUID, reason: str, request: Request | None) -> CommitteeCase:
    case, _ = committee_access.get_case(db, actor, case_id, request, stage=stage, need="coordinator")
    case = core.lock_case(db, case.id)
    sch = db.get(CommitteeSchedule, schedule_id, populate_existing=True)
    if sch is None or sch.case_id != case.id:
        raise not_found("Schedule")
    if sch.status not in _LIVE:
        raise conflict("schedule_not_live", f"A {sch.status.lower()} schedule cannot be cancelled.")
    if not reason or len(reason.strip()) < 5:
        raise invalid("Record the reason for cancelling.")
    _close(db, case, actor, sch, ScheduleStatus.CANCELLED, reason.strip(), request, "schedule_cancelled")
    return case


def cancel_active(db: Session, case: CommitteeCase, actor: User, reason: str, request: Request | None, *, silent: bool = False) -> None:
    """Close the live schedule (if any) as CANCELLED. Used when members are re-selected."""
    sch = active(db, case.id)
    if sch is not None:
        _close(db, case, actor, sch, ScheduleStatus.CANCELLED, reason, request, "schedule_cancelled", move_state=False)


def _close(db: Session, case: CommitteeCase, actor: User, sch: CommitteeSchedule, status: ScheduleStatus, reason: str, request: Request | None, action: str, *, move_state: bool = True) -> None:
    now = utcnow()
    was_confirmed = sch.status == ScheduleStatus.CONFIRMED.value
    res = db.execute(update(CommitteeSchedule).where(CommitteeSchedule.id == sch.id, CommitteeSchedule.status.in_(_LIVE)).values(
        status=status.value, closed_at=now, closed_by_id=actor.id, closed_reason=reason, row_version=sch.row_version + 1))
    if res.rowcount != 1:
        raise conflict("stale_version", "This schedule was changed at the same moment. Reload it and try again.")
    db.refresh(sch)
    from_state = case.state
    if move_state and CommitteeStage(case.stage) == CommitteeStage.EVC and was_confirmed and case.state == CState.SCHEDULED.value:
        core.transition(db, case, CAction.UNSCHEDULE)
    elif move_state:
        core.touch(db, case)
    core.log_event(db, case, actor, action, request=request, object_type="committee_schedule", object_id=sch.id, from_state=from_state, to_state=case.state, reason=reason, institution_visible=True,
                   detail={"kind": sch.kind, "start_at": sch.start_at.isoformat(), "status": status.value})
    db.flush()
    ctexts.schedule_event(db, case, core.application(db, case), sch, CommitteeEvent.SCHEDULE_CHANGED, "Cancelled")


def mark_completed(db: Session, case: CommitteeCase, actor: User, request: Request | None) -> None:
    """The visit or hearing took place: close the confirmed schedule as COMPLETED."""
    sch = active(db, case.id)
    if sch is not None and sch.status == ScheduleStatus.CONFIRMED.value:
        now = utcnow()
        db.execute(update(CommitteeSchedule).where(CommitteeSchedule.id == sch.id, CommitteeSchedule.status == ScheduleStatus.CONFIRMED.value).values(
            status=ScheduleStatus.COMPLETED.value, closed_at=now, closed_by_id=actor.id, closed_reason="Took place", row_version=sch.row_version + 1))
        core.log_event(db, case, actor, "schedule_completed", request=request, object_type="committee_schedule", object_id=sch.id, detail={"kind": sch.kind})
