import re
import uuid
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, Query, Request, Response, UploadFile, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app import presenters
from app.config import Settings, get_settings
from app.db import get_db
from app.deps import CurrentUser, csrf_protect, require_permission
from app.rbac import Permission
from app.schemas_applications import (
    ApplicationCreateIn,
    ApplicationDetailOut,
    ApplicationPage,
    ApplicationSubmitIn,
    ApplicationUpdateIn,
)
from app.services import applications as svc
from app.services import documents as docs_svc
from app.services.errors import invalid
from app.storage import LocalStorage, get_storage

router = APIRouter(prefix="/api/applications", tags=["applications"], dependencies=[Depends(csrf_protect)])

can_create = require_permission(Permission.APPLICATION_CREATE)
can_read = require_permission(Permission.APPLICATION_READ_OWN)
can_update = require_permission(Permission.APPLICATION_UPDATE_OWN)
can_submit = require_permission(Permission.APPLICATION_SUBMIT)

_ITEM_KEY = re.compile(r"^[a-z0-9][a-z0-9_-]{0,79}$")


def _detail(db: Session, app, settings: Settings) -> ApplicationDetailOut:
    return presenters.application_detail(db, app, settings)


@router.post("", response_model=ApplicationDetailOut, status_code=status.HTTP_201_CREATED)
def create(body: ApplicationCreateIn, request: Request, current: CurrentUser = Depends(can_create), db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> ApplicationDetailOut:
    app = svc.create_draft(db, current.user, body.category_id, body.academic_year, request, settings)
    db.commit()
    return _detail(db, app, settings)


@router.get("", response_model=ApplicationPage)
def list_(
    current: CurrentUser = Depends(can_read),
    db: Session = Depends(get_db),
    status_: str | None = Query(None, alias="status", pattern="^(DRAFT|SUBMITTED)$"),
    category_id: uuid.UUID | None = None,
    academic_year: str | None = Query(None, max_length=9),
    q: str | None = Query(None, max_length=40),
    page: int = Query(1, ge=1, le=10_000),
    page_size: int = Query(20, ge=1, le=50),
) -> ApplicationPage:
    rows, total = svc.list_applications(db, current.user, status=status_, category_id=category_id, academic_year=academic_year, q=q, page=page, page_size=page_size)
    return ApplicationPage(items=[presenters.application_summary(a) for a in rows], total=total, page=page, page_size=page_size)


@router.get("/{application_id}", response_model=ApplicationDetailOut)
def get(application_id: uuid.UUID, request: Request, current: CurrentUser = Depends(can_read), db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> ApplicationDetailOut:
    return _detail(db, svc.get_owned(db, current.user, application_id, request), settings)


@router.patch("/{application_id}", response_model=ApplicationDetailOut)
def update(application_id: uuid.UUID, body: ApplicationUpdateIn, request: Request, current: CurrentUser = Depends(can_update), db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> ApplicationDetailOut:
    app = svc.update_draft(db, current.user, application_id, body.form_data, body.row_version, request)
    db.commit()
    return _detail(db, app, settings)


@router.post("/{application_id}/rebind", response_model=ApplicationDetailOut)
def rebind(application_id: uuid.UUID, request: Request, current: CurrentUser = Depends(can_update), db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> ApplicationDetailOut:
    app = svc.rebind(db, current.user, application_id, request, settings)
    db.commit()
    return _detail(db, app, settings)


@router.post("/{application_id}/documents", response_model=ApplicationDetailOut, status_code=status.HTTP_201_CREATED)
def upload(
    application_id: uuid.UUID,
    request: Request,
    response: Response,
    item_key: str = Form(),
    file: UploadFile = File(),
    current: CurrentUser = Depends(can_update),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    storage: LocalStorage = Depends(get_storage),
) -> ApplicationDetailOut:
    if not _ITEM_KEY.fullmatch(item_key):
        raise invalid("Invalid checklist item.")
    app, outcome = docs_svc.upload_version(db, current.user, application_id, item_key, file, request, settings, storage)
    if outcome == "duplicate":  # identical bytes to the current version: nothing new was created
        response.status_code = status.HTTP_200_OK
        response.headers["X-Upload-Result"] = "duplicate_of_current"
    return _detail(db, app, settings)


@router.delete("/{application_id}/documents/{document_id}", response_model=ApplicationDetailOut)
def remove_document(application_id: uuid.UUID, document_id: uuid.UUID, request: Request, current: CurrentUser = Depends(can_update), db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> ApplicationDetailOut:
    app = svc.remove_document(db, current.user, application_id, document_id, request, get_settings())
    db.commit()
    return _detail(db, app, settings)


@router.get("/{application_id}/documents/{document_id}/download")
def download(application_id: uuid.UUID, document_id: uuid.UUID, request: Request, current: CurrentUser = Depends(can_read), db: Session = Depends(get_db), storage: LocalStorage = Depends(get_storage)) -> StreamingResponse:
    doc = svc.document_for_download(db, current.user, application_id, document_id, request)
    db.commit()

    def stream():
        with storage.open(doc.storage_key) as fh:
            while chunk := fh.read(1024 * 1024):
                yield chunk

    return StreamingResponse(
        stream(),
        media_type=doc.media_type,
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(doc.original_filename)}",
            "Content-Length": str(doc.size_bytes),
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
        },
    )


@router.post("/{application_id}/submit", response_model=ApplicationDetailOut)
def submit(application_id: uuid.UUID, body: ApplicationSubmitIn, request: Request, current: CurrentUser = Depends(can_submit), db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> ApplicationDetailOut:
    app = svc.submit(db, current.user, application_id, body.row_version, request, settings)
    db.commit()
    return _detail(db, app, settings)
