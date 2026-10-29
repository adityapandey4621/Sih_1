"""Versioned document upload, removal, access and retry.

Every upload is a new immutable *version* in the slot for its checklist item. Earlier versions are
never overwritten or deleted. The file, its metadata, its processing row and its queued job are
committed together: a browser failure after the commit cannot lose the document, and a failure
before it leaves nothing behind (the stored file is deleted).
"""

import re
import uuid
from collections.abc import Callable

from fastapi import Request, UploadFile
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app import audit, jobs, scrutiny_access
from app.config import Settings
from app.domain import ApplicationStatus, ItemType
from app.evidence import reset_processing_row
from app.models import User, utcnow
from app.models_applications import Application, ApplicationDocument, ApplicationEvent
from app.models_processing import DocumentSlot
from app.processing.documents import sniff
from app.rbac import Permission, permissions_for
from app.services.applications import MAX_ACTIVE_DOCUMENTS, config_of, get_owned
from app.services.errors import ServiceError, conflict, invalid, not_found
from app.storage import LocalStorage, StorageError

_CHUNK = 1024 * 1024


def _clean_filename(name: str | None) -> str:
    base = re.split(r"[\\/]", name or "")[-1]
    base = re.sub(r"[\x00-\x1f\x7f]", "", base).strip().strip(".")
    return (base or "file")[:255]


def _read_limited(upload: UploadFile, limit: int):
    total = 0
    while chunk := upload.file.read(_CHUNK):
        total += len(chunk)
        if total > limit:
            raise ServiceError(413, "file_too_large", f"The file is larger than the {limit // (1024 * 1024)} MB limit.")
        yield chunk


def _reject(db: Session, request: Request | None, user: User, app: Application, reason: str, item_key: str) -> None:
    audit.record(db, "application.upload_rejected", request=request, actor=user, target_type="application", target_id=app.id, detail={"reason": reason, "item_key": item_key})
    db.commit()


def _app_event(db: Session, app: Application, actor: User, action: str, detail: dict | None = None) -> None:
    db.add(ApplicationEvent(application_id=app.id, actor_id=actor.id, action=action, detail=detail))


def upload_version(
    db: Session, user: User, application_id: uuid.UUID, item_key: str, upload: UploadFile, request: Request | None, settings: Settings, storage: LocalStorage,
    *, correction: bool = False, on_stored: Callable[[ApplicationDocument], None] | None = None,
) -> tuple[Application, str]:
    """-> (application, "created" | "duplicate"). `duplicate` means identical bytes to the current version: nothing changed.

    `correction=True` is the one way a *submitted* application gains a document version: corrected evidence for an issued
    deficiency notice (the caller has already checked the notice is open). The same validation, storage, versioning and
    processing pipeline applies. `on_stored` runs in the same transaction, so the notice-response link and the version
    are saved together or not at all.
    """
    app = get_owned(db, user, application_id, request)
    if correction:
        if app.status != ApplicationStatus.SUBMITTED:
            raise conflict("not_submitted", "Corrected evidence can only be supplied for a submitted application.")
        db.info["correction_upload"] = True
    elif app.status != ApplicationStatus.DRAFT:
        raise conflict("not_draft", "Documents cannot be added or replaced after submission.")
    try:
        return _store_version(db, user, app, application_id, item_key, upload, request, settings, storage, on_stored)
    finally:
        db.info.pop("correction_upload", None)


def _store_version(
    db: Session, user: User, app: Application, application_id: uuid.UUID, item_key: str, upload: UploadFile, request: Request | None, settings: Settings, storage: LocalStorage,
    on_stored: Callable[[ApplicationDocument], None] | None,
) -> tuple[Application, str]:
    item = next((i for i in config_of(app)["checklist"] if i["item_key"] == item_key), None)
    if item is None or item["item_type"] != ItemType.DOCUMENT_UPLOAD:
        raise invalid("That checklist item does not take a file upload.")
    if len(app.documents) >= MAX_ACTIVE_DOCUMENTS * 3:
        raise invalid("This application has reached the maximum number of attachments.")

    filename = _clean_filename(upload.filename)
    head = upload.file.read(16)
    upload.file.seek(0)
    kind = sniff(head)
    ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if kind is None:
        _reject(db, request, user, app, "type_not_allowed", item_key)
        raise ServiceError(415, "type_not_allowed", "Only PDF, PNG and JPEG files are accepted, and the file's content must match its type.")
    media, exts = kind
    declared = (upload.content_type or "").split(";")[0].strip().lower()
    if ext not in exts or (declared and declared not in (media, "application/octet-stream")):
        _reject(db, request, user, app, "type_mismatch", item_key)
        raise ServiceError(415, "type_mismatch", "The file's name, declared type and content do not agree. Please upload the original PDF, PNG or JPEG file.")

    key = storage.new_key()
    try:
        size, digest = storage.save(key, _read_limited(upload, settings.max_upload_bytes))
    except ServiceError:
        _reject(db, request, user, app, "too_large", item_key)
        raise
    except (StorageError, OSError) as exc:
        audit.record(db, "application.upload_failed", request=request, actor=user, target_type="application", target_id=app.id, detail={"item_key": item_key, "error": type(exc).__name__})
        db.commit()
        raise ServiceError(503, "storage_unavailable", "The file could not be stored. Nothing was saved; please try again.") from exc
    if size == 0:
        storage.delete(key)
        raise invalid("The file is empty.")

    try:
        slot = db.scalar(select(DocumentSlot).where(DocumentSlot.application_id == app.id, DocumentSlot.item_key == item_key).with_for_update() if db.bind.dialect.name == "postgresql" else select(DocumentSlot).where(DocumentSlot.application_id == app.id, DocumentSlot.item_key == item_key))
        if slot is None:
            slot = DocumentSlot(application_id=app.id, item_key=item_key)
            db.add(slot)
            db.flush()
        current = db.get(ApplicationDocument, slot.current_version_id, populate_existing=True) if slot.current_version_id else None
        if current is not None and current.removed_at is None and current.sha256 == digest:
            # Same bytes as the current version (a double-click, a retried request): no new version, no new job.
            storage.delete(key)
            audit.record(db, "application.document_duplicate_ignored", request=request, actor=user, target_type="application", target_id=app.id, detail={"item_key": item_key, "document_id": str(current.id)})
            if on_stored is not None:
                on_stored(current)  # the response may rely on the version already in place
            db.commit()
            db.expire_all()
            return get_owned(db, user, application_id, request), "duplicate"

        now = utcnow()
        latest = db.scalar(select(func.max(ApplicationDocument.version_number)).where(ApplicationDocument.slot_id == slot.id)) or 0
        previous_id = db.scalar(select(ApplicationDocument.id).where(ApplicationDocument.slot_id == slot.id, ApplicationDocument.version_number == latest)) if latest else None
        if current is not None and current.removed_at is None:
            current.superseded_at = now
            jobs.cancel_waiting_for_documents(db, [current.id], "A newer version was uploaded.")
            jobs.add_event(db, current.id, "superseded", {"by_version": latest + 1}, user.id)
        db.flush()  # free the one-current-version-per-slot index before inserting the replacement
        doc = ApplicationDocument(
            application_id=app.id, item_key=item_key, original_filename=filename, storage_key=key, media_type=media, size_bytes=size, sha256=digest,
            uploaded_by_id=user.id, slot_id=slot.id, version_number=latest + 1, previous_version_id=previous_id,
        )
        db.add(doc)
        db.flush()
        slot.current_version_id = doc.id
        reset_processing_row(db, doc)
        jobs.enqueue_document_job(db, doc, settings, now)
        jobs.enqueue_cross_check(db, app.id, settings, now)
        jobs.add_event(db, doc.id, "uploaded", {"version": doc.version_number, "size": size, "media_type": media}, user.id)
        _app_event(db, app, user, "document_uploaded", {"item_key": item_key, "document_id": str(doc.id), "version": doc.version_number, "sha256": digest, "size": size})
        audit.record(db, "application.document_uploaded", request=request, actor=user, target_type="application", target_id=app.id, detail={"item_key": item_key, "document_id": str(doc.id), "version": doc.version_number, "size": size, "correction": bool(db.info.get("correction_upload"))})
        if on_stored is not None:
            on_stored(doc)
        db.commit()
    except BaseException:
        db.rollback()
        storage.delete(key)  # no orphaned file when the records could not be saved
        raise
    db.expire_all()
    return get_owned(db, user, application_id, request), "created"


def remove_current(db: Session, user: User, application_id: uuid.UUID, document_id: uuid.UUID, request: Request | None, settings: Settings) -> Application:
    app = get_owned(db, user, application_id, request)
    if app.status != ApplicationStatus.DRAFT:
        raise conflict("not_draft", "Documents cannot be removed after submission.")
    doc = next((d for d in app.documents if d.id == document_id and d.removed_at is None and d.superseded_at is None), None)
    if doc is None:
        raise not_found("Document")
    now = utcnow()
    doc.removed_at, doc.removed_by_id = now, user.id  # the record of what was uploaded stays
    slot = db.get(DocumentSlot, doc.slot_id)
    slot.current_version_id = None
    jobs.cancel_waiting_for_documents(db, [doc.id], "The file was removed.")
    jobs.add_event(db, doc.id, "removed", None, user.id)
    jobs.enqueue_cross_check(db, app.id, settings, now)
    _app_event(db, app, user, "document_removed", {"item_key": doc.item_key, "document_id": str(doc.id)})
    audit.record(db, "application.document_removed", request=request, actor=user, target_type="application", target_id=app.id, detail={"item_key": doc.item_key, "document_id": str(doc.id)})
    db.flush()
    return get_owned(db, user, application_id, request)


# ---- access: owners and reviewers ---------------------------------------------------------------


class Access:
    def __init__(self, doc: ApplicationDocument, app: Application, kind: str) -> None:
        self.doc, self.app, self.kind = doc, app, kind  # kind: owner | reviewer

    @property
    def is_reviewer(self) -> bool:
        return self.kind == "reviewer"


def resolve_access(db: Session, user: User, document_id: uuid.UUID, request: Request | None) -> Access:
    """Who may see this file? Its own institution, or a reviewer (read-only) once the application is submitted."""
    doc = db.scalar(select(ApplicationDocument).where(ApplicationDocument.id == document_id).options(selectinload(ApplicationDocument.processing)))
    if doc is None:
        raise not_found("Document")
    app = db.scalars(select(Application).where(Application.id == doc.application_id).options(selectinload(Application.institution), selectinload(Application.rule_version), selectinload(Application.category))).one()
    perms = permissions_for(user.role)
    if Permission.APPLICATION_READ_OWN in perms and user.institution_id is not None and app.institution_id == user.institution_id:
        return Access(doc, app, "owner")
    # Reviewer-side access is limited to cases the user is assigned to (reviewer) or has been asked to decide on (issuer).
    if app.status == ApplicationStatus.SUBMITTED and scrutiny_access.can_read_application_documents(db, user, app):
        return Access(doc, app, "reviewer")
    audit.record(db, "document.access_denied", request=request, actor=user, target_type="document", target_id=doc.id)
    db.commit()
    raise not_found("Document")


def request_retry(db: Session, user: User, access: Access, request: Request | None, settings: Settings) -> None:
    if Permission.DOCUMENT_RETRY not in permissions_for(user.role):
        raise ServiceError(403, "forbidden", "You may not request a retry.")
    try:
        job = jobs.request_retry(db, access.doc, settings, user.id)
    except jobs.RetryNotAllowed as exc:
        raise ServiceError(409, exc.code, exc.message) from exc
    audit.record(db, "document.retry_requested", request=request, actor=user, target_type="document", target_id=access.doc.id, detail={"job_id": str(job.id), "manual_retries": job.manual_retries})
