"""The SHC compliance / hearing process and its recommendation.

SHC is not EVC and not SAC. Per the handbook (Ch. II cl. 2.23.3) institutions get a chance to submit compliance for shortcomings found in scrutiny, re-scrutiny
or the EVC, before the Standing Hearing Committee; the committee's recommendation is placed before the Executive Committee. This module records that process:

  coordinator   prepares the requirements the institution is asked to address, requests compliance, coordinates hearings, completes the stage
  institution   answers each requirement with an explanation and/or corrected evidence (new document versions); the response is RECEIVED, never "accepted"
  member        (assigned) assesses the evidence per requirement, records proceedings, prepares and submits a versioned recommendation

A recommendation is a record for the later decision process. It is not a decision, and nothing here approves or rejects an application. A submitted recommendation
is immutable; a correction is a new version opened by an authorised coordinator with a reason.
What the policy says (whether compliance is permitted, whether a hearing is part of the process, the recommendation options) is configuration, not code.
"""

import hashlib
import json
import uuid
from contextlib import nullcontext

from fastapi import Request, UploadFile
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app import audit, committee_access, jobs
from app.config import Settings
from app.domain import CAction, CommitteeStage, ComplianceRoundStatus, CState, ReportKind, ReportStatus, ResponseStatus, ShcAssessment
from app.evidence import current_documents
from app.models import User, utcnow
from app.models_applications import Application, ApplicationDocument, ApplicationEvent
from app.models_committee import (
    CommitteeAssignment,
    CommitteeCase,
    CommitteeReport,
    ShcFinding,
    ShcProceeding,
    ShcRequirement,
    ShcResponse,
    ShcResponseDocument,
    ShcResponseItem,
    ShcRound,
)
from app.services import committee_core as core
from app.services import committee_policy as cpolicy
from app.services import committee_texts as ctexts
from app.services import scheduling, stages
from app.services import scrutiny as sc_svc
from app.services.applications import config_of
from app.services.documents import upload_version
from app.services.errors import Issue, ServiceError, conflict, invalid, not_found
from app.storage import LocalStorage

STAGE = CommitteeStage.SHC
MAX_TEXT = 8000
ASSESSMENTS = {a.value for a in ShcAssessment}


def _member_case(db: Session, user: User, case_id: uuid.UUID, request: Request | None) -> CommitteeCase:
    case, _ = committee_access.get_case(db, user, case_id, request, stage=STAGE, need="member")
    return core.lock_case(db, case.id)


def _coordinator_case(db: Session, user: User, case_id: uuid.UUID, request: Request | None) -> CommitteeCase:
    case, _ = committee_access.get_case(db, user, case_id, request, stage=STAGE, need="coordinator")
    return core.lock_case(db, case.id)


def shc_policy(case: CommitteeCase) -> dict:
    return core.policy(case).get("content", {}).get("shc", {})


def prepared_requirements(db: Session, case_id: uuid.UUID) -> list[ShcRequirement]:
    return list(db.scalars(select(ShcRequirement).where(ShcRequirement.case_id == case_id, ShcRequirement.round_id.is_(None), ShcRequirement.removed_at.is_(None)).order_by(ShcRequirement.created_at).execution_options(populate_existing=True)))


def rounds(db: Session, case_id: uuid.UUID) -> list[ShcRound]:
    return list(db.scalars(select(ShcRound).where(ShcRound.case_id == case_id).order_by(ShcRound.number).execution_options(populate_existing=True)))


def round_requirements(db: Session, round_id: uuid.UUID) -> list[ShcRequirement]:
    return list(db.scalars(select(ShcRequirement).where(ShcRequirement.round_id == round_id, ShcRequirement.removed_at.is_(None)).order_by(ShcRequirement.position)))


def findings(db: Session, case_id: uuid.UUID) -> list[ShcFinding]:
    return list(db.scalars(select(ShcFinding).where(ShcFinding.case_id == case_id).order_by(ShcFinding.sequence).execution_options(populate_existing=True)))


def proceedings(db: Session, case_id: uuid.UUID) -> list[ShcProceeding]:
    return list(db.scalars(select(ShcProceeding).where(ShcProceeding.case_id == case_id).order_by(ShcProceeding.recorded_at)))


def draft_recommendation(db: Session, case_id: uuid.UUID) -> CommitteeReport | None:
    return db.scalar(select(CommitteeReport).where(CommitteeReport.case_id == case_id, CommitteeReport.kind == ReportKind.SHC_RECOMMENDATION.value, CommitteeReport.status == ReportStatus.DRAFT.value).execution_options(populate_existing=True))


def submitted_recommendations(db: Session, case_id: uuid.UUID) -> list[CommitteeReport]:
    return list(db.scalars(select(CommitteeReport).where(CommitteeReport.case_id == case_id, CommitteeReport.kind == ReportKind.SHC_RECOMMENDATION.value, CommitteeReport.status == ReportStatus.SUBMITTED.value).order_by(CommitteeReport.version_number)))


# ---- coordinator: preparing the requirements --------------------------------------------------------------


def _requirement_values(db: Session, case: CommitteeCase, data: dict) -> dict:
    config = core.case_config(db, case)
    issues: list[Issue] = []
    title = (data.get("title") or "").strip()
    description = (data.get("description") or "").strip()
    if len(title) < 3:
        issues.append(Issue("title", "Give the requirement a short title.", "title"))
    if len(description) < 5:
        issues.append(Issue("description", "Describe what the institution is asked to address, in your own words for the institution.", "description"))
    item_key = data.get("item_key") or None
    if item_key is not None:
        item = next((c for c in config.get("checklist", []) if c["item_key"] == item_key), None)
        if item is None or item["item_type"] != "document_upload":
            issues.append(Issue("item_key", "Corrected files can be filed only under one of this application's document requirements.", "item_key"))
    if issues:
        raise invalid("The requirement was not saved.", issues)
    source_ref, ref_status = sc_svc.build_source_ref(config, data.get("source_ref_key"))
    return dict(title=title[:300], description=description, requested_evidence=(data.get("requested_evidence") or "").strip(), item_key=item_key, source_ref=source_ref, source_ref_status=ref_status)


def _prep_states(case: CommitteeCase) -> None:
    core.require_state(case, CState.PREPARATION, CState.UNDER_REVIEW)


def add_requirement(db: Session, user: User, case_id: uuid.UUID, data: dict, request: Request | None) -> ShcRequirement:
    case = _coordinator_case(db, user, case_id, request)
    _prep_states(case)
    values = _requirement_values(db, case, data)
    origin = data.get("origin") or {"type": "manual"}
    if origin.get("type") not in ("manual", "scrutiny_finding", "evc_observation"):
        raise invalid("Unknown origin.")
    if origin["type"] != "manual":
        known = {(s["origin"], s["id"]) for s in case.trigger.get("shortcomings", [])}
        if (origin["type"], str(origin.get("id"))) not in known:
            raise invalid("That is not one of the shortcomings recorded when this stage was activated.")
    req = ShcRequirement(case_id=case.id, origin=origin, created_by_id=user.id, **values)
    db.add(req)
    db.flush()
    core.log_event(db, case, user, "requirement_added", request=request, object_type="shc_requirement", object_id=req.id, detail={"origin": origin.get("type"), "item_key": req.item_key})
    return req


def update_requirement(db: Session, user: User, case_id: uuid.UUID, req_id: uuid.UUID, data: dict, row_version: int, request: Request | None) -> ShcRequirement:
    case = _coordinator_case(db, user, case_id, request)
    _prep_states(case)
    req = db.get(ShcRequirement, req_id, populate_existing=True)
    if req is None or req.case_id != case.id or req.removed_at is not None:
        raise not_found("Requirement")
    if req.round_id is not None:
        raise conflict("requirement_issued", "A requirement already put to the institution cannot be changed.")
    merged = {"title": req.title, "description": req.description, "requested_evidence": req.requested_evidence, "item_key": req.item_key, "source_ref_key": (req.source_ref or {}).get("key"), **data}
    values = _requirement_values(db, case, merged)
    res = db.execute(update(ShcRequirement).where(ShcRequirement.id == req.id, ShcRequirement.row_version == row_version, ShcRequirement.round_id.is_(None)).values(**values, row_version=row_version + 1))
    if res.rowcount != 1:
        raise conflict("stale_version", "This requirement was changed by someone else. Reload it before saving.")
    db.refresh(req)
    core.log_event(db, case, user, "requirement_updated", request=request, object_type="shc_requirement", object_id=req.id)
    return req


def remove_requirement(db: Session, user: User, case_id: uuid.UUID, req_id: uuid.UUID, request: Request | None) -> None:
    case = _coordinator_case(db, user, case_id, request)
    _prep_states(case)
    req = db.get(ShcRequirement, req_id)
    if req is None or req.case_id != case.id or req.removed_at is not None:
        raise not_found("Requirement")
    if req.round_id is not None:
        raise conflict("requirement_issued", "A requirement already put to the institution cannot be removed.")
    req.removed_at = utcnow()
    core.log_event(db, case, user, "requirement_removed", request=request, object_type="shc_requirement", object_id=req.id)


def request_compliance(db: Session, user: User, case_id: uuid.UUID, instructions: str, row_version: int, request: Request | None) -> ShcRound:
    """Put the prepared requirements to the institution (round 1, or a further round from review). The requirements freeze."""
    case = _coordinator_case(db, user, case_id, request)
    core.require_version(case, row_version)
    core.require_state(case, CState.PREPARATION, CState.UNDER_REVIEW)
    core.require_no_blockers(case, "no_committee_policy", "compliance_rule_unverified")
    if shc_policy(case).get("compliance_submission") != "PERMITTED":
        raise conflict("compliance_not_permitted", "The configuration does not permit the institution to submit compliance in this process.")
    prepared = prepared_requirements(db, case.id)
    if not prepared:
        raise conflict("no_requirements", "Add at least one requirement before asking the institution for compliance.")
    if not (instructions or "").strip():
        raise invalid("Write the instructions the institution will see.", [Issue("instructions", "Instructions are needed.", "instructions")])
    last = db.scalar(select(func.max(ShcRound.number)).where(ShcRound.case_id == case.id)) or 0
    period = cpolicy.response_period(case.policy_snapshot, core.case_config(db, case))
    rnd = ShcRound(case_id=case.id, number=last + 1, instructions=instructions.strip(), requested_by_id=user.id, policy_resolution=period, response_due_at=None)
    db.add(rnd)
    db.flush()
    for pos, r in enumerate(prepared, start=1):
        db.execute(update(ShcRequirement).where(ShcRequirement.id == r.id, ShcRequirement.round_id.is_(None)).values(round_id=rnd.id, position=pos))
    from_state = case.state
    core.transition(db, case, CAction.REQUEST_COMPLIANCE, expected_version=row_version)
    core.log_event(db, case, user, "compliance_requested", request=request, object_type="shc_round", object_id=rnd.id, from_state=from_state, to_state=case.state, institution_visible=True,
                   detail={"round": rnd.number, "requirements": len(prepared), "response_period": period["state"]})
    db.flush()
    ctexts.compliance_requested(db, case, core.application(db, case), rnd.id, rnd.number, None)
    return rnd


# ---- the institution --------------------------------------------------------------------------------------


def institution_round(db: Session, user: User, round_id: uuid.UUID, request: Request | None) -> tuple[ShcRound, CommitteeCase, Application]:
    """A round put to the user's own institution. Anything else, including a round still being prepared: "not found"."""
    rnd = db.get(ShcRound, round_id, populate_existing=True)
    case = db.get(CommitteeCase, rnd.case_id) if rnd else None
    app = db.get(Application, case.application_id) if case else None
    if rnd is None or app is None or user.institution_id is None or app.institution_id != user.institution_id:
        if rnd is not None:
            audit.record(db, "compliance.access_denied", request=request, actor=user, target_type="shc_round", target_id=rnd.id)
            db.commit()
        raise not_found("Compliance request")
    return rnd, case, app


def response_of(db: Session, round_id: uuid.UUID) -> ShcResponse | None:
    return db.scalars(
        select(ShcResponse).where(ShcResponse.round_id == round_id).options(selectinload(ShcResponse.items).selectinload(ShcResponseItem.documents)).execution_options(populate_existing=True)
    ).one_or_none()


def _require_open(rnd: ShcRound, case: CommitteeCase) -> None:
    """The SHC process permits a response only while this round is open and the case is waiting for compliance."""
    if rnd.status != ComplianceRoundStatus.OPEN.value or case.state != CState.AWAITING_COMPLIANCE.value:
        raise conflict("compliance_not_open", "This compliance request is not open for a response.")


def _get_or_create(db: Session, rnd: ShcRound, case: CommitteeCase, app: Application, user: User) -> ShcResponse:
    r = response_of(db, rnd.id)
    if r is not None:
        return r
    try:
        with db.begin_nested() if db.bind.dialect.name == "postgresql" else nullcontext():
            db.add(ShcResponse(round_id=rnd.id, case_id=case.id, application_id=app.id, institution_id=app.institution_id, created_by_id=user.id))
            db.flush()
    except IntegrityError:
        if db.bind.dialect.name != "postgresql":
            raise
    r = response_of(db, rnd.id)
    if r is None:
        raise ServiceError(500, "response_unavailable", "The response could not be started. Please try again.")
    return r


def _item_row(db: Session, r: ShcResponse, requirement_id: uuid.UUID) -> ShcResponseItem:
    row = next((i for i in r.items if i.requirement_id == requirement_id), None)
    if row is None:
        row = ShcResponseItem(response_id=r.id, requirement_id=requirement_id)
        db.add(row)
        db.flush()
        db.refresh(r)
    return row


def save_response(db: Session, user: User, round_id: uuid.UUID, explanation: str | None, items: list[dict], row_version: int | None, request: Request | None) -> ShcResponse:
    rnd, case, app = institution_round(db, user, round_id, request)
    _require_open(rnd, case)
    existing = response_of(db, rnd.id)
    if existing is not None and existing.status == ResponseStatus.SUBMITTED.value:
        raise conflict("response_already_submitted", "The response to this request has already been submitted.")
    valid = {r.id for r in round_requirements(db, rnd.id)}
    issues = []
    for it in items:
        if it["requirement_id"] not in valid:
            issues.append(Issue("unknown_item", "That requirement does not belong to this request.", str(it["requirement_id"])))
        if len(it.get("explanation") or "") > MAX_TEXT:
            issues.append(Issue("too_long", "That explanation is too long.", str(it["requirement_id"])))
    if explanation is not None and len(explanation) > MAX_TEXT:
        issues.append(Issue("too_long", "The explanation is too long.", "explanation"))
    if issues:
        raise invalid("The response was not saved.", issues)
    if existing is not None and row_version != existing.row_version:
        raise conflict("stale_version", "This response was changed elsewhere since you opened it. Reload it before saving.")
    r = existing or _get_or_create(db, rnd, case, app, user)
    res = db.execute(update(ShcResponse).where(ShcResponse.id == r.id, ShcResponse.status == ResponseStatus.DRAFT.value, ShcResponse.row_version == r.row_version).values(
        explanation=(explanation.strip() if explanation is not None else r.explanation), row_version=r.row_version + 1, updated_at=utcnow()))
    if res.rowcount != 1:
        raise conflict("stale_version", "This response was changed at the same moment. Reload it before saving.")
    now = utcnow()
    for it in items:
        row = _item_row(db, r, it["requirement_id"])
        db.execute(update(ShcResponseItem).where(ShcResponseItem.id == row.id).values(explanation=(it.get("explanation") or "").strip(), updated_at=now))
    audit.record(db, "compliance.response_saved", request=request, actor=user, target_type="shc_round", target_id=rnd.id, detail={"case_id": str(case.id), "items": len(items)})
    db.flush()
    return response_of(db, rnd.id)


def upload_compliance(db: Session, user: User, round_id: uuid.UUID, requirement_id: uuid.UUID, upload: UploadFile, request: Request | None, settings: Settings, storage: LocalStorage) -> tuple[ShcResponse, str]:
    """Compliance evidence for one requirement: a new version of the requirement's document, through the same checks and processing as any upload.
    The original and every earlier version stay; nothing marks the new file accepted."""
    rnd, case, app = institution_round(db, user, round_id, request)
    _require_open(rnd, case)
    req = next((r for r in round_requirements(db, rnd.id) if r.id == requirement_id), None)
    if req is None:
        raise not_found("Requirement")
    requirement = next((c for c in config_of(app)["checklist"] if c["item_key"] == req.item_key), None) if req.item_key else None
    if requirement is None or requirement["item_type"] != "document_upload":
        raise invalid("This requirement does not concern a document requirement, so there is nothing to upload against it. Use the explanation field.")
    existing = response_of(db, rnd.id)
    if existing is not None and existing.status == ResponseStatus.SUBMITTED.value:
        raise conflict("response_already_submitted", "The response to this request has already been submitted.")

    def link(doc: ApplicationDocument) -> None:
        r = _get_or_create(db, rnd, case, app, user)
        row = _item_row(db, r, req.id)
        if not any(d.document_id == doc.id for d in row.documents):
            db.add(ShcResponseDocument(response_item_id=row.id, document_id=doc.id, linked_by_id=user.id))
        db.add(ApplicationEvent(application_id=app.id, actor_id=user.id, action="compliance_evidence_uploaded", detail={"round": rnd.number, "item_key": req.item_key, "document_id": str(doc.id), "version": doc.version_number}))

    _, outcome = upload_version(db, user, app.id, req.item_key, upload, request, settings, storage, correction=True, on_stored=link)
    return response_of(db, rnd.id), outcome


def submit_response(db: Session, user: User, round_id: uuid.UUID, idempotency_key: str, row_version: int, request: Request | None, settings: Settings) -> tuple[ShcResponse, bool]:
    """-> (response, replay). Records receipt and moves the case to COMPLIANCE_RECEIVED; assesses nothing."""
    rnd, case, app = institution_round(db, user, round_id, request)
    case = core.lock_case(db, case.id)
    r = response_of(db, rnd.id)
    if r is not None and r.status == ResponseStatus.SUBMITTED.value:
        if r.idempotency_key == idempotency_key:
            return r, True
        raise conflict("response_already_submitted", "The response to this request has already been submitted. It was not submitted a second time.")
    rnd = db.get(ShcRound, rnd.id, populate_existing=True)
    _require_open(rnd, case)
    if r is None:
        raise invalid("There is nothing to submit yet. Add an explanation or evidence to at least one requirement first.", code="empty_response")
    if r.row_version != row_version:
        raise conflict("stale_version", "This response was changed elsewhere since you opened it. Reload it before submitting.")
    answered = [i for i in r.items if (i.explanation or "").strip() or i.documents]
    if not answered:
        raise invalid("Add an explanation or evidence to at least one requirement before submitting.", code="empty_response")
    now = utcnow()
    res = db.execute(update(ShcResponse).where(ShcResponse.id == r.id, ShcResponse.status == ResponseStatus.DRAFT.value, ShcResponse.row_version == row_version).values(
        status=ResponseStatus.SUBMITTED.value, submitted_by_id=user.id, submitted_at=now, idempotency_key=idempotency_key, row_version=row_version + 1, updated_at=now))
    if res.rowcount != 1:
        raise conflict("stale_version", "This response was changed or submitted at the same moment. It was not submitted twice.")
    db.execute(update(ShcRound).where(ShcRound.id == rnd.id, ShcRound.status == ComplianceRoundStatus.OPEN.value).values(status=ComplianceRoundStatus.RESPONSE_RECEIVED.value, response_received_at=now))
    from_state = case.state
    core.transition(db, case, CAction.RECEIVE_COMPLIANCE)
    core.log_event(db, case, user, "compliance_submitted", request=request, object_type="shc_response", object_id=r.id, from_state=from_state, to_state=case.state, institution_visible=True,
                   detail={"round": rnd.number, "items_answered": len(answered), "items_total": len(round_requirements(db, rnd.id))})
    db.add(ApplicationEvent(application_id=app.id, actor_id=user.id, action="compliance_response_submitted", detail={"round": rnd.number}))
    jobs.enqueue_cross_check(db, app.id, settings)  # refresh the automated checks against the new evidence
    db.flush()
    db.refresh(r)
    ctexts.compliance_submitted(db, case, app, rnd.id)
    return r, False


# ---- review ---------------------------------------------------------------------------------------------


def _assigned_count(db: Session, case_id: uuid.UUID) -> int:
    return db.scalar(select(func.count()).select_from(CommitteeAssignment).where(CommitteeAssignment.case_id == case_id, CommitteeAssignment.revoked_at.is_(None))) or 0


def start_review(db: Session, user: User, case_id: uuid.UUID, row_version: int, request: Request | None) -> CommitteeCase:
    case = _coordinator_case(db, user, case_id, request)
    core.require_version(case, row_version)
    core.require_state(case, CState.COMPLIANCE_RECEIVED, CState.PREPARATION)
    if case.state == CState.PREPARATION.value and shc_policy(case).get("compliance_submission") != "NOT_PERMITTED":
        raise conflict("compliance_first", "Review starts after the institution has had the chance to submit compliance. Request compliance first.")
    core.require_no_blockers(case, "no_committee_policy", "no_recommendation_structure")
    need = int((core.policy(case).get("content", {}).get("composition", {}) or {}).get("min_members") or 1)
    if _assigned_count(db, case.id) < need:
        raise conflict("no_members_assigned", "Members must be assigned (through the selection step) before review starts.")
    from_state = case.state
    core.transition(db, case, CAction.START_REVIEW, expected_version=row_version)
    if draft_recommendation(db, case.id) is None and not submitted_recommendations(db, case.id):
        db.add(CommitteeReport(case_id=case.id, kind=ReportKind.SHC_RECOMMENDATION.value, version_number=1, status=ReportStatus.DRAFT.value, content={"option": None, "sections": {}}, created_by_id=user.id))
    core.log_event(db, case, user, "review_started", request=request, object_type="committee_case", object_id=case.id, from_state=from_state, to_state=case.state)
    return case


def record_finding(db: Session, user: User, case_id: uuid.UUID, data: dict, request: Request | None) -> ShcFinding:
    case = _member_case(db, user, case_id, request)
    core.require_state(case, CState.UNDER_REVIEW)
    values = _finding_values(db, case, data)
    seq = (db.scalar(select(func.max(ShcFinding.sequence)).where(ShcFinding.case_id == case.id)) or 0) + 1
    f = ShcFinding(case_id=case.id, sequence=seq, created_by_id=user.id, **values)
    db.add(f)
    db.flush()
    core.log_event(db, case, user, "finding_recorded", request=request, object_type="shc_finding", object_id=f.id, detail={"sequence": seq, "assessment": f.assessment, "requirement_id": str(f.requirement_id) if f.requirement_id else None})
    return f


def _finding_values(db: Session, case: CommitteeCase, data: dict) -> dict:
    if data.get("assessment") not in ASSESSMENTS:
        raise invalid("Choose what the evidence shows.", [Issue("assessment", "Not a valid assessment.", "assessment")])
    text = (data.get("text") or "").strip()
    if len(text) < 5:
        raise invalid("Give the reasons for this assessment.", [Issue("text", "Reasons are needed.", "text")])
    rid = data.get("requirement_id")
    if rid is not None:
        req = db.get(ShcRequirement, uuid.UUID(str(rid)))
        if req is None or req.case_id != case.id or req.round_id is None:
            raise invalid("That requirement does not belong to this case.", [Issue("requirement_id", "Unknown requirement.", "requirement_id")])
    return dict(assessment=data["assessment"], text=text, requirement_id=uuid.UUID(str(rid)) if rid is not None else None)


def update_finding(db: Session, user: User, case_id: uuid.UUID, finding_id: uuid.UUID, data: dict, row_version: int, request: Request | None) -> ShcFinding:
    case = _member_case(db, user, case_id, request)
    core.require_state(case, CState.UNDER_REVIEW)
    f = db.get(ShcFinding, finding_id, populate_existing=True)
    if f is None or f.case_id != case.id:
        raise not_found("Finding")
    values = _finding_values(db, case, {"assessment": f.assessment, "text": f.text, "requirement_id": f.requirement_id, **data})
    res = db.execute(update(ShcFinding).where(ShcFinding.id == f.id, ShcFinding.row_version == row_version).values(**values, row_version=row_version + 1, updated_at=utcnow()))
    if res.rowcount != 1:
        raise conflict("stale_version", "This finding was changed by someone else since you opened it. Reload it before saving.")
    db.refresh(f)
    core.log_event(db, case, user, "finding_updated", request=request, object_type="shc_finding", object_id=f.id, detail={"sequence": f.sequence, "assessment": f.assessment, "by_author": f.created_by_id == user.id})
    return f


def record_proceeding(db: Session, user: User, case_id: uuid.UUID, data: dict, request: Request | None) -> ShcProceeding:
    """What happened at a hearing or review sitting. Append-only. Naming a confirmed hearing schedule records that the hearing took place."""
    case = _member_case(db, user, case_id, request)
    core.require_state(case, CState.UNDER_REVIEW)
    text = (data.get("text") or "").strip()
    if len(text) < 5:
        raise invalid("Record what happened.", [Issue("text", "A record is needed.", "text")])
    held_at = scheduling.to_utc(data["held_local"], data["timezone"])
    if held_at > utcnow():
        raise invalid("A sitting cannot be recorded before it has happened.", [Issue("held_local", "Choose a time that has passed.", "held_local")])
    schedule_id = data.get("schedule_id")
    sch = None
    if schedule_id is not None:
        sch = scheduling.active(db, case.id)
        if sch is None or str(sch.id) != str(schedule_id) or sch.status != "CONFIRMED":
            raise conflict("no_confirmed_hearing", "That hearing is not a confirmed, open schedule for this case.")
    row = ShcProceeding(case_id=case.id, schedule_id=sch.id if sch else None, held_at=held_at, participants=(data.get("participants") or "").strip(), text=text, recorded_by_id=user.id)
    db.add(row)
    db.flush()
    if sch is not None:
        scheduling.mark_completed(db, case, user, request)
    core.log_event(db, case, user, "proceeding_recorded", request=request, object_type="shc_proceeding", object_id=row.id, institution_visible=sch is not None, detail={"schedule_id": str(sch.id) if sch else None, "held_at": held_at.isoformat()})
    return row


# ---- the recommendation ------------------------------------------------------------------------------------


def save_recommendation_draft(db: Session, user: User, case_id: uuid.UUID, option: str | None, sections: dict[str, str], row_version: int, request: Request | None) -> CommitteeReport:
    case = _member_case(db, user, case_id, request)
    core.require_state(case, CState.UNDER_REVIEW)
    draft = draft_recommendation(db, case.id)
    if draft is None:
        raise conflict("no_draft_recommendation", "There is no draft recommendation to edit.")
    pol = shc_policy(case)
    if option is not None and option not in {o["key"] for o in pol.get("recommendation_options", [])}:
        raise invalid("Choose one of the configured recommendation options.", [Issue("option", "Not a configured option.", "option")])
    known = {s["key"] for s in pol.get("recommendation_sections", [])}
    unknown = sorted(set(sections) - known)
    if unknown:
        raise invalid("Some sections are not part of the configured recommendation.", [Issue("sections", f"Unknown section: {k}", k) for k in unknown])
    res = db.execute(update(CommitteeReport).where(CommitteeReport.id == draft.id, CommitteeReport.row_version == row_version, CommitteeReport.status == ReportStatus.DRAFT.value).values(
        content={"option": option, "sections": sections}, row_version=row_version + 1, updated_at=utcnow()))
    if res.rowcount != 1:
        raise conflict("stale_version", "The draft was changed by someone else since you opened it. Reload it before saving.")
    db.refresh(draft)
    core.log_event(db, case, user, "recommendation_draft_saved", request=request, object_type="committee_report", object_id=draft.id, detail={"version": draft.version_number, "by_author": draft.created_by_id == user.id})
    return draft


def _canonical(d: dict) -> str:
    return json.dumps(d, sort_keys=True, separators=(",", ":"), default=str)


def _validate_for_submission(db: Session, case: CommitteeCase, draft: CommitteeReport) -> tuple[list[Issue], dict]:
    pol = shc_policy(case)
    issues: list[Issue] = []
    content = draft.content or {}
    if content.get("option") not in {o["key"] for o in pol.get("recommendation_options", [])}:
        issues.append(Issue("option_missing", "Choose the recommendation.", "option"))
    for s in pol.get("recommendation_sections", []):
        if s.get("required") and len(((content.get("sections") or {}).get(s["key"]) or "").strip()) < 3:
            issues.append(Issue("section_missing", f"The section “{s['label']}” is required.", s["key"]))
    all_rounds = rounds(db, case.id)
    if any(r.status == ComplianceRoundStatus.OPEN.value for r in all_rounds):
        issues.append(Issue("compliance_open", "A compliance request is still open.", "round"))
    fnd = findings(db, case.id)
    assessed = {f.requirement_id for f in fnd}
    reqs = [r for rnd in all_rounds for r in round_requirements(db, rnd.id)]
    for r in reqs:
        if r.id not in assessed:
            issues.append(Issue("requirement_unassessed", f"No assessment was recorded for “{r.title}”.", str(r.id)))
    sch = scheduling.active(db, case.id)
    if sch is not None:
        issues.append(Issue("hearing_pending", "A hearing is scheduled but has not been recorded as held or cancelled.", str(sch.id)))
    if pol.get("hearing") == "REQUIRED" and not any(p.schedule_id for p in proceedings(db, case.id)):
        issues.append(Issue("hearing_required", "The configuration makes a hearing part of this process, and none has been recorded.", "hearing"))
    docs = current_documents(db, case.application_id)
    responses = []
    for rnd in all_rounds:
        resp = response_of(db, rnd.id)
        if resp is not None and resp.status == ResponseStatus.SUBMITTED.value:
            responses.append({"round": rnd.number, "submitted_at": resp.submitted_at.isoformat(), "items": [
                {"requirement_id": str(i.requirement_id), "explanation": i.explanation, "documents": [str(d.document_id) for d in i.documents]} for i in resp.items]})
    snapshot = {
        "rounds": [{"number": r.number, "status": r.status, "requirements": [{"id": str(q.id), "title": q.title, "item_key": q.item_key} for q in round_requirements(db, r.id)]} for r in all_rounds],
        "responses": responses,
        "documents": [{"item_key": k, "document_id": str(d.id), "version_number": d.version_number, "sha256": d.sha256} for k, d in sorted(docs.items())],
        "findings": [{"sequence": f.sequence, "requirement_id": str(f.requirement_id) if f.requirement_id else None, "assessment": f.assessment, "text": f.text, "by": f.created_by.full_name} for f in fnd],
        "proceedings": [{"id": str(p.id), "held_at": p.held_at.isoformat(), "schedule_id": str(p.schedule_id) if p.schedule_id else None} for p in proceedings(db, case.id)],
        "policy": {k: core.policy(case)[k] for k in ("id", "key", "version", "classification", "content_hash")} if core.policy(case) else None,
        "rule_version": case.trigger.get("rule_version"), "not_a_decision": True,
    }
    return issues, snapshot


def submit_recommendation(db: Session, user: User, case_id: uuid.UUID, idempotency_key: str, row_version: int, request: Request | None) -> tuple[CommitteeCase, CommitteeReport, bool]:
    case = _member_case(db, user, case_id, request)
    prior = db.scalar(select(CommitteeReport).where(CommitteeReport.case_id == case.id, CommitteeReport.kind == ReportKind.SHC_RECOMMENDATION.value, CommitteeReport.idempotency_key == idempotency_key))
    if prior is not None:
        return case, prior, True
    core.require_state(case, CState.UNDER_REVIEW)
    draft = draft_recommendation(db, case.id)
    if draft is None:
        raise conflict("no_draft_recommendation", "There is no draft recommendation to submit.")
    if draft.row_version != row_version:
        raise conflict("stale_version", "The draft was changed since you opened it. Reload it before submitting.")
    issues, snapshot = _validate_for_submission(db, case, draft)
    if issues:
        raise conflict("recommendation_incomplete", "The recommendation cannot be submitted yet.", issues)
    content = {"option": draft.content["option"], "sections": draft.content.get("sections", {})}
    digest = hashlib.sha256(_canonical({"content": content, "snapshot": snapshot}).encode()).hexdigest()
    now = utcnow()
    res = db.execute(update(CommitteeReport).where(CommitteeReport.id == draft.id, CommitteeReport.status == ReportStatus.DRAFT.value, CommitteeReport.row_version == row_version).values(
        status=ReportStatus.SUBMITTED.value, content=content, source_snapshot=snapshot, content_hash=digest, submitted_by_id=user.id, submitted_at=now, idempotency_key=idempotency_key, row_version=row_version + 1, updated_at=now))
    if res.rowcount != 1:
        raise conflict("stale_version", "The recommendation was changed or submitted at the same moment. Reload it.")
    db.refresh(draft)
    from_state = case.state
    core.transition(db, case, CAction.SUBMIT_RECOMMENDATION)
    core.log_event(db, case, user, "recommendation_submitted", request=request, object_type="committee_report", object_id=draft.id, from_state=from_state, to_state=case.state,
                   detail={"version": draft.version_number, "content_hash": digest, "option": content["option"], "decided": False})
    db.flush()
    ctexts.report_submitted(db, case, core.application(db, case), draft.id, draft.version_number)
    return case, draft, False


def open_amendment(db: Session, user: User, case_id: uuid.UUID, reason: str, row_version: int, request: Request | None) -> CommitteeCase:
    case = _coordinator_case(db, user, case_id, request)
    core.require_version(case, row_version)
    core.require_state(case, CState.RECOMMENDATION_SUBMITTED)
    if not reason or len(reason.strip()) < 5:
        raise invalid("Record why an amendment is needed.")
    last = submitted_recommendations(db, case.id)[-1]
    from_state = case.state
    core.transition(db, case, CAction.OPEN_AMENDMENT, expected_version=row_version)
    new = CommitteeReport(case_id=case.id, kind=ReportKind.SHC_RECOMMENDATION.value, version_number=last.version_number + 1, status=ReportStatus.DRAFT.value, content=dict(last.content or {}),
                          supersedes_id=last.id, amendment_reason=reason.strip(), amendment_authorised_by_id=user.id, created_by_id=user.id)
    db.add(new)
    db.flush()
    core.log_event(db, case, user, "amendment_opened", request=request, object_type="committee_report", object_id=new.id, from_state=from_state, to_state=case.state, reason=reason, detail={"supersedes": str(last.id), "version": new.version_number})
    return case


def complete(db: Session, user: User, case_id: uuid.UUID, summary: str, row_version: int, request: Request | None) -> CommitteeCase:
    """Record that the SHC stage is finished. NOT a decision. The route engine then records what follows."""
    case = _coordinator_case(db, user, case_id, request)
    core.require_version(case, row_version)
    core.require_state(case, CState.RECOMMENDATION_SUBMITTED)
    if not summary or len(summary.strip()) < 5:
        raise invalid("Record a short note on completing the SHC stage.")
    last = submitted_recommendations(db, case.id)[-1]
    now = utcnow()
    completion = {"summary": summary.strip(), "recommendation_id": str(last.id), "recommendation_version": last.version_number, "recommendation_hash": last.content_hash,
                  "option": (last.content or {}).get("option"), "decided": False}
    from_state = case.state
    core.transition(db, case, CAction.COMPLETE, expected_version=row_version, completed_at=now, completed_by_id=user.id, completion=completion)
    core.log_event(db, case, user, "stage_completed", request=request, object_type="committee_case", object_id=case.id, from_state=from_state, to_state=case.state, reason=summary,
                   detail={"recommendation_version": last.version_number, "decided": False})
    db.flush()
    ctexts.stage_completed(db, case, core.application(db, case))
    stages.advance_from_case(db, case, user, request)
    return case
