"""Document versions, processing results and preliminary findings.

Access is decided per request from the stored user and the document's application: the owning
institution, or a reviewer (read-only, submitted applications only). Everything else is "not found".
"""

import uuid
from urllib.parse import quote

from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.responses import Response, StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app import audit, evidence_presenters as ep
from app.config import Settings, get_settings
from app.db import get_db
from app.deps import CurrentUser, csrf_protect, get_current_user, require_permission
from app.domain import ApplicationStatus, ExtractionStatus, ScanStatus
from app.models_applications import Application, ApplicationDocument
from app.models_processing import PreliminaryFinding, ProcessingJob
from app.processing import documents as docproc
from app.processing.errors import ProcessingError
from app.rbac import Permission
from app.schemas_evidence import ApplicationEvidenceOut, DocumentDetailOut, EventOut, ExtractionOut, ReviewListItem, VersionOut
from app.services import applications as app_svc
from app.services import documents as docs_svc
from app.services.errors import ServiceError, not_found
from app.storage import LocalStorage, StorageError, get_storage

router = APIRouter(tags=["documents"], dependencies=[Depends(csrf_protect)])


def _job(db: Session, doc: ApplicationDocument) -> ProcessingJob | None:
    return db.scalar(select(ProcessingJob).where(ProcessingJob.document_id == doc.id, ProcessingJob.job_type == "PROCESS_DOCUMENT"))


def _read(doc: ApplicationDocument, storage: LocalStorage) -> bytes:
    if doc.processing is not None and doc.processing.scan_status == ScanStatus.INFECTED:
        raise ServiceError(409, "quarantined", "This file was flagged by the malware scanner and is quarantined.")
    try:
        with storage.open(doc.storage_key) as fh:
            return fh.read()
    except (OSError, StorageError) as exc:
        raise ServiceError(503, "storage_unavailable", "The stored file could not be read just now. Please try again.") from exc


# ---- by document --------------------------------------------------------------------------------


@router.get("/api/documents/{document_id}", response_model=DocumentDetailOut)
def get_document(document_id: uuid.UUID, request: Request, current: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)) -> DocumentDetailOut:
    acc = docs_svc.resolve_access(db, current.user, document_id, request)
    return ep.document_detail(db, acc.doc, acc.app, acc.kind)


@router.get("/api/documents/{document_id}/download")
def download_document(document_id: uuid.UUID, request: Request, current: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db), storage: LocalStorage = Depends(get_storage)) -> StreamingResponse:
    acc = docs_svc.resolve_access(db, current.user, document_id, request)
    data = _read(acc.doc, storage)
    audit.record(db, "document.downloaded", request=request, actor=current.user, target_type="document", target_id=acc.doc.id, detail={"viewer": acc.kind})
    db.commit()
    return StreamingResponse(
        iter([data]), media_type=acc.doc.media_type,
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(acc.doc.original_filename)}", "Content-Length": str(len(data)),
            "X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store",
        },
    )


@router.get("/api/documents/{document_id}/pages/{page}/preview")
def preview_page(document_id: uuid.UUID, page: int, request: Request, current: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db), storage: LocalStorage = Depends(get_storage), settings: Settings = Depends(get_settings)) -> Response:
    """A rasterised PNG of one page. Pages are rendered, never served as a PDF, so no document code runs in the browser."""
    acc = docs_svc.resolve_access(db, current.user, document_id, request)
    if acc.doc.processing is not None and acc.doc.processing.technical_status == "FAILED":
        raise ServiceError(409, "file_unreadable", "This file could not be read, so it cannot be previewed.")
    data = _read(acc.doc, storage)
    try:
        png = docproc.render_preview(data, acc.doc.media_type, page, settings)
    except ProcessingError as exc:
        raise ServiceError(404 if "range" in exc.detail else 422, exc.code, exc.info.message) from exc
    return Response(png, media_type="image/png", headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})


@router.get("/api/documents/{document_id}/extraction", response_model=ExtractionOut)
def get_extraction(document_id: uuid.UUID, request: Request, current: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)) -> ExtractionOut:
    acc = docs_svc.resolve_access(db, current.user, document_id, request)
    return ep.extraction(db, acc.doc, _job(db, acc.doc))


@router.get("/api/documents/{document_id}/history", response_model=list[EventOut])
def get_history(document_id: uuid.UUID, request: Request, current: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)) -> list[EventOut]:
    acc = docs_svc.resolve_access(db, current.user, document_id, request)
    return ep.history(db, acc.doc)


@router.post("/api/documents/{document_id}/retry", response_model=ExtractionOut, status_code=status.HTTP_202_ACCEPTED)
def retry_processing(document_id: uuid.UUID, request: Request, current: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> ExtractionOut:
    acc = docs_svc.resolve_access(db, current.user, document_id, request)
    docs_svc.request_retry(db, current.user, acc, request, settings)
    db.commit()
    db.refresh(acc.doc)
    return ep.extraction(db, acc.doc, _job(db, acc.doc))


# ---- by application -----------------------------------------------------------------------------


@router.get("/api/applications/{application_id}/evidence", response_model=ApplicationEvidenceOut)
def own_evidence(application_id: uuid.UUID, request: Request, current: CurrentUser = Depends(require_permission(Permission.APPLICATION_READ_OWN)), db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> ApplicationEvidenceOut:
    app = app_svc.get_owned(db, current.user, application_id, request)
    return ep.application_evidence(db, app, "owner", settings)


@router.get("/api/applications/{application_id}/documents", response_model=list[VersionOut])
def list_versions(application_id: uuid.UUID, request: Request, current: CurrentUser = Depends(require_permission(Permission.APPLICATION_READ_OWN)), db: Session = Depends(get_db)) -> list[VersionOut]:
    """Every file version of the application, oldest first, with its processing state. Earlier versions are kept for the record."""
    app = app_svc.get_owned(db, current.user, application_id, request)
    return ep.versions_for_application(db, app)


@router.get("/api/review/applications", response_model=list[ReviewListItem])
def review_list(current: CurrentUser = Depends(require_permission(Permission.EVIDENCE_READ)), db: Session = Depends(get_db), limit: int = Query(100, ge=1, le=200)) -> list[ReviewListItem]:
    """Submitted applications. Phase 3 has no assignment mechanism, so every reviewer sees every submitted application."""
    apps = db.scalars(
        select(Application).where(Application.status == ApplicationStatus.SUBMITTED.value).order_by(Application.submitted_at.desc()).limit(limit)
        .options(selectinload(Application.institution), selectinload(Application.category))
    ).all()
    out: list[ReviewListItem] = []
    for a in apps:
        docs = ep.current_documents(db, a.id)
        procs = [d.processing for d in docs.values() if d.processing is not None]
        open_f = db.scalar(select(func.count()).select_from(PreliminaryFinding).where(PreliminaryFinding.application_id == a.id, PreliminaryFinding.superseded_at.is_(None), PreliminaryFinding.priority != "info")) or 0
        out.append(ReviewListItem(
            id=a.id, reference=a.reference, institution_name=a.institution.name, category_name=a.category.name, academic_year=a.academic_year, submitted_at=a.submitted_at,
            documents=len(docs), still_processing=sum(1 for p in procs if p.extraction_status in (ExtractionStatus.QUEUED, ExtractionStatus.RUNNING)),
            review_recommended=sum(1 for p in procs if p.review_recommended), open_findings=open_f,
        ))
    return out


@router.get("/api/review/applications/{application_id}/evidence", response_model=ApplicationEvidenceOut)
def review_evidence(application_id: uuid.UUID, request: Request, current: CurrentUser = Depends(require_permission(Permission.EVIDENCE_READ)), db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> ApplicationEvidenceOut:
    app = db.scalars(select(Application).where(Application.id == application_id).options(selectinload(Application.institution), selectinload(Application.category), selectinload(Application.rule_version))).one_or_none()
    if app is None or app.status != ApplicationStatus.SUBMITTED:
        raise not_found("Application")  # drafts are the institution's own work in progress
    audit.record(db, "evidence.viewed", request=request, actor=current.user, target_type="application", target_id=app.id)
    db.commit()
    return ep.application_evidence(db, app, "reviewer", settings)

