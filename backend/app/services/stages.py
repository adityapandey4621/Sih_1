"""The route engine: which stage comes next is read from the application's FROZEN route, never assumed.

  scrutiny completes -> `on_scrutiny_handoff`      (Phase 4's StageHandoff records the resolution; this activates what it names)
  EVC / SHC completes -> `advance_from_case`       (a RouteTransition records the resolution)

For each completed stage the frozen route says one of:
  QUEUED                  a single, non-conditional next stage: it is activated (idempotently) if it is an EVC / SHC stage
  AWAITING_DETERMINATION  the next stage is conditional ("AA" in Table 2.3) or there is a choice: an authorised person determines, with a reason
  NO_FURTHER_STAGE        the route configures nothing more: the application is handed to the LATER decision process (a record only)
  EXCEPTION               the route is missing or inconsistent: nothing is guessed; the case waits for configuration review

Nothing here equates "scrutiny finished cleanly" with "no EVC needed", or "an EVC report exists" with "an SHC is needed". Whether a stage applies is
whatever the approved route configuration (plus a recorded human determination where it is conditional) says. Nothing here decides an application.
"""

import uuid

from fastapi import Request
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import committee_access
from app.config import get_settings
from app.domain import (
    INITIAL_STATE,
    CommitteeStage,
    CState,
    GapReason,
    HandoffStatus,
    TransitionStatus,
    committee_stage_for,
)
from app.models import User, utcnow
from app.models_applications import Application, ApplicationEvent
from app.models_committee import CommitteeCase, CommitteeReport, DecisionHandoff, EvcObservation, RouteTransition
from app.models_scrutiny import ScrutinyCase, ScrutinyFinding, StageHandoff
from app.services import committee_core as core
from app.services import committee_policy as cpolicy
from app.services import committee_texts as ctexts
from app.services import routing
from app.services import rules as rules_svc
from app.services.applications import config_of
from app.services.errors import conflict, invalid

# ---- what the activation records ------------------------------------------------------------------


def identified_shortcomings(db: Session, app: Application) -> list[dict]:
    """Points the earlier stages left open, as references for the SHC coordinator to turn into institution-facing requirements.
    These are INTERNAL records; nothing here is shown to the institution as it stands."""
    out: list[dict] = []
    for f in db.scalars(select(ScrutinyFinding).where(ScrutinyFinding.application_id == app.id, ScrutinyFinding.state == "UNRESOLVED").order_by(ScrutinyFinding.sequence)):
        out.append({"origin": "scrutiny_finding", "id": str(f.id), "sequence": f.sequence, "item_key": f.item_key, "summary": f.description[:400]})
    evc = db.scalar(select(CommitteeCase).where(CommitteeCase.application_id == app.id, CommitteeCase.stage == CommitteeStage.EVC.value))
    if evc is not None:
        for o in db.scalars(select(EvcObservation).where(EvcObservation.case_id == evc.id, EvcObservation.shortcoming.is_(True)).order_by(EvcObservation.item_key)):
            out.append({"origin": "evc_observation", "id": str(o.id), "item_key": o.item_key, "summary": (o.note or "")[:400]})
    return out


def _scrutiny_outcome(sc: ScrutinyCase | None) -> dict | None:
    if sc is None or not sc.completion:
        return None
    c = sc.completion
    return {"outcome": c.get("outcome"), "counts": c.get("counts"), "completed_round": c.get("completed_round"), "completed_at": sc.completed_at.isoformat() if sc.completed_at else None, "approved": False}


# ---- activation -----------------------------------------------------------------------------------


def _preceding_complete(db: Session, app: Application, from_key: str) -> bool:
    if from_key == "scrutiny":
        return db.scalar(select(ScrutinyCase.state).where(ScrutinyCase.application_id == app.id)) == "SCRUTINY_COMPLETED"
    for c in db.scalars(select(CommitteeCase).where(CommitteeCase.application_id == app.id)):
        if (c.trigger or {}).get("stage_key") == from_key:
            return c.state == CState.COMPLETED.value
    return False


def activate(
    db: Session, app: Application, stage_key: str, *, from_stage_key: str, source: str, actor: User | None, request: Request | None, determination: dict | None = None,
    from_case_id: uuid.UUID | None = None,
) -> CommitteeCase | None:
    """Create the EVC / SHC case for `stage_key`, once. Returns None when the key is not a committee stage this phase builds (it stays QUEUED)."""
    stage = committee_stage_for(stage_key)
    if stage is None:
        core.log_event(db, None, actor, "stage_module_unavailable", request=request, stage="ROUTE", application_id=app.id, detail={"stage_key": stage_key, "from_stage_key": from_stage_key})
        return None
    existing = db.scalar(select(CommitteeCase).where(CommitteeCase.application_id == app.id, CommitteeCase.stage == stage.value))
    if existing is not None:
        return existing  # a repeated request or job creates nothing new
    if not _preceding_complete(db, app, from_stage_key):
        raise conflict("preceding_stage_incomplete", f"The “{from_stage_key}” stage is not complete, so “{stage_key}” cannot be activated.")
    settings = get_settings()
    config = config_of(app)
    pol = cpolicy.applicable(db, stage, config, settings)
    snapshot = cpolicy.freeze(pol) if pol else None
    blockers = cpolicy.blockers_for(stage, snapshot)
    sc = db.scalar(select(ScrutinyCase).where(ScrutinyCase.application_id == app.id))
    trigger = {
        "stage_key": stage_key, "from_stage_key": from_stage_key, "source": source, "determination": determination, "scrutiny_outcome": _scrutiny_outcome(sc),
        "rule_version": {"id": str(app.rule_version_id), "rule_set": config["rule_set"]["key"], "number": config["version"]["number"], "classification": config["rule_set"]["classification"]},
        "route_clause": next(({"clause_ref": s.get("clause_ref"), "label": s["label"], "is_conditional": bool(s.get("is_conditional"))} for s in config.get("route", []) if s["stage_key"] == stage_key), None),
        "shortcomings": identified_shortcomings(db, app) if stage == CommitteeStage.SHC else [], "from_case_id": str(from_case_id) if from_case_id else None,
    }
    case = CommitteeCase(
        application_id=app.id, stage=stage.value, rule_version_id=app.rule_version_id, scrutiny_case_id=sc.id if sc else None, state=INITIAL_STATE[stage].value, trigger=trigger,
        policy_id=pol.id if pol else None, policy_snapshot=snapshot, blockers=blockers or None,
    )
    try:
        if db.bind.dialect.name == "postgresql":
            with db.begin_nested():
                db.add(case)
                db.flush()
        else:
            db.add(case)
            db.flush()
    except IntegrityError:  # created concurrently by the same activation: that is what the unique constraint is for
        found = db.scalar(select(CommitteeCase).where(CommitteeCase.application_id == app.id, CommitteeCase.stage == stage.value))
        if found is None:
            raise
        return found
    core.log_event(db, case, actor, "stage_activated", request=request, object_type="committee_case", object_id=case.id, to_state=case.state, institution_visible=stage == CommitteeStage.EVC,
                   detail={"stage_key": stage_key, "from_stage_key": from_stage_key, "source": source, "rule_version_id": str(app.rule_version_id), "policy": cpolicy.ref(snapshot),
                           "blockers": [b["code"] for b in blockers]})
    if blockers:
        rules_svc.record_gap(db, app.category_id, app.academic_year, GapReason.INCOMPLETE, {"missing": f"committee_policy:{stage.value}", "blockers": [b["code"] for b in blockers], "rule_version_id": str(app.rule_version_id)})
        core.log_event(db, case, None, "stage_blocked", object_type="committee_case", object_id=case.id, detail={"blockers": [b["code"] for b in blockers]})
        ctexts.stage_blocked(db, case, app, blockers[0]["code"], blockers[0]["message"])
    db.flush()
    ctexts.stage_activated(db, case, app)
    return case


# ---- after scrutiny -------------------------------------------------------------------------------


def on_scrutiny_handoff(db: Session, sc: ScrutinyCase, app: Application, handoff: StageHandoff, actor: User | None, request: Request | None) -> CommitteeCase | DecisionHandoff | None:
    """Scrutiny recorded (or a person determined) the next stage: act on it. Idempotent."""
    if handoff.status == HandoffStatus.QUEUED.value and handoff.to_stage_key:
        det = {"by_id": str(handoff.determined_by_id), "reason": handoff.determination_reason, "at": handoff.determined_at.isoformat() if handoff.determined_at else None} if handoff.determined_by_id else None
        return activate(db, app, handoff.to_stage_key, from_stage_key="scrutiny", source="scrutiny_handoff", actor=actor, request=request, determination=det)
    if handoff.status == HandoffStatus.NO_FURTHER_STAGE.value:
        return create_decision_handoff(db, app, actor, request)
    return None


# ---- after an EVC / SHC stage ---------------------------------------------------------------------


def advance_from_case(db: Session, case: CommitteeCase, actor: User | None, request: Request | None) -> RouteTransition:
    """The stage just completed: record what the frozen route says follows, and act on it. Idempotent."""
    if case.state != CState.COMPLETED.value:
        raise conflict("stage_incomplete", "The route can advance only from a completed stage.")
    app = core.application(db, case)
    from_key = case.trigger["stage_key"]
    existing = db.scalar(select(RouteTransition).where(RouteTransition.application_id == app.id, RouteTransition.from_stage_key == from_key))
    if existing is not None:
        return existing
    res = routing.resolve_after(config_of(app), from_key)
    rt = RouteTransition(
        application_id=app.id, from_stage_key=from_key, from_case_id=case.id, to_stage_key=res.to_stage_key, to_stage_label=res.to_stage_label, status=res.status.value,
        route_basis={**res.basis, "options": res.options}, reason=res.reason,
    )
    db.add(rt)
    db.flush()
    core.log_event(db, case, actor, "route_resolved", request=request, object_type="route_transition", object_id=rt.id,
                   detail={"from_stage_key": from_key, "status": rt.status, "to_stage_key": rt.to_stage_key, "rule_version": res.basis.get("rule_version")})
    _act_on(db, app, case, rt, actor, request)
    return rt


def _act_on(db: Session, app: Application, case: CommitteeCase, rt: RouteTransition, actor: User | None, request: Request | None) -> None:
    status = TransitionStatus(rt.status)
    if status == TransitionStatus.QUEUED and rt.to_stage_key:
        activate(db, app, rt.to_stage_key, from_stage_key=rt.from_stage_key, source="route_transition", actor=actor, request=request, from_case_id=case.id)
    elif status == TransitionStatus.NO_FURTHER_STAGE:
        create_decision_handoff(db, app, actor, request, via=case)
    elif status == TransitionStatus.AWAITING_DETERMINATION:
        ctexts.determination_needed(db, case, app, rt.reason or "The route needs a determination.")
    else:
        ctexts.stage_blocked(db, case, app, "route_unresolved", rt.reason or "The approved route cannot be applied.")


def determine_after_case(db: Session, actor: User, case_id: uuid.UUID, stage: CommitteeStage, stage_key: str | None, reason: str, request: Request | None) -> RouteTransition:
    """A person chooses among the next stages the FROZEN route allows (or records that a single conditional stage does not apply)."""
    case, _ = committee_access.get_case(db, actor, case_id, request, stage=stage, need="coordinator")
    case = core.lock_case(db, case.id)
    if case.state != CState.COMPLETED.value:
        raise conflict("stage_incomplete", "The next stage can be determined only after this stage is complete.")
    app = core.application(db, case)
    rt = db.scalar(select(RouteTransition).where(RouteTransition.application_id == app.id, RouteTransition.from_stage_key == case.trigger["stage_key"]).execution_options(populate_existing=True))
    if rt is None or rt.status != TransitionStatus.AWAITING_DETERMINATION.value:
        raise conflict("not_awaiting_determination", "There is no pending next-stage determination for this case.")
    if not reason or len(reason.strip()) < 5:
        raise invalid("Record the reason for this determination.")
    options = {o["stage_key"]: o for o in (rt.route_basis or {}).get("options", [])}
    if stage_key is None:
        if not (len(options) == 1 and next(iter(options.values()))["is_conditional"]):
            raise invalid("Choose one of the stages the approved route offers.")
        status, label = TransitionStatus.NO_FURTHER_STAGE, None
    elif stage_key not in options:
        raise invalid("That stage is not one of the next stages in this application's approved route.")
    else:
        status, label = TransitionStatus.QUEUED, options[stage_key]["label"]
    now = utcnow()
    res = db.execute(update(RouteTransition).where(RouteTransition.id == rt.id, RouteTransition.status == TransitionStatus.AWAITING_DETERMINATION.value).values(
        status=status.value, to_stage_key=stage_key, to_stage_label=label, determined_by_id=actor.id, determined_at=now, determination_reason=reason.strip(), updated_at=now))
    if res.rowcount != 1:
        raise conflict("stale_version", "Another determination was recorded at the same moment.")
    db.refresh(rt)
    core.log_event(db, case, actor, "route_determined", request=request, object_type="route_transition", object_id=rt.id, reason=reason, detail={"to_stage": stage_key, "status": status.value})
    if status == TransitionStatus.QUEUED and stage_key:
        activate(db, app, stage_key, from_stage_key=rt.from_stage_key, source="determination", actor=actor, request=request, from_case_id=case.id,
                 determination={"by_id": str(actor.id), "reason": reason.strip(), "at": now.isoformat()})
    else:
        create_decision_handoff(db, app, actor, request, via=case)
    return rt


# ---- the hand-off to the later decision process -----------------------------------------------------


def _blocked_reason(db: Session, app: Application) -> str | None:
    sc = db.scalar(select(ScrutinyCase).where(ScrutinyCase.application_id == app.id))
    if sc is None or sc.state != "SCRUTINY_COMPLETED":
        return "Scrutiny is not complete."
    handoff = db.scalar(select(StageHandoff).where(StageHandoff.case_id == sc.id))
    if handoff is not None and handoff.status in (HandoffStatus.AWAITING_DETERMINATION.value, HandoffStatus.EXCEPTION.value):
        return "The stage after scrutiny has not been determined."
    for c in db.scalars(select(CommitteeCase).where(CommitteeCase.application_id == app.id)):
        if c.state != CState.COMPLETED.value:
            return f"The {c.stage} stage is not complete."
    for rt in db.scalars(select(RouteTransition).where(RouteTransition.application_id == app.id)):
        if rt.status in (TransitionStatus.AWAITING_DETERMINATION.value, TransitionStatus.EXCEPTION.value):
            return f"The stage after “{rt.from_stage_key}” has not been determined."
        if rt.status == TransitionStatus.QUEUED.value and not db.scalar(select(CommitteeCase.id).where(CommitteeCase.application_id == app.id, CommitteeCase.stage == (committee_stage_for(rt.to_stage_key) or CommitteeStage.EVC).value)):
            return f"The next stage “{rt.to_stage_key}” has not been carried out."
    return None


def create_decision_handoff(db: Session, app: Application, actor: User | None, request: Request | None, via: CommitteeCase | None = None) -> DecisionHandoff:
    """Record that every stage the route required is complete. Refused (never forced) while any required stage is unfinished. Idempotent."""
    existing = db.scalar(select(DecisionHandoff).where(DecisionHandoff.application_id == app.id))
    if existing is not None:
        return existing
    why = _blocked_reason(db, app)
    if why:
        raise conflict("required_stage_incomplete", f"The application cannot be marked ready for the decision stage: {why}")
    config = config_of(app)
    stages: list[dict] = [{"stage_key": "scrutiny", "completed_at": (db.scalar(select(ScrutinyCase.completed_at).where(ScrutinyCase.application_id == app.id)) or utcnow()).isoformat()}]
    for c in db.scalars(select(CommitteeCase).where(CommitteeCase.application_id == app.id).order_by(CommitteeCase.activated_at)):
        rep = db.scalar(select(CommitteeReport).where(CommitteeReport.case_id == c.id, CommitteeReport.status == "SUBMITTED").order_by(CommitteeReport.version_number.desc()))
        stages.append({"stage_key": c.trigger["stage_key"], "stage": c.stage, "case_id": str(c.id), "completed_at": c.completed_at.isoformat() if c.completed_at else None,
                       "record": {"report_id": str(rep.id), "kind": rep.kind, "version": rep.version_number, "content_hash": rep.content_hash} if rep else None})
    basis = {
        "rule_version": {"id": str(app.rule_version_id), "rule_set": config["rule_set"]["key"], "number": config["version"]["number"], "classification": config["rule_set"]["classification"]},
        "route": [s["stage_key"] for s in config.get("route", [])], "stages": stages, "decided": False,
        "note": "A hand-off record only. No decision, approval or rejection has been made; the decision belongs to the later, authorised process.",
    }
    row = DecisionHandoff(application_id=app.id, basis=basis, created_by_id=actor.id if actor else None)
    db.add(row)
    db.flush()
    db.add(ApplicationEvent(application_id=app.id, actor_id=actor.id if actor else None, action="ready_for_decision_stage", detail={"approved": False, "stages": [s["stage_key"] for s in stages]}))
    core.log_event(db, via, actor, "decision_handoff_ready", request=request, object_type="decision_handoff", object_id=row.id, stage="ROUTE", application_id=app.id, detail={"stages": [s["stage_key"] for s in stages]})
    if via is not None:
        ctexts.decision_handoff_ready(db, via, app)
    from app.services import decisions  # Phase 6: the hand-off opens the Executive Committee's decision case (idempotent, same transaction)

    decisions.open_case(db, row, app, actor, request)
    return row


# ---- recovery -------------------------------------------------------------------------------------


def reconcile(db: Session) -> int:
    """After a restart or a failure between steps: activate any stage a recorded resolution names but whose case was never created. Idempotent."""
    made = 0
    for h in db.scalars(select(StageHandoff).where(StageHandoff.status.in_((HandoffStatus.QUEUED.value, HandoffStatus.NO_FURTHER_STAGE.value)))):
        sc = db.get(ScrutinyCase, h.case_id)
        app = db.get(Application, h.application_id)
        before = db.scalar(select(CommitteeCase.id).where(CommitteeCase.application_id == app.id)), db.scalar(select(DecisionHandoff.id).where(DecisionHandoff.application_id == app.id))
        try:
            on_scrutiny_handoff(db, sc, app, h, None, None)
        except Exception:  # noqa: BLE001 - one bad application must not stop the rest; it stays visible as unreconciled
            db.rollback()
            continue
        after = db.scalar(select(CommitteeCase.id).where(CommitteeCase.application_id == app.id)), db.scalar(select(DecisionHandoff.id).where(DecisionHandoff.application_id == app.id))
        made += int(before != after)
    for rt in db.scalars(select(RouteTransition).where(RouteTransition.status.in_((TransitionStatus.QUEUED.value, TransitionStatus.NO_FURTHER_STAGE.value)))):
        case = db.get(CommitteeCase, rt.from_case_id)
        app = db.get(Application, rt.application_id)
        try:
            _act_on(db, app, case, rt, None, None)
            made += 0
        except Exception:  # noqa: BLE001
            db.rollback()
    from app.services import decisions

    for h in db.scalars(select(DecisionHandoff)):
        try:
            decisions.open_case(db, h, db.get(Application, h.application_id), None, None)
        except Exception:  # noqa: BLE001
            db.rollback()
    db.commit()
    return made
