"""EVC and SHC endpoints (Phase 5).

Each endpoint is checked twice: by role permission here, and by case scope in the service (`committee_access.get_case`: a coordinator of the stage, or a
member with an active assignment to this exact case). Mutating endpoints return the refreshed case so a workspace updates in one round trip.
Neither process can approve, reject or decide anything: there is no such endpoint.
"""

import uuid
from datetime import datetime
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, Query, Request, Response, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit, committee_access
from app import committee_presenters as pr
from app import evidence_presenters as ep
from app.config import Settings, get_settings
from app.db import get_db
from app.deps import CurrentUser, csrf_protect, get_current_user, require_any_permission, require_permission
from app.domain import CommitteeStage
from app.models import User
from app.models_applications import Application
from app.models_committee import CommitteeCase, DecisionHandoff
from app.rbac import Permission, Role
from app.schemas_committee import (
    CancelIn,
    ComplianceDraftIn,
    ComplianceRequestIn,
    ComplianceSubmitIn,
    ConfirmIn,
    DetermineIn,
    EvidenceLinkIn,
    FindingIn,
    FindingPatchIn,
    ObservationIn,
    ProceedingIn,
    ReasonVersionIn,
    RecommendationDraftIn,
    ReportDraftIn,
    RequirementIn,
    RequirementPatchIn,
    ReselectIn,
    RescheduleIn,
    ScheduleIn,
    SelectionIn,
    SubmitIn,
    SummaryIn,
    VersionIn,
)
from app.schemas_evidence import ApplicationEvidenceOut
from app.services import applications as app_svc
from app.services import evc as evc_svc
from app.services import scheduling, selection, stages
from app.services import shc as shc_svc
from app.services.errors import ServiceError
from app.storage import LocalStorage, StorageError, get_storage

_DEPS = [Depends(csrf_protect)]


def _detail(db: Session, current: CurrentUser, case_id: uuid.UUID, stage: CommitteeStage, settings: Settings, request: Request) -> dict:
    case, scope = committee_access.get_case(db, current.user, case_id, request, stage=stage)
    return pr.case_detail(db, current.user, case, scope, settings)


def _common(router: APIRouter, stage: CommitteeStage, queue_p: Permission, coord_p: Permission) -> None:
    """Endpoints that are the same for both stages: queue, detail, evidence, selection, schedule and next-stage determination."""
    can_queue, can_coord = require_permission(queue_p), require_permission(coord_p)
    member_role = selection.MEMBER_ROLE[stage]

    @router.get("/cases")
    def queue(
        current: CurrentUser = Depends(can_queue), db: Session = Depends(get_db), state: str | None = Query(None, pattern="^[A-Z_]{3,32}$"), q: str | None = Query(None, max_length=80),
        category_id: uuid.UUID | None = None, mine: bool = False, blocked: bool = False, visit_from: datetime | None = None, visit_to: datetime | None = None,
        page: int = Query(1, ge=1, le=10_000), page_size: int = Query(20, ge=1, le=50),
    ) -> dict:
        return pr.list_queue(db, current.user, stage, state=state, q=q, category_id=category_id, mine=mine, blocked=blocked, visit_from=visit_from, visit_to=visit_to, page=page, page_size=page_size)

    @router.get("/cases/{case_id}")
    def detail(case_id: uuid.UUID, request: Request, current: CurrentUser = Depends(can_queue), db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> dict:
        return _detail(db, current, case_id, stage, settings, request)

    @router.get("/cases/{case_id}/evidence", response_model=ApplicationEvidenceOut)
    def evidence(
        case_id: uuid.UUID, request: Request, current: CurrentUser = Depends(require_permission(Permission.EVIDENCE_READ)), db: Session = Depends(get_db), settings: Settings = Depends(get_settings),
    ) -> ApplicationEvidenceOut:
        """The Phase 3 evidence package of the application behind this case (original documents, extracted values with pages, automated issues)."""
        case, _ = committee_access.get_case(db, current.user, case_id, request, stage=stage)
        app = db.get(Application, case.application_id)
        audit.record(db, "evidence.viewed", request=request, actor=current.user, target_type="application", target_id=app.id, detail={"committee_case_id": str(case.id), "stage": stage.value})
        db.commit()
        return ep.application_evidence(db, app, "reviewer", settings)

    @router.get("/assignable")
    def assignable(current: CurrentUser = Depends(can_coord), db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> dict:
        """Fictional members a coordinator may record in a DEMONSTRATION selection. Empty when only the official process applies."""
        users = list(db.scalars(select(User).where(User.role == member_role.value, User.is_active.is_(True)).order_by(User.full_name))) if settings.demo_rules_enabled else []
        return {"demo_only": True, "members": [{"id": u.id, "name": u.full_name} for u in users]}

    # ---- selection ----
    @router.post("/cases/{case_id}/selection")
    def request_selection(case_id: uuid.UUID, body: SelectionIn, request: Request, response: Response, current: CurrentUser = Depends(can_coord), db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> dict:
        _, _, replay = selection.request_selection(db, current.user, case_id, stage, body.user_ids, body.idempotency_key, request, settings)
        db.commit()
        if replay:
            response.headers["X-Idempotent-Replay"] = "true"
        return _detail(db, current, case_id, stage, settings, request)

    @router.post("/cases/{case_id}/selection/refresh")
    def refresh_selection(case_id: uuid.UUID, request: Request, current: CurrentUser = Depends(can_coord), db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> dict:
        selection.refresh(db, current.user, case_id, stage, request)
        db.commit()
        return _detail(db, current, case_id, stage, settings, request)

    @router.post("/cases/{case_id}/selection/cancel")
    def cancel_selection(case_id: uuid.UUID, body: CancelIn, request: Request, current: CurrentUser = Depends(can_coord), db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> dict:
        selection.cancel_request(db, current.user, case_id, stage, body.reason, request)
        db.commit()
        return _detail(db, current, case_id, stage, settings, request)

    # ---- schedule (a visit for EVC, a hearing for SHC) ----
    @router.post("/cases/{case_id}/schedule", status_code=201)
    def propose(case_id: uuid.UUID, body: ScheduleIn, request: Request, response: Response, current: CurrentUser = Depends(can_coord), db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> dict:
        _, _, replay = scheduling.propose(db, current.user, case_id, stage, body.model_dump(exclude={"idempotency_key"}), body.idempotency_key, request)
        db.commit()
        if replay:
            response.status_code = 200
            response.headers["X-Idempotent-Replay"] = "true"
        return _detail(db, current, case_id, stage, settings, request)

    @router.post("/cases/{case_id}/schedule/reschedule", status_code=201)
    def reschedule(case_id: uuid.UUID, body: RescheduleIn, request: Request, response: Response, current: CurrentUser = Depends(can_coord), db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> dict:
        _, _, replay = scheduling.propose(db, current.user, case_id, stage, body.model_dump(exclude={"idempotency_key", "reason"}), body.idempotency_key, request, reschedule_reason=body.reason)
        db.commit()
        if replay:
            response.status_code = 200
            response.headers["X-Idempotent-Replay"] = "true"
        return _detail(db, current, case_id, stage, settings, request)

    @router.post("/cases/{case_id}/schedule/{schedule_id}/confirm")
    def confirm(case_id: uuid.UUID, schedule_id: uuid.UUID, body: ConfirmIn, request: Request, current: CurrentUser = Depends(can_coord), db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> dict:
        scheduling.confirm(db, current.user, case_id, stage, schedule_id, body.row_version, request)
        db.commit()
        return _detail(db, current, case_id, stage, settings, request)

    @router.post("/cases/{case_id}/schedule/{schedule_id}/cancel")
    def cancel(case_id: uuid.UUID, schedule_id: uuid.UUID, body: CancelIn, request: Request, current: CurrentUser = Depends(can_coord), db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> dict:
        scheduling.cancel_schedule(db, current.user, case_id, stage, schedule_id, body.reason, request)
        db.commit()
        return _detail(db, current, case_id, stage, settings, request)

    # ---- the route: what follows this stage ----
    @router.post("/cases/{case_id}/route/determine")
    def determine(case_id: uuid.UUID, body: DetermineIn, request: Request, current: CurrentUser = Depends(can_coord), db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> dict:
        stages.determine_after_case(db, current.user, case_id, stage, body.stage_key, body.reason, request)
        db.commit()
        return _detail(db, current, case_id, stage, settings, request)


# =====================================================================================================
# EVC
# =====================================================================================================

evc = APIRouter(prefix="/api/evc", tags=["evc"], dependencies=_DEPS)
_common(evc, CommitteeStage.EVC, Permission.EVC_QUEUE, Permission.EVC_COORDINATE)
can_evc_coord = require_permission(Permission.EVC_COORDINATE)
can_evc_work = require_permission(Permission.EVC_WORK)
_E = CommitteeStage.EVC


@evc.post("/cases/{case_id}/reselect")
def evc_reselect(case_id: uuid.UUID, body: ReselectIn, request: Request, current: CurrentUser = Depends(can_evc_coord), db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> dict:
    selection.reselect(db, current.user, case_id, body.reason, body.row_version, request)
    db.commit()
    return _detail(db, current, case_id, _E, settings, request)


@evc.post("/cases/{case_id}/visit/start")
def evc_start(case_id: uuid.UUID, body: VersionIn, request: Request, current: CurrentUser = Depends(can_evc_work), db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> dict:
    evc_svc.start_visit(db, current.user, case_id, body.row_version, request)
    db.commit()
    return _detail(db, current, case_id, _E, settings, request)


@evc.put("/cases/{case_id}/observations/{item_key}")
def evc_observation(case_id: uuid.UUID, item_key: str, body: ObservationIn, request: Request, current: CurrentUser = Depends(can_evc_work), db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> dict:
    evc_svc.record_observation(db, current.user, case_id, item_key, body.model_dump(mode="json", exclude={"row_version"}), body.row_version, request)
    db.commit()
    return _detail(db, current, case_id, _E, settings, request)


@evc.post("/cases/{case_id}/evidence", status_code=201)
def evc_evidence_file(
    case_id: uuid.UUID, request: Request, file: UploadFile = File(), item_key: str | None = Form(None), description: str = Form(""), current: CurrentUser = Depends(can_evc_work),
    db: Session = Depends(get_db), settings: Settings = Depends(get_settings), storage: LocalStorage = Depends(get_storage),
) -> dict:
    evc_svc.add_evidence_file(db, current.user, case_id, file, item_key, description, request, settings, storage)
    return _detail(db, current, case_id, _E, settings, request)


@evc.post("/cases/{case_id}/evidence/link", status_code=201)
def evc_evidence_link(case_id: uuid.UUID, body: EvidenceLinkIn, request: Request, current: CurrentUser = Depends(can_evc_work), db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> dict:
    evc_svc.add_evidence_link(db, current.user, case_id, body.url, body.description, body.item_key, request)
    db.commit()
    return _detail(db, current, case_id, _E, settings, request)


@evc.delete("/cases/{case_id}/evidence/{evidence_id}")
def evc_evidence_remove(case_id: uuid.UUID, evidence_id: uuid.UUID, request: Request, current: CurrentUser = Depends(can_evc_work), db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> dict:
    evc_svc.remove_evidence(db, current.user, case_id, evidence_id, request)
    db.commit()
    return _detail(db, current, case_id, _E, settings, request)


@evc.get("/evidence/{evidence_id}/download")
def evc_evidence_download(evidence_id: uuid.UUID, request: Request, current: CurrentUser = Depends(require_permission(Permission.EVC_QUEUE)), db: Session = Depends(get_db), storage: LocalStorage = Depends(get_storage)) -> StreamingResponse:
    ev = evc_svc.evidence_for_download(db, current.user, evidence_id, request)
    try:
        with storage.open(ev.storage_key) as fh:
            data = fh.read()
    except (OSError, StorageError) as exc:
        raise ServiceError(503, "storage_unavailable", "The stored file could not be read just now. Please try again.") from exc
    return StreamingResponse(iter([data]), media_type=ev.media_type, headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(ev.original_filename or 'file')}", "Content-Length": str(len(data)),
                                                                              "X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store"})


@evc.put("/cases/{case_id}/report")
def evc_report_draft(case_id: uuid.UUID, body: ReportDraftIn, request: Request, current: CurrentUser = Depends(can_evc_work), db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> dict:
    evc_svc.save_report_draft(db, current.user, case_id, body.sections, body.row_version, request)
    db.commit()
    return _detail(db, current, case_id, _E, settings, request)


@evc.post("/cases/{case_id}/report/submit")
def evc_report_submit(case_id: uuid.UUID, body: SubmitIn, request: Request, response: Response, current: CurrentUser = Depends(can_evc_work), db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> dict:
    _, _, replay = evc_svc.submit_report(db, current.user, case_id, body.idempotency_key, body.row_version, request)
    db.commit()
    if replay:
        response.headers["X-Idempotent-Replay"] = "true"
    return _detail(db, current, case_id, _E, settings, request)


@evc.post("/cases/{case_id}/report/amend")
def evc_report_amend(case_id: uuid.UUID, body: ReasonVersionIn, request: Request, current: CurrentUser = Depends(can_evc_coord), db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> dict:
    evc_svc.open_amendment(db, current.user, case_id, body.reason, body.row_version, request)
    db.commit()
    return _detail(db, current, case_id, _E, settings, request)


@evc.post("/cases/{case_id}/complete")
def evc_complete(case_id: uuid.UUID, body: SummaryIn, request: Request, current: CurrentUser = Depends(can_evc_coord), db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> dict:
    evc_svc.complete(db, current.user, case_id, body.summary, body.row_version, request)
    db.commit()
    return _detail(db, current, case_id, _E, settings, request)


# =====================================================================================================
# SHC
# =====================================================================================================

shc = APIRouter(prefix="/api/shc", tags=["shc"], dependencies=_DEPS)
_common(shc, CommitteeStage.SHC, Permission.SHC_QUEUE, Permission.SHC_COORDINATE)
can_shc_coord = require_permission(Permission.SHC_COORDINATE)
can_shc_work = require_permission(Permission.SHC_WORK)
_S = CommitteeStage.SHC


@shc.post("/cases/{case_id}/requirements", status_code=201)
def shc_add_requirement(case_id: uuid.UUID, body: RequirementIn, request: Request, current: CurrentUser = Depends(can_shc_coord), db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> dict:
    shc_svc.add_requirement(db, current.user, case_id, body.model_dump(), request)
    db.commit()
    return _detail(db, current, case_id, _S, settings, request)


@shc.patch("/cases/{case_id}/requirements/{req_id}")
def shc_edit_requirement(case_id: uuid.UUID, req_id: uuid.UUID, body: RequirementPatchIn, request: Request, current: CurrentUser = Depends(can_shc_coord), db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> dict:
    shc_svc.update_requirement(db, current.user, case_id, req_id, body.model_dump(exclude_unset=True, exclude={"row_version"}), body.row_version, request)
    db.commit()
    return _detail(db, current, case_id, _S, settings, request)


@shc.delete("/cases/{case_id}/requirements/{req_id}")
def shc_remove_requirement(case_id: uuid.UUID, req_id: uuid.UUID, request: Request, current: CurrentUser = Depends(can_shc_coord), db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> dict:
    shc_svc.remove_requirement(db, current.user, case_id, req_id, request)
    db.commit()
    return _detail(db, current, case_id, _S, settings, request)


@shc.post("/cases/{case_id}/compliance/request")
def shc_request_compliance(case_id: uuid.UUID, body: ComplianceRequestIn, request: Request, current: CurrentUser = Depends(can_shc_coord), db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> dict:
    shc_svc.request_compliance(db, current.user, case_id, body.instructions, body.row_version, request)
    db.commit()
    return _detail(db, current, case_id, _S, settings, request)


@shc.post("/cases/{case_id}/review/start")
def shc_start_review(case_id: uuid.UUID, body: VersionIn, request: Request, current: CurrentUser = Depends(can_shc_coord), db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> dict:
    shc_svc.start_review(db, current.user, case_id, body.row_version, request)
    db.commit()
    return _detail(db, current, case_id, _S, settings, request)


@shc.post("/cases/{case_id}/findings", status_code=201)
def shc_finding(case_id: uuid.UUID, body: FindingIn, request: Request, current: CurrentUser = Depends(can_shc_work), db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> dict:
    shc_svc.record_finding(db, current.user, case_id, body.model_dump(mode="json"), request)
    db.commit()
    return _detail(db, current, case_id, _S, settings, request)


@shc.patch("/cases/{case_id}/findings/{finding_id}")
def shc_edit_finding(case_id: uuid.UUID, finding_id: uuid.UUID, body: FindingPatchIn, request: Request, current: CurrentUser = Depends(can_shc_work), db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> dict:
    shc_svc.update_finding(db, current.user, case_id, finding_id, body.model_dump(mode="json", exclude_unset=True, exclude={"row_version"}), body.row_version, request)
    db.commit()
    return _detail(db, current, case_id, _S, settings, request)


@shc.post("/cases/{case_id}/proceedings", status_code=201)
def shc_proceeding(case_id: uuid.UUID, body: ProceedingIn, request: Request, current: CurrentUser = Depends(can_shc_work), db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> dict:
    shc_svc.record_proceeding(db, current.user, case_id, body.model_dump(mode="json"), request)
    db.commit()
    return _detail(db, current, case_id, _S, settings, request)


@shc.put("/cases/{case_id}/recommendation")
def shc_recommendation_draft(case_id: uuid.UUID, body: RecommendationDraftIn, request: Request, current: CurrentUser = Depends(can_shc_work), db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> dict:
    shc_svc.save_recommendation_draft(db, current.user, case_id, body.option, body.sections, body.row_version, request)
    db.commit()
    return _detail(db, current, case_id, _S, settings, request)


@shc.post("/cases/{case_id}/recommendation/submit")
def shc_recommendation_submit(case_id: uuid.UUID, body: SubmitIn, request: Request, response: Response, current: CurrentUser = Depends(can_shc_work), db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> dict:
    _, _, replay = shc_svc.submit_recommendation(db, current.user, case_id, body.idempotency_key, body.row_version, request)
    db.commit()
    if replay:
        response.headers["X-Idempotent-Replay"] = "true"
    return _detail(db, current, case_id, _S, settings, request)


@shc.post("/cases/{case_id}/recommendation/amend")
def shc_recommendation_amend(case_id: uuid.UUID, body: ReasonVersionIn, request: Request, current: CurrentUser = Depends(can_shc_coord), db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> dict:
    shc_svc.open_amendment(db, current.user, case_id, body.reason, body.row_version, request)
    db.commit()
    return _detail(db, current, case_id, _S, settings, request)


@shc.post("/cases/{case_id}/complete")
def shc_complete(case_id: uuid.UUID, body: SummaryIn, request: Request, current: CurrentUser = Depends(can_shc_coord), db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> dict:
    shc_svc.complete(db, current.user, case_id, body.summary, body.row_version, request)
    db.commit()
    return _detail(db, current, case_id, _S, settings, request)


# =====================================================================================================
# The institution: committee status of its own applications, and SHC compliance
# =====================================================================================================

inst = APIRouter(tags=["committee"], dependencies=_DEPS)
can_status = require_permission(Permission.COMMITTEE_STATUS_OWN)
can_respond = require_permission(Permission.SHC_RESPOND)


@inst.get("/api/applications/{application_id}/committee")
def application_committee(application_id: uuid.UUID, request: Request, current: CurrentUser = Depends(can_status), db: Session = Depends(get_db)) -> dict:
    """Where the application stands in the EVC / SHC stages, in the institution's terms. No names, no internal notes, no recommendation."""
    app = app_svc.get_owned(db, current.user, application_id, request)
    return pr.institution_status(db, app)


@inst.get("/api/compliance")
def list_compliance(current: CurrentUser = Depends(can_status), db: Session = Depends(get_db)) -> list[dict]:
    if current.user.institution_id is None:
        return []
    out: list[dict] = []
    for app in db.scalars(select(Application).where(Application.institution_id == current.user.institution_id)):
        out += pr.inst_round_summaries(db, app)
    return sorted(out, key=lambda r: r["requested_at"], reverse=True)


@inst.get("/api/compliance/{round_id}")
def get_compliance(round_id: uuid.UUID, request: Request, current: CurrentUser = Depends(can_status), db: Session = Depends(get_db)) -> dict:
    rnd, case, app = shc_svc.institution_round(db, current.user, round_id, request)
    return pr.inst_round(db, rnd, case, app)


@inst.put("/api/compliance/{round_id}/response")
def save_compliance(round_id: uuid.UUID, body: ComplianceDraftIn, request: Request, current: CurrentUser = Depends(can_respond), db: Session = Depends(get_db)) -> dict:
    shc_svc.save_response(db, current.user, round_id, body.explanation, [{"requirement_id": i.requirement_id, "explanation": i.explanation} for i in body.items], body.row_version, request)
    db.commit()
    rnd, case, app = shc_svc.institution_round(db, current.user, round_id, request)
    return pr.inst_round(db, rnd, case, app)


@inst.post("/api/compliance/{round_id}/requirements/{requirement_id}/documents", status_code=201)
def upload_compliance(
    round_id: uuid.UUID, requirement_id: uuid.UUID, request: Request, response: Response, file: UploadFile = File(), current: CurrentUser = Depends(can_respond), db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings), storage: LocalStorage = Depends(get_storage),
) -> dict:
    """Compliance evidence: a new version of the requirement's document, run through the same checks and processing as any upload. Earlier versions stay."""
    _, outcome = shc_svc.upload_compliance(db, current.user, round_id, requirement_id, file, request, settings, storage)
    if outcome == "duplicate":
        response.status_code = 200
        response.headers["X-Upload-Result"] = "duplicate_of_current"
    rnd, case, app = shc_svc.institution_round(db, current.user, round_id, request)
    return pr.inst_round(db, rnd, case, app)


@inst.post("/api/compliance/{round_id}/response/submit")
def submit_compliance(round_id: uuid.UUID, body: ComplianceSubmitIn, request: Request, response: Response, current: CurrentUser = Depends(can_respond), db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> dict:
    _, replay = shc_svc.submit_response(db, current.user, round_id, body.idempotency_key, body.row_version, request, settings)
    db.commit()
    if replay:
        response.headers["X-Idempotent-Replay"] = "true"
    rnd, case, app = shc_svc.institution_round(db, current.user, round_id, request)
    return pr.inst_round(db, rnd, case, app)


# =====================================================================================================
# Staff: applications ready for the later decision stage (a hand-off list; nothing is decided here)
# =====================================================================================================

handoffs = APIRouter(tags=["committee"], dependencies=_DEPS)


@handoffs.get("/api/committee/decision-handoffs")
def decision_handoffs(current: CurrentUser = Depends(require_any_permission(Permission.EVC_COORDINATE, Permission.SHC_COORDINATE, Permission.SCRUTINY_ASSIGN)), db: Session = Depends(get_db)) -> list[dict]:
    rows = []
    for h in db.scalars(select(DecisionHandoff).order_by(DecisionHandoff.created_at.desc()).limit(200)):
        app = db.get(Application, h.application_id)
        rows.append({"application": pr._app_summary(db, app), "status": h.status, "created_at": h.created_at, "stages": [s["stage_key"] for s in h.basis["stages"]], "decided": False, "note": h.basis["note"]})
    return rows


_ = (get_current_user, Role, CommitteeCase)
