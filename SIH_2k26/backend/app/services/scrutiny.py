"""Scrutiny cases: lifecycle, assignment boundary, rounds, reviewer findings, completion and hand-off.

Principles enforced here (and tested):
  * Automated checks (Phase 3) only *suggest*. A finding exists because an assigned reviewer recorded it.
  * A case changes state only through `transition()`: one compare-and-set UPDATE against `CASE_TRANSITIONS`.
  * Completing scrutiny never approves anything. The next stage comes from the application's frozen route or
    a person's recorded determination; if the route cannot be applied the case goes to an exception state.
  * Every action leaves an event (case history) and an audit record: actor, time, object, from/to state, reason.
"""

import uuid
from dataclasses import dataclass

from fastapi import Request
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app import audit, scrutiny_access
from app.config import Settings
from app.domain import (
    CASE_TRANSITIONS,
    FINDING_PENDING_STATES,
    FINDING_TRANSITIONS,
    NOTICE_PREPARING,
    AssignmentSource,
    CaseAction,
    CaseState,
    ExtractionStatus,
    FindingState,
    HandoffStatus,
    NoticeStatus,
    RoundKind,
    RoundStatus,
    SourceRefStatus,
    Visibility,
)
from app.evidence import current_documents
from app.models import User, utcnow
from app.models_applications import Application, ApplicationDocument, ApplicationEvent
from app.models_processing import PreliminaryFinding
from app.models_scrutiny import (
    DeficiencyNotice,
    FindingEvent,
    NoticeItem,
    ScrutinyAssignment,
    ScrutinyCase,
    ScrutinyEvent,
    ScrutinyFinding,
    ScrutinyRound,
    StageHandoff,
)
from app.rbac import Role
from app.services import policy, routing
from app.services import scrutiny_texts as texts
from app.services.applications import config_of
from app.services.errors import Issue, ServiceError, conflict, invalid, not_found

ISSUE_TYPES = {
    "missing_document": "Missing document",
    "unreadable_document": "Document cannot be read",
    "inconsistent_information": "Inconsistent information",
    "incomplete_information": "Incomplete information",
    "other": "Other",
}
DEMO_ASSIGNMENT_NOTICE = "Demonstration assignment: a coordinator chose a fictional reviewer. This is NOT the official AICTE committee selection process."


# ---- history ------------------------------------------------------------------------------------


def log_event(
    db: Session, case: ScrutinyCase, actor: User | None, action: str, *, request: Request | None = None, object_type: str | None = None, object_id: uuid.UUID | str | None = None,
    from_state: str | None = None, to_state: str | None = None, reason: str | None = None, detail: dict | None = None, institution_visible: bool = False,
) -> None:
    """One case-history event plus the matching audit record. `detail` holds identifiers and states, never document content."""
    db.add(ScrutinyEvent(
        case_id=case.id, application_id=case.application_id, actor_id=actor.id if actor else None, actor_label=actor.full_name if actor else "System", action=action,
        object_type=object_type, object_id=str(object_id) if object_id else None, from_state=from_state, to_state=to_state, reason=reason, detail=detail,
        institution_visible=institution_visible,
    ))
    audit.record(
        db, f"scrutiny.{action}", request=request, actor=actor, target_type=object_type or "scrutiny_case", target_id=object_id or case.id,
        detail={"case_id": str(case.id), "application_id": str(case.application_id), "from": from_state, "to": to_state, "reason": (reason or "")[:500] or None, **(detail or {})},
    )


# ---- case access and state ----------------------------------------------------------------------


def lock_case(db: Session, case_id: uuid.UUID) -> ScrutinyCase:
    q = select(ScrutinyCase).where(ScrutinyCase.id == case_id).execution_options(populate_existing=True)
    if db.bind.dialect.name == "postgresql":
        q = q.with_for_update()
    case = db.scalars(q).one_or_none()
    if case is None:
        raise not_found("Scrutiny case")
    return case


def transition(db: Session, case: ScrutinyCase, action: CaseAction, *, expected_version: int | None = None, **extra) -> CaseState:
    """The only way a case's state changes. Compare-and-set on the current state (and optionally the row version)."""
    try:
        current = CaseState(case.state)
    except ValueError:
        raise ServiceError(409, "invalid_transition", "The case is in an unrecognised state.") from None
    target = CASE_TRANSITIONS.get((current, action))
    if target is None:
        raise conflict("invalid_transition", f"That action is not allowed while the case is “{current.value}”.")
    if expected_version is not None and case.row_version != expected_version:
        raise conflict("stale_version", "This case was changed by someone else since you opened it. Reload it to see the latest state.")
    now = utcnow()
    cond = [ScrutinyCase.id == case.id, ScrutinyCase.state == current.value, ScrutinyCase.row_version == case.row_version]
    res = db.execute(update(ScrutinyCase).where(*cond).values(state=target.value, row_version=case.row_version + 1, updated_at=now, state_changed_at=now, **extra))
    if res.rowcount != 1:
        raise conflict("stale_version", "This case was changed by someone else at the same moment. Reload it and try again.")
    db.refresh(case)
    return target


def latest_round(db: Session, case_id: uuid.UUID) -> ScrutinyRound | None:
    return db.scalar(select(ScrutinyRound).where(ScrutinyRound.case_id == case_id).order_by(ScrutinyRound.number.desc()).limit(1).execution_options(populate_existing=True))


def open_round(db: Session, case_id: uuid.UUID) -> ScrutinyRound | None:
    return db.scalar(select(ScrutinyRound).where(ScrutinyRound.case_id == case_id, ScrutinyRound.status == RoundStatus.OPEN.value).execution_options(populate_existing=True))


def _application(db: Session, case: ScrutinyCase) -> Application:
    return db.get(Application, case.application_id)


def case_config(db: Session, case: ScrutinyCase) -> dict:
    return config_of(_application(db, case))


# ---- opening a case -----------------------------------------------------------------------------


def open_case(db: Session, app: Application, actor: User | None, request: Request | None = None) -> ScrutinyCase:
    """A submitted application enters scrutiny. Idempotent: one case per application."""
    existing = db.scalar(select(ScrutinyCase).where(ScrutinyCase.application_id == app.id))
    if existing is not None:
        return existing
    config = config_of(app)
    stages = [s for s in config.get("route", []) if s["stage_key"] == "scrutiny"]
    ok = len(stages) == 1
    case = ScrutinyCase(
        application_id=app.id, rule_version_id=app.rule_version_id,
        state=CaseState.AWAITING_ASSIGNMENT.value if ok else CaseState.CONFIGURATION_EXCEPTION.value,
        exception_code=None if ok else "no_scrutiny_stage",
        exception_detail=None if ok else routing.resolve_next(config).reason,
    )
    db.add(case)
    db.flush()
    if ok:
        db.add(ScrutinyRound(case_id=case.id, number=1, kind=RoundKind.INITIAL.value, status=RoundStatus.OPEN.value))
    log_event(db, case, actor, "case_opened", request=request, object_type="scrutiny_case", object_id=case.id, to_state=case.state, institution_visible=True,
              detail={"rule_version_id": str(app.rule_version_id), "exception": case.exception_code})
    db.flush()
    texts.case_entered_queue(db, case, app)
    return case


# ---- assignment boundary ------------------------------------------------------------------------


@dataclass
class SelectionInfo:
    mode: str
    available: bool
    label: str
    notice: str


def selection_info(config: dict, settings: Settings) -> SelectionInfo:
    """Which mechanism may put a reviewer on this case. Real committee selection has no interface yet, so for anything that is not an
    explicitly DEMO-classified case in a development deployment, no assignment can be made here."""
    if policy.is_demo_case(config) and settings.demo_rules_enabled:
        return SelectionInfo(AssignmentSource.DEMO_MANUAL.value, True, "Demonstration assignment", DEMO_ASSIGNMENT_NOTICE)
    return SelectionInfo(
        AssignmentSource.OFFICIAL_SELECTION.value, False, "Official selection (unavailable)",
        "Reviewers for this case must come from the authorised AICTE selection process. No interface to that process is configured, so no assignment can be recorded here.",
    )


REASSIGNABLE = {
    CaseState.READY_FOR_SCRUTINY, CaseState.UNDER_SCRUTINY, CaseState.AWAITING_NOTICE_DECISION, CaseState.AWAITING_RESPONSE,
    CaseState.AWAITING_RESCRUTINY, CaseState.UNDER_RESCRUTINY,
}


def assign(db: Session, actor: User, case_id: uuid.UUID, assignee_id: uuid.UUID, reason: str | None, row_version: int, request: Request | None, settings: Settings) -> ScrutinyCase:
    case, _ = scrutiny_access.get_case(db, actor, case_id, request, need="coordinator")
    case = lock_case(db, case.id)
    app = _application(db, case)
    info = selection_info(config_of(app), settings)
    if not info.available:
        audit.record(db, "scrutiny.assignment_unavailable", request=request, actor=actor, target_type="scrutiny_case", target_id=case.id)
        db.commit()
        raise ServiceError(409, "selection_unavailable", info.notice)
    assignee = db.get(User, assignee_id)
    if assignee is None or not assignee.is_active or assignee.role != Role.SCRUTINY_OFFICER.value:
        raise invalid("Choose an active scrutiny officer.")
    state = CaseState(case.state)
    if case.row_version != row_version:
        raise conflict("stale_version", "This case was changed by someone else since you opened it. Reload it before assigning.")
    first = state == CaseState.AWAITING_ASSIGNMENT
    if not first and state not in REASSIGNABLE:
        raise conflict("invalid_transition", f"A reviewer cannot be assigned while the case is “{state.value}”.")
    active = list(db.scalars(select(ScrutinyAssignment).where(ScrutinyAssignment.case_id == case.id, ScrutinyAssignment.revoked_at.is_(None))))
    if any(a.user_id == assignee.id for a in active):
        raise conflict("already_assigned", "That reviewer is already assigned to this case.")
    if not first and not (reason and reason.strip()):
        raise invalid("Give a reason for changing the assignment.")
    now = utcnow()
    for a in active:  # a reassignment replaces the previous reviewer; the old record is kept, revoked
        db.execute(update(ScrutinyAssignment).where(ScrutinyAssignment.id == a.id, ScrutinyAssignment.revoked_at.is_(None)).values(revoked_at=now, revoked_by_id=actor.id, revoked_reason=(reason or "Reassigned")[:500]))
    row = ScrutinyAssignment(
        case_id=case.id, user_id=assignee.id, source=info.mode, is_demo=info.mode == AssignmentSource.DEMO_MANUAL.value, made_by_id=actor.id, made_by_label=actor.email,
        selection_ref={"mode": info.mode, "label": info.label, "rule_set": config_of(app)["rule_set"]["key"], "rule_version": config_of(app)["version"]["number"], "classification": config_of(app)["rule_set"]["classification"], "notice": info.notice},
        reason=(reason or "").strip() or None,
    )
    db.add(row)
    db.flush()
    from_state = case.state
    if first:
        transition(db, case, CaseAction.ASSIGN, expected_version=row_version)
    else:
        db.execute(update(ScrutinyCase).where(ScrutinyCase.id == case.id, ScrutinyCase.row_version == case.row_version).values(row_version=case.row_version + 1, updated_at=now))
        db.refresh(case)
    log_event(db, case, actor, "assigned" if first else "reassigned", request=request, object_type="scrutiny_assignment", object_id=row.id, from_state=from_state, to_state=case.state, reason=reason,
              detail={"assignee_id": str(assignee.id), "source": info.mode, "demo": row.is_demo})
    db.flush()
    texts.case_assigned(db, case, app, assignee.id, row.is_demo, row.id)
    return case


def determine_handoff(db: Session, actor: User, case_id: uuid.UUID, stage_key: str | None, reason: str, request: Request | None) -> StageHandoff:
    """A person chooses among the next stages the *frozen* route allows (or records that a conditional stage does not apply)."""
    case, _ = scrutiny_access.get_case(db, actor, case_id, request, need="coordinator")
    case = lock_case(db, case.id)
    handoff = db.scalar(select(StageHandoff).where(StageHandoff.case_id == case.id).execution_options(populate_existing=True))
    if handoff is None or handoff.status != HandoffStatus.AWAITING_DETERMINATION.value:
        raise conflict("not_awaiting_determination", "There is no pending next-stage determination for this case.")
    if not reason or len(reason.strip()) < 5:
        raise invalid("Record the reason for this determination.")
    options = {o["stage_key"]: o for o in (handoff.route_basis or {}).get("options", [])}
    if stage_key is None:
        if not (len(options) == 1 and next(iter(options.values()))["is_conditional"]):
            raise invalid("Choose one of the stages the approved route offers.")
        status, label = HandoffStatus.NO_FURTHER_STAGE, None
    elif stage_key not in options:
        raise invalid("That stage is not one of the next stages in this application's approved route.")
    else:
        status, label = HandoffStatus.QUEUED, options[stage_key]["label"]
    now = utcnow()
    res = db.execute(update(StageHandoff).where(StageHandoff.id == handoff.id, StageHandoff.status == HandoffStatus.AWAITING_DETERMINATION.value).values(
        status=status.value, to_stage_key=stage_key, to_stage_label=label, determined_by_id=actor.id, determined_at=now, determination_reason=reason.strip(), updated_at=now))
    if res.rowcount != 1:
        raise conflict("stale_version", "Another determination was recorded at the same moment.")
    db.refresh(handoff)
    log_event(db, case, actor, "handoff_determined", request=request, object_type="stage_handoff", object_id=handoff.id, reason=reason,
              detail={"to_stage": stage_key, "status": status.value})
    texts.handed_off(db, case, _application(db, case), handoff)
    return handoff


# ---- starting work and notes --------------------------------------------------------------------


def _require_reviewer(db: Session, user: User, case_id: uuid.UUID, request: Request | None) -> ScrutinyCase:
    case, _ = scrutiny_access.get_case(db, user, case_id, request, need="reviewer")
    return lock_case(db, case.id)


def start(db: Session, user: User, case_id: uuid.UUID, row_version: int, request: Request | None) -> ScrutinyCase:
    case = _require_reviewer(db, user, case_id, request)
    from_state = case.state
    transition(db, case, CaseAction.START, expected_version=row_version)
    rnd = open_round(db, case.id)
    if rnd is not None and rnd.started_at is None:
        db.execute(update(ScrutinyRound).where(ScrutinyRound.id == rnd.id).values(started_at=utcnow()))
    log_event(db, case, user, "round_started", request=request, object_type="scrutiny_round", object_id=rnd.id if rnd else None, from_state=from_state, to_state=case.state,
              detail={"round": rnd.number if rnd else None, "kind": rnd.kind if rnd else None})
    return case


def _require_working(case: ScrutinyCase) -> None:
    if case.state not in (CaseState.UNDER_SCRUTINY.value, CaseState.UNDER_RESCRUTINY.value):
        raise conflict("invalid_transition", f"That action needs the case to be under scrutiny (it is “{case.state}”).")


def save_notes(db: Session, user: User, case_id: uuid.UUID, notes: str, row_version: int, request: Request | None) -> ScrutinyRound:
    case = _require_reviewer(db, user, case_id, request)
    _require_working(case)
    rnd = open_round(db, case.id)
    now = utcnow()
    res = db.execute(update(ScrutinyRound).where(ScrutinyRound.id == rnd.id, ScrutinyRound.row_version == row_version, ScrutinyRound.status == RoundStatus.OPEN.value).values(
        review_notes=notes, review_notes_by_id=user.id, review_notes_at=now, row_version=row_version + 1))
    if res.rowcount != 1:
        raise conflict("stale_version", "These notes were changed by someone else since you opened them. Reload to see their version before saving yours.")
    db.refresh(rnd)
    log_event(db, case, user, "notes_saved", request=request, object_type="scrutiny_round", object_id=rnd.id, detail={"round": rnd.number})
    return rnd


# ---- source references: chosen from the frozen configuration, never typed in --------------------


def source_options(config: dict) -> list[dict]:
    sources = {s["id"]: s for s in config.get("sources", [])}
    out: list[dict] = []
    for s in config.get("sources", []):
        out.append({"key": f"source:{s['id']}", "label": s["title"], "clause_ref": s.get("clause_ref"), "verification_status": s["verification_status"], "origin": "source"})
    for i in config.get("checklist", []):
        if i.get("clause_ref"):
            src = sources.get(i.get("source_id"))
            out.append({"key": f"item:{i['item_key']}", "label": f"{i['label']}", "clause_ref": i["clause_ref"], "verification_status": src["verification_status"] if src else None, "origin": "checklist"})
    return out


def build_source_ref(config: dict, key: str | None) -> tuple[dict | None, str]:
    if not key:
        return None, SourceRefStatus.NEEDS_VERIFICATION.value
    opt = next((o for o in source_options(config) if o["key"] == key), None)
    if opt is None:
        raise invalid("That source reference is not part of this application's approved rule version.")
    return {"key": opt["key"], "label": opt["label"], "clause_ref": opt["clause_ref"], "verification_status": opt["verification_status"]}, SourceRefStatus.ESTABLISHED.value


# ---- findings -----------------------------------------------------------------------------------


def _validate_finding(db: Session, case: ScrutinyCase, app: Application, config: dict, d: dict) -> dict:
    """Normalise and validate the content of a finding. Returns column values."""
    issues: list[Issue] = []
    if d["issue_type"] not in ISSUE_TYPES:
        issues.append(Issue("issue_type", "Choose an issue type.", "issue_type"))
    description = (d.get("description") or "").strip()
    if len(description) < 5:
        issues.append(Issue("description", "Describe the issue (at least a few words).", "description"))
    item_key, doc_id, page = d.get("item_key") or None, d.get("document_id"), d.get("page_number")
    doc: ApplicationDocument | None = None
    if doc_id is not None:
        doc = db.get(ApplicationDocument, doc_id)
        if doc is None or doc.application_id != app.id or doc.removed_at is not None:
            issues.append(Issue("document", "That document version does not belong to this application.", "document_id"))
        else:
            if item_key and item_key != doc.item_key:
                issues.append(Issue("item_key", "The document belongs to a different requirement.", "item_key"))
            item_key = doc.item_key
            known = doc.processing.page_count if doc.processing is not None else None
            if page is not None and known is not None and page > known:
                issues.append(Issue("page_number", f"The document has {known} page(s).", "page_number"))
    elif page is not None:
        issues.append(Issue("page_number", "A page needs a document version.", "page_number"))
    if item_key and not any(i["item_key"] == item_key for i in config["checklist"]):
        issues.append(Issue("item_key", "That requirement is not part of this application's checklist.", "item_key"))
    if page is not None and page < 1:
        issues.append(Issue("page_number", "Pages start at 1.", "page_number"))
    prelim_id = d.get("preliminary_finding_id")
    if prelim_id is not None:
        pf = db.get(PreliminaryFinding, prelim_id)
        if pf is None or pf.application_id != app.id:
            issues.append(Issue("preliminary_finding_id", "That automated issue does not belong to this application.", "preliminary_finding_id"))
    visibility = d.get("visibility") or Visibility.INTERNAL_ONLY.value
    inst_text = (d.get("institution_text") or "").strip() or None
    correction = (d.get("requested_correction") or "").strip() or None
    if visibility == Visibility.ELIGIBLE_FOR_NOTICE.value and not (inst_text and correction):
        issues.append(Issue("institution_text", "A finding that may go into a notice needs institution-facing wording and the correction requested.", "institution_text"))
    try:
        source_ref, source_status = build_source_ref(config, d.get("source_ref_key"))
    except ServiceError as exc:
        issues.append(Issue("source_ref_key", exc.message, "source_ref_key"))
        source_ref, source_status = None, SourceRefStatus.NEEDS_VERIFICATION.value
    if issues:
        raise invalid("The finding was not saved.", issues)
    return dict(
        item_key=item_key, document_id=doc_id, page_number=page, issue_type=d["issue_type"], description=description, institution_text=inst_text, requested_correction=correction,
        source_ref=source_ref, source_ref_status=source_status, preliminary_finding_id=prelim_id, visibility=visibility,
    )


def create_finding(db: Session, user: User, case_id: uuid.UUID, data: dict, request: Request | None) -> ScrutinyFinding:
    case = _require_reviewer(db, user, case_id, request)
    _require_working(case)
    if case.state == CaseState.UNDER_RESCRUTINY.value:
        raise conflict("rescrutiny_scope", "Re-scrutiny verifies only the deficiencies already pointed out (Approval Process Handbook 2024-27, Ch. I cl. 1.8(g)), so a new finding cannot be recorded in this round. "
                                            "Conclude the existing findings; anything else is for the authorised process to raise.")
    app = _application(db, case)
    values = _validate_finding(db, case, app, config_of(app), data)
    rnd = open_round(db, case.id)
    seq = (db.scalar(select(func.max(ScrutinyFinding.sequence)).where(ScrutinyFinding.case_id == case.id)) or 0) + 1
    f = ScrutinyFinding(case_id=case.id, application_id=app.id, round_id=rnd.id, sequence=seq, created_by_id=user.id, state=FindingState.OPEN.value, **values)
    db.add(f)
    db.flush()
    db.add(FindingEvent(finding_id=f.id, round_id=rnd.id, actor_id=user.id, action="recorded", to_state=f.state))
    log_event(db, case, user, "finding_recorded", request=request, object_type="scrutiny_finding", object_id=f.id, detail={"sequence": seq, "issue_type": f.issue_type, "visibility": f.visibility, "round": rnd.number,
              "document_id": str(f.document_id) if f.document_id else None, "from_automated_issue": bool(f.preliminary_finding_id)})
    db.flush()
    return f


def get_finding(db: Session, case: ScrutinyCase, finding_id: uuid.UUID) -> ScrutinyFinding:
    f = db.get(ScrutinyFinding, finding_id, populate_existing=True)
    if f is None or f.case_id != case.id:
        raise not_found("Finding")
    return f


def _in_open_notice(db: Session, finding_id: uuid.UUID) -> bool:
    return db.scalar(select(NoticeItem.id).join(DeficiencyNotice, DeficiencyNotice.id == NoticeItem.notice_id).where(NoticeItem.finding_id == finding_id, DeficiencyNotice.status.in_(NOTICE_PREPARING)).limit(1)) is not None


def update_finding(db: Session, user: User, case_id: uuid.UUID, finding_id: uuid.UUID, data: dict, row_version: int, request: Request | None) -> ScrutinyFinding:
    case = _require_reviewer(db, user, case_id, request)
    _require_working(case)
    f = get_finding(db, case, finding_id)
    if f.state != FindingState.OPEN.value:
        raise conflict("finding_not_editable", "A finding that has been noticed or concluded can no longer be edited.")
    if _in_open_notice(db, f.id):
        raise conflict("finding_in_notice_draft", "This finding is part of a notice that is being prepared. Withdraw that notice before editing it.")
    app = _application(db, case)
    merged = {
        "issue_type": f.issue_type, "description": f.description, "item_key": f.item_key, "document_id": f.document_id, "page_number": f.page_number, "institution_text": f.institution_text,
        "requested_correction": f.requested_correction, "visibility": f.visibility, "preliminary_finding_id": f.preliminary_finding_id,
        "source_ref_key": (f.source_ref or {}).get("key"), **data,
    }
    values = _validate_finding(db, case, app, config_of(app), merged)
    res = db.execute(update(ScrutinyFinding).where(ScrutinyFinding.id == f.id, ScrutinyFinding.row_version == row_version, ScrutinyFinding.state == FindingState.OPEN.value).values(
        **values, row_version=row_version + 1, updated_at=utcnow()))
    if res.rowcount != 1:
        raise conflict("stale_version", "This finding was changed by someone else since you opened it. Reload it to see their change before saving yours.")
    rnd = open_round(db, case.id)
    db.add(FindingEvent(finding_id=f.id, round_id=rnd.id, actor_id=user.id, action="edited", from_state=f.state, to_state=f.state))
    log_event(db, case, user, "finding_edited", request=request, object_type="scrutiny_finding", object_id=f.id, detail={"sequence": f.sequence})
    db.refresh(f)
    return f


def disposition(db: Session, user: User, case_id: uuid.UUID, finding_id: uuid.UUID, to: FindingState, reason: str, row_version: int, request: Request | None) -> ScrutinyFinding:
    """Resolved / not supported / (re-scrutiny) unresolved, always with a reason."""
    case = _require_reviewer(db, user, case_id, request)
    _require_working(case)
    f = get_finding(db, case, finding_id)
    current = FindingState(f.state)
    if to == FindingState.NOTICED or to not in FINDING_TRANSITIONS[current]:
        raise conflict("invalid_transition", f"A finding that is “{current.value}” cannot become “{to.value}” this way.")
    if not reason or len(reason.strip()) < 5:
        raise invalid("Record the reason for this conclusion.")
    if _in_open_notice(db, f.id):
        raise conflict("finding_in_notice_draft", "This finding is part of a notice that is being prepared. Withdraw that notice first.")
    res = db.execute(update(ScrutinyFinding).where(ScrutinyFinding.id == f.id, ScrutinyFinding.row_version == row_version, ScrutinyFinding.state == current.value).values(
        state=to.value, state_reason=reason.strip(), row_version=row_version + 1, updated_at=utcnow()))
    if res.rowcount != 1:
        raise conflict("stale_version", "This finding was changed by someone else since you opened it. Reload it before concluding.")
    rnd = open_round(db, case.id)
    db.add(FindingEvent(finding_id=f.id, round_id=rnd.id, actor_id=user.id, action=to.value.lower(), from_state=current.value, to_state=to.value, reason=reason.strip()))
    log_event(db, case, user, f"finding_{to.value.lower()}", request=request, object_type="scrutiny_finding", object_id=f.id, from_state=current.value, to_state=to.value, reason=reason,
              detail={"sequence": f.sequence, "round": rnd.number})
    db.flush()
    db.refresh(f)
    close_notices_if_evaluated(db, case, user, request)
    return f


def close_notices_if_evaluated(db: Session, case: ScrutinyCase, actor: User, request: Request | None) -> None:
    """A notice whose response has been received is closed once every finding it carried has been concluded."""
    for n in db.scalars(select(DeficiencyNotice).where(DeficiencyNotice.case_id == case.id, DeficiencyNotice.status == NoticeStatus.RESPONSE_RECEIVED.value)):
        states = db.scalars(select(ScrutinyFinding.state).join(NoticeItem, NoticeItem.finding_id == ScrutinyFinding.id).where(NoticeItem.notice_id == n.id)).all()
        if states and not any(FindingState(s) in FINDING_PENDING_STATES for s in states):
            now = utcnow()
            res = db.execute(update(DeficiencyNotice).where(DeficiencyNotice.id == n.id, DeficiencyNotice.status == NoticeStatus.RESPONSE_RECEIVED.value).values(
                status=NoticeStatus.CLOSED.value, closed_at=now, row_version=n.row_version + 1, updated_at=now))
            if res.rowcount == 1:
                log_event(db, case, actor, "notice_closed", request=request, object_type="deficiency_notice", object_id=n.id, from_state=NoticeStatus.RESPONSE_RECEIVED.value, to_state=NoticeStatus.CLOSED.value,
                          institution_visible=True, detail={"reference": n.reference})


# ---- completion and hand-off --------------------------------------------------------------------


def _unfinished_processing(db: Session, app: Application) -> list[str]:
    return [d.item_key for d in current_documents(db, app.id).values() if d.processing is not None and d.processing.extraction_status in (ExtractionStatus.QUEUED.value, ExtractionStatus.RUNNING.value)]


def complete(db: Session, user: User, case_id: uuid.UUID, row_version: int, summary: str, acknowledge_unresolved: bool, request: Request | None) -> ScrutinyCase:
    """Record that this scrutiny / re-scrutiny stage is finished. NOT an approval. The next stage follows from the frozen route."""
    case = _require_reviewer(db, user, case_id, request)
    _require_working(case)
    if case.row_version != row_version:
        raise conflict("stale_version", "This case was changed by someone else since you opened it. Reload it before completing.")
    if not summary or len(summary.strip()) < 5:
        raise invalid("Record a short summary of the scrutiny outcome.")
    app = _application(db, case)
    findings = list(db.scalars(select(ScrutinyFinding).where(ScrutinyFinding.case_id == case.id).order_by(ScrutinyFinding.sequence)))
    pending = [f for f in findings if FindingState(f.state) in FINDING_PENDING_STATES]
    if pending:
        raise conflict("findings_pending", f"{len(pending)} finding(s) are still open or awaiting a response. Conclude each one (resolved, unresolved or not supported) or issue a notice first.",
                       [Issue(f.state, f"Finding {f.sequence} is {f.state.lower()}.", str(f.id)) for f in pending])
    if db.scalar(select(DeficiencyNotice.id).where(DeficiencyNotice.case_id == case.id, DeficiencyNotice.status.in_(NOTICE_PREPARING)).limit(1)) is not None:
        raise conflict("notice_draft_open", "A notice is still being prepared. Withdraw or issue it before completing scrutiny.")
    busy = _unfinished_processing(db, app)
    if busy:
        raise conflict("documents_still_processing", "Some documents are still being processed, so their extracted values are not final yet. Complete scrutiny once processing has finished.", [Issue("processing", k, k) for k in busy])
    unresolved = [f for f in findings if f.state == FindingState.UNRESOLVED.value]
    if unresolved and not acknowledge_unresolved:
        raise invalid("Some findings remain unresolved. Confirm that you are completing scrutiny with them recorded as unresolved.", [Issue("unresolved", f"Finding {f.sequence} is unresolved.", str(f.id)) for f in unresolved], code="unresolved_findings_need_acknowledgement")

    config = config_of(app)
    resolution = routing.resolve_next(config)
    rnd = open_round(db, case.id)
    now = utcnow()
    completion = {
        "summary": summary.strip(), "completed_round": rnd.number, "outcome": "COMPLETED_WITH_UNRESOLVED_FINDINGS" if unresolved else "COMPLETED_NO_OPEN_FINDINGS",
        "findings": [{"id": str(f.id), "sequence": f.sequence, "state": f.state, "reason": f.state_reason, "document_id": str(f.document_id) if f.document_id else None} for f in findings],
        "counts": {s.value: sum(1 for f in findings if f.state == s.value) for s in FindingState},
        "documents": [{"item_key": k, "document_id": str(d.id), "version": d.version_number} for k, d in sorted(current_documents(db, app.id).items())],
        "rule_version": {"id": str(case.rule_version_id), "rule_set": config["rule_set"]["key"], "number": config["version"]["number"], "classification": config["rule_set"]["classification"]},
        "approved": False,
    }
    res = db.execute(update(ScrutinyRound).where(ScrutinyRound.id == rnd.id, ScrutinyRound.status == RoundStatus.OPEN.value).values(
        status=RoundStatus.COMPLETED.value, closed_at=now, outcome=completion["outcome"], outcome_reason=summary.strip(), row_version=rnd.row_version + 1))
    if res.rowcount != 1:
        raise conflict("stale_version", "This round was closed at the same moment by another request.")
    from_state = case.state
    if resolution.status == HandoffStatus.EXCEPTION:
        transition(db, case, CaseAction.FLAG_EXCEPTION, expected_version=row_version, completed_at=now, completed_by_id=user.id, completion=completion,
                   exception_code="route_unresolved", exception_detail=resolution.reason)
    else:
        transition(db, case, CaseAction.COMPLETE, expected_version=row_version, completed_at=now, completed_by_id=user.id, completion=completion)
    handoff = StageHandoff(
        case_id=case.id, application_id=app.id, from_stage_key=resolution.from_stage_key, to_stage_key=resolution.to_stage_key, to_stage_label=resolution.to_stage_label,
        status=resolution.status.value, route_basis={**resolution.basis, "options": resolution.options}, reason=resolution.reason,
    )
    db.add(handoff)
    log_event(db, case, user, "scrutiny_completed", request=request, object_type="scrutiny_round", object_id=rnd.id, from_state=from_state, to_state=case.state, reason=summary,
              institution_visible=True, detail={"outcome": completion["outcome"], "round": rnd.number, "approved": False})
    log_event(db, case, user, "handoff_recorded", request=request, object_type="stage_handoff", object_id=handoff.id, detail={"status": handoff.status, "to_stage": handoff.to_stage_key})
    db.add(ApplicationEvent(application_id=app.id, actor_id=user.id, action="scrutiny_completed", detail={"approved": False, "outcome": completion["outcome"]}))
    db.flush()
    texts.scrutiny_completed(db, case, app)
    texts.handed_off(db, case, app, handoff)
    return case
