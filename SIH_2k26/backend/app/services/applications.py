"""Application intake: drafts, attachments and submission.

Every function takes the authenticated `user` and derives the institution scope from the
stored user record; the client never supplies an institution. Cross-institution access
answers "not found" and is audited.
"""

import re
import secrets
import uuid
from collections.abc import Iterator
from datetime import date

from fastapi import Request, UploadFile
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, selectinload

from app import audit
from app.config import Settings
from app.domain import ApplicationStatus, GapReason, ItemType, is_valid_academic_year
from app.models import Institution, User, utcnow
from app.models_applications import Application, ApplicationDocument, ApplicationEvent
from app.models_catalogue import ApplicationCategory
from app.services import checklist as cl
from app.services import rules
from app.services.errors import Issue, ServiceError, conflict, invalid, not_found
from app.storage import LocalStorage, StorageError

_ALPHABET = "ABCDEFGHJKMNPQRSTVWXYZ23456789"  # no I, L, O, U, 0, 1: unambiguous when read aloud or copied by hand


def _new_reference() -> str:
    pick = lambda n: "".join(secrets.choice(_ALPHABET) for _ in range(n))  # noqa: E731
    return f"APP-{pick(4)}-{pick(4)}"


def _event(db: Session, app: Application, actor: User, action: str, detail: dict | None = None, note: str | None = None) -> None:
    db.add(ApplicationEvent(application_id=app.id, actor_id=actor.id, action=action, detail=detail, note=note))


def _institution_of(user: User) -> uuid.UUID:
    if user.institution_id is None:
        raise ServiceError(403, "no_institution", "Your account is not linked to an institution, so you cannot work on applications.")
    return user.institution_id


# ---- access ------------------------------------------------------------------------------------


def get_owned(db: Session, user: User, application_id: uuid.UUID, request: Request | None, *, lock: bool = False) -> Application:
    q = (
        select(Application)
        .where(Application.id == application_id)
        .options(
            selectinload(Application.documents).selectinload(ApplicationDocument.processing),
            selectinload(Application.events).selectinload(ApplicationEvent.actor),
            selectinload(Application.category),
            selectinload(Application.institution),
            selectinload(Application.rule_version),
            selectinload(Application.created_by),
            selectinload(Application.submitted_by),
        )
    )
    if lock:
        q = q.with_for_update()
    # Sessions keep objects after commit, so refresh rows and collections from the database every time.
    app = db.scalars(q.execution_options(populate_existing=True)).one_or_none()
    if app is None:
        raise not_found("Application")
    if app.institution_id != user.institution_id:
        audit.record(db, "application.access_denied", request=request, actor=user, target_type="application", target_id=app.id)
        db.commit()
        raise not_found("Application")  # do not reveal that it exists
    return app


def config_of(app: Application) -> dict:
    """The frozen configuration this application is evaluated against."""
    if app.status == ApplicationStatus.SUBMITTED and app.submission_snapshot:
        return app.submission_snapshot["configuration"]
    snap = app.rule_version.snapshot
    if not snap:  # cannot happen for a bound version (only published versions are bound)
        raise ServiceError(500, "configuration_missing", "The rule configuration for this application is unavailable.")
    return snap


def active_documents(app: Application) -> dict[str, ApplicationDocument]:
    return {d.item_key: d for d in app.documents if d.removed_at is None and d.superseded_at is None}


def checklist_states(app: Application) -> list[cl.ItemState]:
    docs = {k: str(d.id) for k, d in active_documents(app).items()}
    return cl.evaluate(config_of(app), app.form_data or {}, docs)


# ---- create / list / update --------------------------------------------------------------------


def create_draft(db: Session, user: User, category_id: uuid.UUID, academic_year: str, request: Request | None, settings: Settings) -> Application:
    institution_id = _institution_of(user)
    if not is_valid_academic_year(academic_year):
        raise invalid("Choose a valid academic year (for example 2026-27).")
    category = db.get(ApplicationCategory, category_id)
    if category is None or not category.is_active:
        raise invalid("Choose an available application category.")

    res = rules.resolve_version(db, category.id, academic_year, date.today(), settings)
    if res.version is None:
        # Persist the gap even though the request fails, so reviewers can see that demand exists.
        rules.record_gap(db, category.id, academic_year, res.reason or GapReason.NO_APPROVED_VERSION, res.detail)
        audit.record(db, "rule.gap_reported", request=request, actor=user, target_type="category", target_id=category.id, detail={"academic_year": academic_year, "reason": (res.reason or GapReason.NO_APPROVED_VERSION).value})
        db.commit()
        raise ServiceError(
            409,
            "no_applicable_configuration",
            "No single approved configuration applies to this category and academic year, so the requirements and processing route cannot be determined. "
            "This has been reported to the rule configuration reviewers; no application was created. Nothing has been guessed.",
            [Issue((res.reason or GapReason.NO_APPROVED_VERSION).value, "See the rule configuration review queue.")],
        )

    for _ in range(5):  # reference collisions are astronomically unlikely, but the unique constraint is the arbiter
        ref = _new_reference()
        if not db.scalar(select(Application.id).where(Application.reference == ref)):
            break
    app = Application(
        reference=ref, institution_id=institution_id, created_by_id=user.id, category_id=category.id,
        academic_year=academic_year, rule_version_id=res.version.id, status=ApplicationStatus.DRAFT.value, form_data={},
    )
    db.add(app)
    db.flush()
    _event(db, app, user, "created", {"rule_set": res.version.rule_set.key, "rule_version": res.version.version_number})
    audit.record(db, "application.created", request=request, actor=user, target_type="application", target_id=app.id, detail={"reference": app.reference, "rule_version_id": str(res.version.id)})
    db.flush()
    return get_owned(db, user, app.id, request)


def list_applications(
    db: Session, user: User, *, status: str | None, category_id: uuid.UUID | None, academic_year: str | None, q: str | None, page: int, page_size: int
) -> tuple[list[Application], int]:
    institution_id = _institution_of(user)
    base = select(Application).where(Application.institution_id == institution_id)
    if status:
        base = base.where(Application.status == status)
    if category_id:
        base = base.where(Application.category_id == category_id)
    if academic_year:
        base = base.where(Application.academic_year == academic_year)
    if q:
        base = base.where(Application.reference.ilike(f"%{q.strip().upper().replace('%', '').replace('_', '')}%"))
    total = db.scalar(select(func.count()).select_from(base.subquery())) or 0
    rows = db.scalars(
        base.order_by(Application.created_at.desc()).limit(page_size).offset((page - 1) * page_size).options(
            selectinload(Application.documents).selectinload(ApplicationDocument.processing), selectinload(Application.category), selectinload(Application.rule_version)
        )
    ).all()
    return list(rows), total


def update_draft(db: Session, user: User, application_id: uuid.UUID, changes: dict, row_version: int, request: Request | None) -> Application:
    app = get_owned(db, user, application_id, request)
    if app.status != ApplicationStatus.DRAFT:
        raise conflict("not_draft", "This application has been submitted and can no longer be edited.")
    inputs = cl.input_items(config_of(app))
    issues: list[Issue] = []
    merged = dict(app.form_data or {})
    for key, value in changes.items():
        item = inputs.get(key)
        if item is None:
            issues.append(Issue("unknown_field", f"'{key}' is not a field of this application's checklist.", key))
        elif cl._blank(value):
            merged.pop(key, None)
        elif (err := cl.validate_value(item, value)) is not None:
            issues.append(Issue("invalid_value", err, key))
        else:
            merged[key] = value.strip() if isinstance(value, str) else value
    if issues:
        raise invalid("Some values were not accepted.", issues)

    # Compare-and-set: only succeeds if nobody changed or submitted the draft since the client loaded it.
    result = db.execute(
        update(Application)
        .where(Application.id == app.id, Application.row_version == row_version, Application.status == ApplicationStatus.DRAFT.value)
        .values(form_data=merged, row_version=row_version + 1, updated_at=utcnow())
    )
    if result.rowcount != 1:
        db.rollback()
        fresh = db.get(Application, application_id)
        if fresh is not None and fresh.status != ApplicationStatus.DRAFT:
            raise conflict("not_draft", "This application has been submitted and can no longer be edited.")
        raise conflict("stale_version", "This draft was changed elsewhere since you opened it. Reload to see the latest version before saving.")
    _event(db, app, user, "draft_updated", {"fields": sorted(changes)})
    audit.record(db, "application.draft_updated", request=request, actor=user, target_type="application", target_id=app.id, detail={"fields": sorted(changes)})
    db.flush()
    db.expire(app)
    return get_owned(db, user, app.id, request)


def rebind(db: Session, user: User, application_id: uuid.UUID, request: Request | None, settings: Settings) -> Application:
    """Explicitly move a draft to the configuration that now applies (after a newer version was published)."""
    app = get_owned(db, user, application_id, request)
    if app.status != ApplicationStatus.DRAFT:
        raise conflict("not_draft", "Only drafts can be moved to a newer configuration.")
    res = rules.resolve_version(db, app.category_id, app.academic_year, date.today(), settings)
    if res.version is None:
        raise ServiceError(409, "no_applicable_configuration", "No single approved configuration currently applies; the draft keeps its present requirements.")
    if res.version.id == app.rule_version_id:
        return app
    old = app.rule_version
    result = db.execute(update(Application).where(Application.id == app.id, Application.status == "DRAFT").values(rule_version_id=res.version.id, row_version=app.row_version + 1, updated_at=utcnow()))
    if result.rowcount != 1:
        raise conflict("not_draft", "This application can no longer be changed.")
    _event(db, app, user, "rule_version_changed", {"from": f"{old.rule_set.key} v{old.version_number}", "to": f"{res.version.rule_set.key} v{res.version.version_number}"})
    audit.record(db, "application.rule_version_changed", request=request, actor=user, target_type="application", target_id=app.id, detail={"from": str(old.id), "to": str(res.version.id)})
    db.flush()
    db.expire(app)
    return get_owned(db, user, app.id, request)


# ---- documents ---------------------------------------------------------------------------------

MAX_ACTIVE_DOCUMENTS = 100


def upload_document(db: Session, user: User, application_id: uuid.UUID, item_key: str, upload: UploadFile, request: Request | None, settings: Settings, storage: LocalStorage) -> Application:
    from app.services import documents  # local import: documents builds on this module

    return documents.upload_version(db, user, application_id, item_key, upload, request, settings, storage)[0]


def remove_document(db: Session, user: User, application_id: uuid.UUID, document_id: uuid.UUID, request: Request | None, settings: Settings | None = None) -> Application:
    from app.config import get_settings
    from app.services import documents

    return documents.remove_current(db, user, application_id, document_id, request, settings or get_settings())

def document_for_download(db: Session, user: User, application_id: uuid.UUID, document_id: uuid.UUID, request: Request | None) -> ApplicationDocument:
    app = get_owned(db, user, application_id, request)
    doc = next((d for d in app.documents if d.id == document_id), None)
    if doc is None:
        raise not_found("Document")
    audit.record(db, "application.document_downloaded", request=request, actor=user, target_type="application", target_id=app.id, detail={"document_id": str(doc.id)})
    db.flush()
    return doc


# ---- submission --------------------------------------------------------------------------------


def submit(db: Session, user: User, application_id: uuid.UUID, row_version: int, request: Request | None, settings: Settings) -> Application:
    app = get_owned(db, user, application_id, request, lock=True)
    if app.status == ApplicationStatus.SUBMITTED:
        raise ServiceError(409, "already_submitted", f"Application {app.reference} has already been submitted. It was not submitted a second time.")
    if app.row_version != row_version:
        raise conflict("stale_version", "This draft changed since you opened it. Reload and review it before submitting.")

    res = rules.resolve_version(db, app.category_id, app.academic_year, date.today(), settings)
    if res.version is None:
        raise ServiceError(409, "no_applicable_configuration", "No single approved configuration currently applies to this application, so it cannot be submitted. The reviewers have been notified through the configuration queue.")
    if res.version.id != app.rule_version_id:
        raise conflict("rule_version_changed", "A different approved configuration now applies to this category and year. Update the draft to the current requirements, review it, then submit.")

    states = checklist_states(app)
    missing = cl.outstanding_required(states)
    if missing:
        raise invalid(
            "The application cannot be submitted until every required item is supplied.",
            [Issue(s.status, s.error or f"“{s.item['label']}” is required.", s.item["item_key"]) for s in missing],
            code="submission_blocked",
        )

    now = utcnow()
    version = app.rule_version
    snapshot = {
        "schema": 1,
        "submitted_at": now.isoformat(),
        "rule_version": {"id": str(version.id), "rule_set_key": version.rule_set.key, "number": version.version_number, "content_hash": version.content_hash, "classification": version.rule_set.classification},
        "configuration": version.snapshot,
        "institution": {"id": str(app.institution.id), "name": app.institution.name, "external_identifier": app.institution.external_identifier},
        "category": {"id": str(app.category.id), "key": app.category.key, "name": app.category.name},
        "academic_year": app.academic_year,
        "form_data": dict(app.form_data or {}),
        "checklist": [s.as_dict() for s in states],
        "documents": [
            {"id": str(d.id), "item_key": d.item_key, "version": d.version_number, "filename": d.original_filename, "media_type": d.media_type, "size_bytes": d.size_bytes, "sha256": d.sha256, "uploaded_at": d.uploaded_at.isoformat()}
            for d in app.documents if d.removed_at is None and d.superseded_at is None
        ],
    }
    result = db.execute(
        update(Application)
        .where(Application.id == app.id, Application.status == "DRAFT", Application.row_version == row_version)
        .values(status="SUBMITTED", submitted_at=now, submitted_by_id=user.id, submission_snapshot=snapshot, row_version=row_version + 1, updated_at=now)
    )
    if result.rowcount != 1:
        db.rollback()
        raise ServiceError(409, "already_submitted", "This application was submitted or changed by another request. It was not submitted twice.")
    _event(db, app, user, "submitted", {"rule_set": version.rule_set.key, "rule_version": version.version_number, "content_hash": version.content_hash})
    audit.record(db, "application.submitted", request=request, actor=user, target_type="application", target_id=app.id, detail={"reference": app.reference, "rule_version_id": str(version.id), "content_hash": version.content_hash})
    db.flush()
    db.expire(app)
    submitted = get_owned(db, user, app.id, request)
    # Same transaction: a submitted application is always in the scrutiny queue (or, if its route has no scrutiny stage, in the exception queue).
    from app.services import scrutiny  # local import: scrutiny builds on this module

    scrutiny.open_case(db, submitted, user, request)
    return submitted


def institution_summary(inst: Institution) -> dict:
    return {"id": str(inst.id), "name": inst.name, "external_identifier": inst.external_identifier}
