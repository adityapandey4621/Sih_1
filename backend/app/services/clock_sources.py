"""Derive clocks from what is already recorded, and decide from what is recorded now whether a clock's subject is finished.

Nothing is kept in memory: `sync()` can run after any outage and rebuilds exactly the clocks that should exist (creating a clock that exists is a no-op,
and an existing clock is never touched). `refresh()` completes clocks whose subject has finished, so a reminder is never raised for work that is done.

Sources (each only creates a clock for a policy that EXISTS in the application's frozen configuration - no policy, no clock):
  deficiency notice issued          -> INSTITUTION_RESPONSE   uses the due date and policy resolution stored on the notice
  official outcome first issued     -> APPEAL                 uses services/appeal_rules.py
  scrutiny case opened              -> OFFICER_STAGE          trigger `scrutiny_case_opened`, finished when the first scrutiny round is over
  EVC stage activated               -> EVC                    trigger `evc_stage_activated`, finished when a visit is scheduled
  EVC visit confirmed               -> EVC                    trigger `evc_visit_confirmed`, finished when the report is submitted
  decision case opened              -> OFFICER_STAGE          trigger `decision_case_opened`, finished when a decision is recorded
  compliance obligation / validity  -> created by their own services (services/compliance.py, services/validity.py); here only completion is derived
"""

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.domain import (
    APPEAL_TRIGGER_DECISION_UPLOADED,
    CLOCK_LIVE,
    NOTICE_ISSUED_TRIGGER,
    ClockDeadlineType,
    ClockKind,
    ClockStatus,
    CommitteeStage,
    CState,
    ResponsePolicyState,
)
from app.models_applications import Application
from app.models_clocks import Clock
from app.models_committee import CommitteeCase, CommitteeSchedule
from app.models_compliance import Obligation, ValidityItem, ValidityVersion
from app.models_decisions import Appeal, DecisionCase, EcDecision, OfficialIssuance
from app.models_scrutiny import DeficiencyNotice, ScrutinyCase
from app.services import appeal_rules, clock_policy, clocks
from app.services.applications import config_of

log = logging.getLogger("app.clocks")

TRIGGER_SCRUTINY_OPENED = "scrutiny_case_opened"
TRIGGER_EVC_ACTIVATED = "evc_stage_activated"
TRIGGER_EVC_VISIT_CONFIRMED = "evc_visit_confirmed"
TRIGGER_DECISION_OPENED = "decision_case_opened"

_SCRUTINY_FIRST_ROUND = ("AWAITING_ASSIGNMENT", "READY_FOR_SCRUTINY", "UNDER_SCRUTINY")
_EVC_BEFORE_SCHEDULED = (CState.SELECTION_PENDING.value, CState.SELECTION_REQUESTED.value, CState.SCHEDULE_PENDING.value)
_EVC_REPORT_DONE = (CState.REPORT_SUBMITTED.value, CState.COMPLETED.value)
_DECISION_BEFORE = ("AWAITING_OBSERVATIONS", "READY_FOR_DECISION")


def _cfg(cache: dict, app: Application) -> dict:
    if app.rule_version_id not in cache:
        cache[app.rule_version_id] = config_of(app)
    return cache[app.rule_version_id]


def _make(db: Session, app: Application, res: clock_policy.ClockResolution, *, kind: str, subject_type: str, subject_id: uuid.UUID, trigger_at: datetime, role: str | None, label_suffix: str = "") -> bool:
    due = res.due_from(trigger_at)
    _, created = clocks.create_clock(
        db, kind=kind, deadline_type=res.deadline_type if res.deadline_type in (ClockDeadlineType.STATUTORY.value, ClockDeadlineType.INTERNAL_SLA.value) else ClockDeadlineType.INTERNAL_SLA.value,
        subject_type=subject_type, subject_id=subject_id, policy_key=res.policy_key, label=f"{res.label}{label_suffix}", app=app, trigger_event=res.trigger_event, trigger_at=trigger_at, due_at=due,
        snapshot=res.snapshot(app.rule_version_id), responsible_role=role, not_established_reason=None if res.usable else res.message,
    )
    return created


def _generic(db: Session, settings: Settings, cache: dict, app: Application, trigger: str, *, kind: str, subject_type: str, subject_id: uuid.UUID, trigger_at: datetime, role: str | None) -> int:
    made = 0
    for res in clock_policy.resolutions_for(_cfg(cache, app), settings, trigger):
        made += _make(db, app, res, kind=kind, subject_type=subject_type, subject_id=subject_id, trigger_at=trigger_at, role=role)
    return made


def _sync_notices(db: Session, settings: Settings, cache: dict) -> int:
    made = 0
    for n in db.scalars(select(DeficiencyNotice).where(DeficiencyNotice.status == "ISSUED", DeficiencyNotice.issued_at.is_not(None))):
        app = db.get(Application, n.application_id)
        pr = n.policy_resolution or {}
        key = pr.get("policy_key") or "notice-response"
        if db.scalar(select(Clock.id).where(Clock.subject_type == "notice", Clock.subject_id == n.id, Clock.policy_key == key)):
            continue
        cfg_policy = next((p for p in _cfg(cache, app).get("deadlines", []) if p.get("policy_key") == pr.get("policy_key")), None)
        extras, _ = clock_policy.parse_extras((cfg_policy or {}).get("escalation"))
        # the due date is the one stored on the notice when it was issued: never recomputed
        snap = {**{k: pr.get(k) for k in ("policy_key", "deadline_type", "duration_value", "duration_unit", "calendar_convention", "clause_ref", "source", "demo", "state", "message")},
                "label": "Response to the deficiency notice", "trigger_event": NOTICE_ISSUED_TRIGGER, "rule_version_id": str(app.rule_version_id), **extras.as_dict()}
        dt = pr.get("deadline_type")
        _, created = clocks.create_clock(
            db, kind=ClockKind.INSTITUTION_RESPONSE.value, deadline_type=dt if dt in ("STATUTORY", "INTERNAL_SLA") else "INTERNAL_SLA", subject_type="notice", subject_id=n.id, policy_key=key,
            label=f"Response to deficiency notice {n.reference}", app=app, trigger_event=NOTICE_ISSUED_TRIGGER, trigger_at=n.issued_at, due_at=n.response_due_at, snapshot=snap,
            responsible_role="scrutiny_coordinator", not_established_reason=pr.get("message") or "No response period was established when the notice was issued.",
        )
        made += int(created)
    return made


def _sync_appeals(db: Session, settings: Settings, cache: dict) -> int:
    made = 0
    for case in db.scalars(select(DecisionCase).where(DecisionCase.first_issued_at.is_not(None), DecisionCase.state.in_(("ISSUED", "DECIDED")))):
        app = db.get(Application, case.application_id)
        if db.scalar(select(Clock.id).where(Clock.subject_type == "decision_case", Clock.subject_id == case.id)):
            continue
        iss = db.scalar(select(OfficialIssuance).where(OfficialIssuance.case_id == case.id).order_by(OfficialIssuance.issued_at))
        dec = db.get(EcDecision, iss.decision_id) if iss else None
        win = appeal_rules.resolve_window(_cfg(cache, app), settings, dec.outcome if dec else None)
        if win.state in (ResponsePolicyState.MISSING, ResponsePolicyState.AMBIGUOUS):
            continue  # no appeal policy exists: there is nothing to time (the institution view already says so)
        cfg_policy = next((p for p in _cfg(cache, app).get("deadlines", []) if p.get("policy_key") == win.policy_key), {})
        extras, _ = clock_policy.parse_extras(cfg_policy.get("escalation"))
        due = win.deadline(case.first_issued_at)
        snap = {**win.stable(), "label": cfg_policy.get("label") or "Appeal window", "deadline_type": cfg_policy.get("deadline_type") or "STATUTORY", "rule_version_id": str(app.rule_version_id), **extras.as_dict()}
        dt = cfg_policy.get("deadline_type")
        _, created = clocks.create_clock(
            db, kind=ClockKind.APPEAL.value, deadline_type=dt if dt in ("STATUTORY", "INTERNAL_SLA") else "STATUTORY", subject_type="decision_case", subject_id=case.id, policy_key=win.policy_key or "appeal-window",
            label="Appeal window", app=app, trigger_event=win.trigger_event or APPEAL_TRIGGER_DECISION_UPLOADED, trigger_at=case.first_issued_at, due_at=due, snapshot=snap,
            responsible_role="sac_coordinator", not_established_reason=win.message,
        )
        made += int(created)
    return made


def sync(db: Session, settings: Settings) -> int:
    """Create every clock that should exist and does not. Idempotent; safe to run at any time, from any number of processes."""
    cache: dict = {}
    made = _sync_notices(db, settings, cache) + _sync_appeals(db, settings, cache)
    for case in db.scalars(select(ScrutinyCase).where(ScrutinyCase.state.in_(_SCRUTINY_FIRST_ROUND))):
        app = db.get(Application, case.application_id)
        made += _generic(db, settings, cache, app, TRIGGER_SCRUTINY_OPENED, kind=ClockKind.OFFICER_STAGE.value, subject_type="scrutiny_case", subject_id=case.id, trigger_at=case.created_at, role="scrutiny_coordinator")
    for case in db.scalars(select(CommitteeCase).where(CommitteeCase.stage == CommitteeStage.EVC.value)):
        app = db.get(Application, case.application_id)
        if case.state in _EVC_BEFORE_SCHEDULED:
            made += _generic(db, settings, cache, app, TRIGGER_EVC_ACTIVATED, kind=ClockKind.EVC.value, subject_type="committee_case", subject_id=case.id, trigger_at=case.activated_at, role="evc_coordinator")
        sched = db.scalar(select(CommitteeSchedule).where(CommitteeSchedule.case_id == case.id, CommitteeSchedule.kind == "VISIT", CommitteeSchedule.status == "CONFIRMED"))
        if sched is not None and case.state not in _EVC_REPORT_DONE:
            made += _generic(db, settings, cache, app, TRIGGER_EVC_VISIT_CONFIRMED, kind=ClockKind.EVC.value, subject_type="committee_case", subject_id=case.id, trigger_at=sched.start_at, role="evc_coordinator")
    for case in db.scalars(select(DecisionCase).where(DecisionCase.state.in_(_DECISION_BEFORE))):
        app = db.get(Application, case.application_id)
        made += _generic(db, settings, cache, app, TRIGGER_DECISION_OPENED, kind=ClockKind.OFFICER_STAGE.value, subject_type="decision_case", subject_id=case.id, trigger_at=case.opened_at, role="regulation_bureau")
    db.flush()
    return made


# ---- is the subject finished? (always answered from the persisted row) ----------------------------------


def _done_notice(db: Session, clock: Clock) -> tuple[bool, str]:
    n = db.get(DeficiencyNotice, clock.subject_id)
    return (n is None or n.status != "ISSUED"), "response_received_or_notice_closed"


def _done_decision_case(db: Session, clock: Clock) -> tuple[bool, str]:
    case = db.get(DecisionCase, clock.subject_id)
    if case is None:
        return True, "case_missing"
    if clock.kind == ClockKind.APPEAL.value:
        if db.scalar(select(Appeal.id).where(Appeal.application_id == case.application_id)):
            return True, "appeal_submitted"
        return case.state == "CLOSED", "decision_case_closed"
    return case.state not in _DECISION_BEFORE, "decision_recorded"


def _done_scrutiny_case(db: Session, clock: Clock) -> tuple[bool, str]:
    case = db.get(ScrutinyCase, clock.subject_id)
    return (case is None or case.state not in _SCRUTINY_FIRST_ROUND), "first_scrutiny_round_over"


def _done_committee_case(db: Session, clock: Clock) -> tuple[bool, str]:
    case = db.get(CommitteeCase, clock.subject_id)
    if case is None:
        return True, "case_missing"
    if clock.trigger_event == TRIGGER_EVC_VISIT_CONFIRMED:
        return case.state in _EVC_REPORT_DONE, "report_submitted"
    return case.state not in _EVC_BEFORE_SCHEDULED, "visit_scheduled"


def _done_obligation(db: Session, clock: Clock) -> tuple[bool, str]:
    o = db.get(Obligation, clock.subject_id, populate_existing=True)
    # Evidence that has been SUBMITTED is not compliance: only a recorded verification (or a recorded outcome / withdrawal) finishes the clock.
    return (o is None or o.status in ("VERIFIED", "NOT_MET", "WITHDRAWN")), f"obligation_{(o.status if o else 'missing').lower()}"


def _done_validity(db: Session, clock: Clock) -> tuple[bool, str]:
    v = db.get(ValidityVersion, clock.subject_id, populate_existing=True)
    if v is None:
        return True, "version_missing"
    item = db.get(ValidityItem, v.item_id, populate_existing=True)
    if item is not None and item.status == "RESOLVED":
        return True, "resolved_by_authorised_person"
    return v.status == "SUPERSEDED", "renewal_verified"


_DONE: dict[str, Callable[[Session, Clock], tuple[bool, str]]] = {
    "notice": _done_notice, "decision_case": _done_decision_case, "scrutiny_case": _done_scrutiny_case, "committee_case": _done_committee_case,
    "obligation": _done_obligation, "validity_version": _done_validity,
}


def is_done(db: Session, clock: Clock) -> tuple[bool, str]:
    fn = _DONE.get(clock.subject_type)
    return fn(db, clock) if fn else (False, "unknown_subject")


@dataclass
class Refreshed:
    completed: int = 0
    checked: int = 0


def refresh(db: Session, only: uuid.UUID | None = None) -> Refreshed:
    """Complete every live clock whose subject is finished according to the database right now."""
    out = Refreshed()
    q = select(Clock).where(Clock.status.in_(CLOCK_LIVE))
    if only is not None:
        q = q.where(Clock.id == only)
    for clock in db.scalars(q).all():
        out.checked += 1
        done, why = is_done(db, clock)
        if done and clocks.complete(db, clock, why):
            out.completed += 1
    return out


def refresh_clock(db: Session, clock: Clock) -> bool:
    """True if the clock is still live (not finished) after re-reading its subject."""
    if clock.status not in CLOCK_LIVE:
        return False
    done, why = is_done(db, clock)
    if done:
        clocks.complete(db, clock, why)
        return False
    return True


_ = ClockStatus
