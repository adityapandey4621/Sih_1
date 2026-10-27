"""Executive Committee decisions, Council steps, official outcomes and appeals (Phase 6).

Each endpoint is checked by role permission here and, where it is about one institution, by ownership in the service. There is no endpoint that decides: a decision
is *recorded* by a person holding `decision:record`, and no committee report, recommendation or administrator role can reach it.
"""

import uuid
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, Query, Request, Response, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit
from app import decision_presenters as dp
from app import evidence_presenters as ep
from app.config import Settings, get_settings
from app.db import get_db
from app.deps import CurrentUser, csrf_protect, get_current_user, require_permission
from app.models_applications import Application
from app.models_decisions import Appeal, AppealDocument, DecisionCase, OfficialIssuance
from app.rbac import Permission, permissions_for
from app.schemas_decisions import (
    AppealIn,
    AppealLinkIn,
    CloseIn,
    CouncilAppealDecisionIn,
    DecisionCorrectionIn,
    DecisionIn,
    ObservationIn,
    RatificationIn,
)
from app.schemas_evidence import ApplicationEvidenceOut
from app.services import appeals, decisions
from app.services import applications as app_svc
from app.services.errors import ServiceError, not_found
from app.storage import LocalStorage, StorageError, get_storage

router = APIRouter(tags=["decisions"], dependencies=[Depends(csrf_protect)])

can_queue = require_permission(Permission.DECISION_QUEUE)
can_observe = require_permission(Permission.DECISION_OBSERVE)
can_record = require_permission(Permission.DECISION_RECORD)
can_issue = require_permission(Permission.DECISION_ISSUE)
can_council = require_permission(Permission.COUNCIL_RECORD)


def _detail(db: Session, current: CurrentUser, case_id: uuid.UUID, settings: Settings, request: Request) -> dict:
    case = decisions.get_case(db, current.user, case_id, request)
    return dp.case_detail(db, current.user, case, settings)


@router.get("/api/decisions/cases")
def queue(
    current: CurrentUser = Depends(can_queue), db: Session = Depends(get_db), state: str | None = Query(None, pattern="^[A-Z_]{3,32}$"), q: str | None = Query(None, max_length=80),
    page: int = Query(1, ge=1, le=10_000), page_size: int = Query(20, ge=1, le=50),
) -> dict:
    return dp.list_queue(db, current.user, state=state, q=q, page=page, page_size=page_size)


@router.get("/api/decisions/cases/{case_id}")
def detail(case_id: uuid.UUID, request: Request, current: CurrentUser = Depends(can_queue), db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> dict:
    return _detail(db, current, case_id, settings, request)


@router.get("/api/decisions/cases/{case_id}/evidence", response_model=ApplicationEvidenceOut)
def evidence(case_id: uuid.UUID, request: Request, current: CurrentUser = Depends(require_permission(Permission.EVIDENCE_READ)), db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> ApplicationEvidenceOut:
    """The Phase 3 evidence package of the application behind this decision case (original documents, extracted values, automated issues)."""
    case = decisions.get_case(db, current.user, case_id, request)
    app = db.get(Application, case.application_id)
    audit.record(db, "evidence.viewed", request=request, actor=current.user, target_type="application", target_id=app.id, detail={"decision_case_id": str(case.id)})
    db.commit()
    return ep.application_evidence(db, app, "reviewer", settings)


@router.post("/api/decisions/cases/{case_id}/observations", status_code=201)
def observe(case_id: uuid.UUID, body: ObservationIn, request: Request, current: CurrentUser = Depends(can_observe), db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> dict:
    decisions.record_observation(db, current.user, case_id, body.text, body.idempotency_key, request)
    db.commit()
    return _detail(db, current, case_id, settings, request)


@router.post("/api/decisions/cases/{case_id}/decision", status_code=201)
def record(case_id: uuid.UUID, body: DecisionIn, request: Request, response: Response, current: CurrentUser = Depends(can_record), db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> dict:
    _, _, replay = decisions.record_decision(db, current.user, case_id, body.model_dump(exclude={"idempotency_key", "row_version"}), body.idempotency_key, body.row_version, request)
    db.commit()
    if replay:
        response.status_code = 200
        response.headers["X-Idempotent-Replay"] = "true"
    return _detail(db, current, case_id, settings, request)


@router.post("/api/decisions/cases/{case_id}/decision/correct", status_code=201)
def correct(case_id: uuid.UUID, body: DecisionCorrectionIn, request: Request, response: Response, current: CurrentUser = Depends(can_record), db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> dict:
    _, _, replay = decisions.correct_decision(db, current.user, case_id, body.model_dump(exclude={"idempotency_key", "row_version", "correction_reason"}), body.correction_reason, body.idempotency_key, body.row_version, request)
    db.commit()
    if replay:
        response.status_code = 200
        response.headers["X-Idempotent-Replay"] = "true"
    return _detail(db, current, case_id, settings, request)


@router.post("/api/decisions/cases/{case_id}/issue", status_code=201)
def issue(
    case_id: uuid.UUID, request: Request, response: Response, document_reference: str | None = Form(None), idempotency_key: str = Form(..., min_length=8, max_length=80), row_version: int = Form(...),
    file: UploadFile | None = File(None), current: CurrentUser = Depends(can_issue), db: Session = Depends(get_db), settings: Settings = Depends(get_settings), storage: LocalStorage = Depends(get_storage),
) -> dict:
    """Issue the recorded decision to the institution: a reference and/or the official file. The system generates no letter wording, signature or seal."""
    _, _, replay = decisions.issue_outcome(db, current.user, case_id, document_reference, file, idempotency_key, row_version, request, settings, storage)
    db.commit()
    if replay:
        response.status_code = 200
        response.headers["X-Idempotent-Replay"] = "true"
    return _detail(db, current, case_id, settings, request)


@router.post("/api/decisions/cases/{case_id}/ratification", status_code=201)
def ratify(case_id: uuid.UUID, body: RatificationIn, request: Request, response: Response, current: CurrentUser = Depends(can_council), db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> dict:
    _, replay = decisions.record_ratification(db, current.user, case_id, body.outcome, body.reasons, body.reference, body.correction_reason, body.idempotency_key, request)
    db.commit()
    if replay:
        response.status_code = 200
        response.headers["X-Idempotent-Replay"] = "true"
    return _detail(db, current, case_id, settings, request)


@router.post("/api/decisions/cases/{case_id}/close")
def close(case_id: uuid.UUID, body: CloseIn, request: Request, current: CurrentUser = Depends(can_issue), db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> dict:
    decisions.close_case(db, current.user, case_id, body.row_version, request, settings)
    db.commit()
    return _detail(db, current, case_id, settings, request)


@router.post("/api/decisions/appeals/{appeal_id}/council-decision", status_code=201)
def council_appeal_decision(
    appeal_id: uuid.UUID, body: CouncilAppealDecisionIn, request: Request, response: Response, current: CurrentUser = Depends(can_council), db: Session = Depends(get_db), settings: Settings = Depends(get_settings),
) -> dict:
    ap = db.get(Appeal, appeal_id)
    if ap is None:
        raise not_found("Appeal")
    decisions.get_case(db, current.user, ap.decision_case_id, request)
    _, replay = appeals.record_council_decision(db, current.user, appeal_id, body.outcome, body.reasons, body.speaking_order, body.reference, body.correction_reason, body.idempotency_key, request)
    db.commit()
    if replay:
        response.status_code = 200
        response.headers["X-Idempotent-Replay"] = "true"
    return _detail(db, current, ap.decision_case_id, settings, request)


def _file_response(storage: LocalStorage, key: str, media: str, name: str) -> StreamingResponse:
    try:
        with storage.open(key) as fh:
            data = fh.read()
    except (OSError, StorageError) as exc:
        raise ServiceError(503, "storage_unavailable", "The stored file could not be read just now. Please try again.") from exc
    return StreamingResponse(iter([data]), media_type=media, headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}", "Content-Length": str(len(data)), "X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store"})


@router.get("/api/decisions/issuances/{issuance_id}/download")
def download_issuance(issuance_id: uuid.UUID, request: Request, current: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db), storage: LocalStorage = Depends(get_storage)) -> StreamingResponse:
    """The official outcome file: the owning institution, or a decision role. Anyone else: not found (audited)."""
    iss = db.get(OfficialIssuance, issuance_id)
    case = db.get(DecisionCase, iss.case_id) if iss else None
    app = db.get(Application, case.application_id) if case else None
    perms = permissions_for(current.user.role)
    owner = app is not None and current.user.institution_id is not None and app.institution_id == current.user.institution_id and Permission.DECISION_READ_OWN in perms
    staff = Permission.DECISION_QUEUE in perms
    if iss is None or iss.storage_key is None or not (owner or staff) or (owner and iss.status != "ISSUED" and not staff):
        if iss is not None:
            audit.record(db, "decision.access_denied", request=request, actor=current.user, target_type="official_issuance", target_id=iss.id)
            db.commit()
        raise not_found("Document")
    audit.record(db, "decision.document_downloaded", request=request, actor=current.user, target_type="official_issuance", target_id=iss.id, detail={"viewer": "owner" if owner else "staff"})
    db.commit()
    return _file_response(storage, iss.storage_key, iss.media_type or "application/octet-stream", iss.original_filename or "outcome")


# =====================================================================================================
# The institution: official status and appeals
# =====================================================================================================

inst = APIRouter(tags=["appeals"], dependencies=[Depends(csrf_protect)])
can_read_own = require_permission(Permission.DECISION_READ_OWN)
can_appeal = require_permission(Permission.APPEAL_SUBMIT)


@inst.get("/api/applications/{application_id}/decision")
def official_status(application_id: uuid.UUID, request: Request, current: CurrentUser = Depends(can_read_own), db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> dict:
    """The official decision status of the institution's own application, with the appeal facility's state. Never internal records."""
    app = app_svc.get_owned(db, current.user, application_id, request)
    return dp.institution_view(db, app, settings)


@inst.post("/api/applications/{application_id}/appeal", status_code=201)
def submit_appeal(application_id: uuid.UUID, body: AppealIn, request: Request, response: Response, current: CurrentUser = Depends(can_appeal), db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> dict:
    _, replay = appeals.submit(db, current.user, application_id, body.grounds, body.idempotency_key, request, settings)
    db.commit()
    if replay:
        response.status_code = 200
        response.headers["X-Idempotent-Replay"] = "true"
    app = app_svc.get_owned(db, current.user, application_id, request)
    return dp.institution_view(db, app, settings)


@inst.post("/api/appeals/{appeal_id}/documents", status_code=201)
def appeal_file(
    appeal_id: uuid.UUID, request: Request, file: UploadFile = File(), description: str = Form(""), current: CurrentUser = Depends(can_appeal), db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings), storage: LocalStorage = Depends(get_storage),
) -> dict:
    """Supporting evidence for the institution's own appeal (private file; not malware-scanned or text-processed). It promises no outcome."""
    appeals.add_file(db, current.user, appeal_id, file, description, request, settings, storage)
    ap = db.get(Appeal, appeal_id)
    return dp.institution_view(db, db.get(Application, ap.application_id), settings)


@inst.post("/api/appeals/{appeal_id}/documents/link", status_code=201)
def appeal_link(appeal_id: uuid.UUID, body: AppealLinkIn, request: Request, current: CurrentUser = Depends(can_appeal), db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> dict:
    appeals.add_link(db, current.user, appeal_id, body.url, body.description, request)
    db.commit()
    ap = db.get(Appeal, appeal_id)
    return dp.institution_view(db, db.get(Application, ap.application_id), settings)


@inst.get("/api/appeals/documents/{document_id}/download")
def appeal_document_download(document_id: uuid.UUID, request: Request, current: CurrentUser = Depends(can_appeal), db: Session = Depends(get_db), storage: LocalStorage = Depends(get_storage)) -> StreamingResponse:
    doc = db.get(AppealDocument, document_id)
    ap = db.get(Appeal, doc.appeal_id) if doc else None
    if doc is None or ap is None or doc.kind != "FILE" or current.user.institution_id is None or ap.institution_id != current.user.institution_id:
        if doc is not None:
            audit.record(db, "appeal.access_denied", request=request, actor=current.user, target_type="appeal_document", target_id=doc.id)
            db.commit()
        raise not_found("Document")
    return _file_response(storage, doc.storage_key, doc.media_type or "application/octet-stream", doc.original_filename or "file")


_ = (select,)
