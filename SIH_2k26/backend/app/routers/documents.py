"""Document versions, processing results and preliminary findings.

Access is decided per request from the stored user and the document's application: the owning
institution, or a reviewer (read-only, submitted applications only). Everything else is "not found".
"""

import uuid
from urllib.parse import quote

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import Response, StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit, evidence_presenters as ep
from app.config import Settings, get_settings
from app.db import get_db
from app.deps import CurrentUser, csrf_protect, get_current_user, require_permission
from app.domain import ScanStatus
from app.models_applications import ApplicationDocument
from app.models_processing import ProcessingJob
from app.processing import documents as docproc
from app.processing.errors import ProcessingError
from app.rbac import Permission
from app.schemas_evidence import ApplicationEvidenceOut, DocumentDetailOut, EventOut, ExtractionOut, VersionOut
from app.services import applications as app_svc
from app.services import documents as docs_svc
from app.services.errors import ServiceError
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


# Reviewer-side listing and evidence now live under /api/scrutiny (scoped to assigned cases): see routers/scrutiny.py.

