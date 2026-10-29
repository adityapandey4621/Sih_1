"""Document validity: expiry dates read from documents, claimed in renewals, and CONFIRMED by a person.

Three different things, kept apart:
  EXTRACTED  a date the processing layer read from a submitted document (with its document version, page, method and confidence). A candidate.
  DECLARED   a date the institution states with a renewal upload. A claim.
  VERIFIED   a date a person holding `compliance:verify` confirmed against the document. Only a verified date creates an expiry clock and its alerts.

An uncertain read, a missing date, or a date that conflicts with another one is NEEDS_REVIEW: a person must look, and no expiry alert is raised from it.
A renewal upload is a new version. It does NOT cancel the alerts of the version it renews: those end only when the renewal is verified (or an authorised person
records that the item no longer applies, with a reason). Nothing in this module revokes or penalises anything.

Which fields count as validity dates is configuration, not code: an extraction field of kind `date` flagged `validity` in the frozen checklist.
The expiry moment is the END of the stated calendar date in UTC; the official convention for "valid until" is not verified (see docs/COMPLIANCE_AND_DEADLINES.md).
"""

import uuid
from datetime import UTC, date, datetime, time

from fastapi import Request, UploadFile
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import audit
from app.config import Settings
from app.domain import VALIDITY_TRIGGER, ClockKind, NotificationEvent, ValiditySource, ValidityStatus
from app.models import User, utcnow
from app.models_applications import Application, ApplicationDocument
from app.models_compliance import ValidityItem, ValidityVersion
from app.models_processing import DocumentProcessing, ExtractedField
from app.rbac import Permission, Role, permissions_for
from app.services import clock_policy, clocks, notifications, private_files, scheduler
from app.services.applications import config_of
from app.services.errors import Issue, conflict, invalid, not_found
from app.storage import LocalStorage

MIN_YEAR, MAX_YEAR = 2000, 2100
_PROCESSED = ("COMPLETE", "PARTIAL")


def validity_fields(config: dict) -> list[tuple[str, str, str]]:
    """(item_key, field_key, label) for every extraction field flagged as a validity date in the frozen configuration."""
    out = []
    for item in config.get("checklist", []):
        for f in item.get("extraction") or []:
            if f.get("validity") and f.get("kind") == "date":
                out.append((item["item_key"], f["field_key"], f"{item['label']} — {f['label']}"))
    return out


def expiry_instant(d: date) -> datetime:
    return datetime.combine(d, time(23, 59, 59), tzinfo=UTC)


def in_range(d: date) -> bool:
    return MIN_YEAR <= d.year <= MAX_YEAR


# ---- candidates from submitted documents -------------------------------------------------------------------


def _next_number(db: Session, item_id: uuid.UUID) -> int:
    return (db.scalar(select(func.max(ValidityVersion.version_number)).where(ValidityVersion.item_id == item_id)) or 0) + 1


def sync_candidates(db: Session, settings: Settings) -> int:
    """Create (once) a candidate for each processed document version that carries a configured validity field. Idempotent."""
    made = 0
    for app in db.scalars(select(Application).where(Application.status == "SUBMITTED")).all():
        fields = validity_fields(config_of(app))
        if not fields:
            continue
        for item_key, field_key, label in fields:
            docs = db.scalars(select(ApplicationDocument).where(ApplicationDocument.application_id == app.id, ApplicationDocument.item_key == item_key, ApplicationDocument.removed_at.is_(None)).order_by(ApplicationDocument.version_number)).all()
            if not docs:
                continue
            item = db.scalar(select(ValidityItem).where(ValidityItem.application_id == app.id, ValidityItem.item_key == item_key, ValidityItem.field_key == field_key))
            for doc in docs:
                proc = db.scalar(select(DocumentProcessing).where(DocumentProcessing.document_id == doc.id))
                if proc is None or proc.extraction_status not in _PROCESSED:
                    continue  # not read yet; a failed or pending read creates no candidate
                if item is not None and db.scalar(select(ValidityVersion.id).where(ValidityVersion.item_id == item.id, ValidityVersion.source_document_id == doc.id)) is not None:
                    continue
                if item is None:
                    item = ValidityItem(application_id=app.id, institution_id=app.institution_id, rule_version_id=app.rule_version_id, item_key=item_key, field_key=field_key, label=label)
                    db.add(item)
                    db.flush()
                made += _candidate(db, settings, app, item, doc)
    db.flush()
    return made


def _candidate(db: Session, settings: Settings, app: Application, item: ValidityItem, doc: ApplicationDocument) -> int:
    f = db.scalar(select(ExtractedField).where(ExtractedField.document_id == doc.id, ExtractedField.field_key == item.field_key))
    value: date | None = None
    flag: str | None = None
    if f is None or f.status == "NOT_FOUND":
        flag = "No date could be read from this document version. A person must read it."
    elif f.status != "EXTRACTED" or not f.normalised_value:
        flag = "The date was read uncertainly. A person must read it."
    else:
        try:
            value = date.fromisoformat(f.normalised_value)
        except ValueError:
            flag = "The date could not be understood. A person must read it."
        else:
            if not in_range(value):
                flag, value = f"The date read ({value.isoformat()}) is outside a plausible range. A person must read it.", None
            elif f.confidence is not None and f.confidence < settings.ocr_min_confidence:
                flag = "The date was read with low confidence. A person must confirm it."
    # a date that differs from another live candidate or the verified date is a conflict, not an update
    others = db.scalars(select(ValidityVersion).where(ValidityVersion.item_id == item.id, ValidityVersion.status.in_(("UNVERIFIED", "NEEDS_REVIEW", "VERIFIED")))).all()
    conflict_with = [o for o in others if value is not None and (o.verified_date or o.value_date) not in (None, value)]
    conflict_info = None
    if conflict_with:
        flag = flag or "This date differs from another recorded date for the same document. A person must decide which is right."
        conflict_info = {"with_versions": [o.version_number for o in conflict_with], "dates": [(o.verified_date or o.value_date).isoformat() for o in conflict_with]}
    status = ValidityStatus.NEEDS_REVIEW if flag else ValidityStatus.UNVERIFIED
    v = ValidityVersion(
        item_id=item.id, version_number=_next_number(db, item.id), source=ValiditySource.EXTRACTED.value, status=status.value, value_date=value, raw_value=f.value if f else None,
        source_document_id=doc.id, source_document_version=doc.version_number, source_page=f.page_number if f else None, source_method=f.method if f else None, source_confidence=f.confidence if f else None,
        source_evidence=f.evidence if f else None, review_reason_flag=flag, conflict=conflict_info,
    )
    db.add(v)
    db.flush()
    officers = notifications.users_with_role(db, Role.COMPLIANCE_OFFICER)
    notifications.notify(db, NotificationEvent.VALIDITY_NEEDS_REVIEW, officers, kind="compliance_officer", scope_key=f"validity:{v.id}", title=f"Document date to verify: {item.label}",
                         body=f"Application {app.reference}: a date was {'read from' if value else 'looked for in'} the submitted document and needs a person to confirm it. It is not yet used for any expiry alert.",
                         link="/app/post-approval?tab=validity", application_id=app.id)
    return 1


# ---- access ---------------------------------------------------------------------------------------------


def get_item(db: Session, user: User, item_id: uuid.UUID, request: Request | None) -> ValidityItem:
    it = db.get(ValidityItem, item_id, populate_existing=True)
    perms = permissions_for(user.role)
    ok = False
    if it is not None:
        if Permission.COMPLIANCE_READ_OWN in perms:
            ok = user.institution_id is not None and it.institution_id == user.institution_id
        elif Permission.COMPLIANCE_QUEUE in perms:
            ok = True
    if not ok:
        if it is not None:
            audit.record(db, "compliance.access_denied", request=request, actor=user, target_type="validity_item", target_id=it.id)
            db.commit()
        raise not_found("Document")
    return it


# ---- renewals ---------------------------------------------------------------------------------------------


def renew(db: Session, user: User, item_id: uuid.UUID, declared: date, key: str, upload: UploadFile, request: Request | None, settings: Settings, storage: LocalStorage) -> tuple[ValidityVersion, bool]:
    item = get_item(db, user, item_id, request)
    if Permission.COMPLIANCE_SUBMIT not in permissions_for(user.role):
        raise not_found("Document")
    prior = db.scalar(select(ValidityVersion).where(ValidityVersion.idempotency_key == key))
    if prior is not None:
        if prior.item_id != item.id:
            raise conflict("idempotency_key_reused", "That request key was already used for something else.")
        return prior, True
    if item.status != "ACTIVE":
        raise conflict("item_resolved", "This document no longer needs renewal.")
    if not in_range(declared):
        raise invalid("That date is not plausible.", [Issue("declared_date", f"Enter a date between {MIN_YEAR} and {MAX_YEAR}.", "declared_date")])
    pending = db.scalar(select(ValidityVersion.id).where(ValidityVersion.item_id == item.id, ValidityVersion.source == ValiditySource.DECLARED.value, ValidityVersion.status.in_(("UNVERIFIED", "NEEDS_REVIEW"))))
    if pending is not None:
        raise conflict("renewal_pending", "A renewal is already waiting for verification. Wait for its outcome before sending another.")
    stored = private_files.store(upload, settings, storage)
    try:
        v = ValidityVersion(
            item_id=item.id, version_number=_next_number(db, item.id), source=ValiditySource.DECLARED.value, status=ValidityStatus.UNVERIFIED.value, value_date=declared, raw_value=declared.isoformat(),
            storage_key=stored.key, original_filename=stored.name, media_type=stored.media_type, size_bytes=stored.size, sha256=stored.sha256, scan_status=stored.scan_status, uploaded_by_id=user.id, idempotency_key=key,
        )
        db.add(v)
        db.flush()
        audit.record(db, "validity.renewal_uploaded", request=request, actor=user, target_type="validity_item", target_id=item.id, detail={"version": v.version_number})
        app = db.get(Application, item.application_id)
        notifications.notify(db, NotificationEvent.VALIDITY_RENEWAL_UPLOADED, notifications.users_with_role(db, Role.COMPLIANCE_OFFICER), kind="compliance_officer", scope_key=f"validity:{v.id}",
                             title=f"Renewal to verify: {item.label}", body=f"Application {app.reference}: the institution uploaded a renewal and states a new date. The earlier date and its alerts stay in force until you verify the renewal.", link="/app/post-approval?tab=validity", application_id=app.id)
        return v, False
    except BaseException:
        storage.delete(stored.key)
        raise


# ---- the human review -----------------------------------------------------------------------------------------


def verify(db: Session, user: User, version_id: uuid.UUID, verified: date, reason: str, internal_note: str | None, key: str, request: Request | None, settings: Settings) -> tuple[ValidityVersion, bool]:
    v = db.get(ValidityVersion, version_id, populate_existing=True)
    if v is None:
        raise not_found("Date")
    item = get_item(db, user, v.item_id, request)
    if Permission.COMPLIANCE_VERIFY not in permissions_for(user.role):
        audit.record(db, "compliance.access_denied", request=request, actor=user, target_type="validity_version", target_id=v.id, detail={"attempt": "verify"})
        db.commit()
        raise not_found("Date")
    if v.status == "VERIFIED" and v.verified_by_id == user.id and v.verified_date == verified:
        return v, True  # the same verification sent again (a retry): nothing new is recorded
    if v.status not in ("UNVERIFIED", "NEEDS_REVIEW"):
        raise conflict("invalid_transition", f"This date is already {v.status.lower()}.")
    if item.status != "ACTIVE":
        raise conflict("item_resolved", "This document no longer needs tracking.")
    if not in_range(verified):
        raise invalid("That date is not plausible.", [Issue("verified_date", f"Enter a date between {MIN_YEAR} and {MAX_YEAR}.", "verified_date")])
    now = utcnow()
    app = db.get(Application, item.application_id)
    for older in db.scalars(select(ValidityVersion).where(ValidityVersion.item_id == item.id, ValidityVersion.status == "VERIFIED", ValidityVersion.id != v.id)).all():
        older.status = ValidityStatus.SUPERSEDED.value  # only a VERIFIED successor retires the earlier verified date, and only now
    v.status, v.verified_date, v.verified_by_id, v.verified_at, v.review_reason, v.internal_note = ValidityStatus.VERIFIED.value, verified, user.id, now, reason.strip(), (internal_note or "").strip() or None
    db.flush()
    extras, ref = clock_policy.alert_policy(config_of(app), settings, VALIDITY_TRIGGER)
    snap = {"policy_key": "document-validity", "label": f"Validity of {item.label}", "deadline_type": "VALIDITY", "trigger_event": VALIDITY_TRIGGER, "state": "VERIFIED_DATE",
            "message": "A date a person confirmed against the document. The expiry moment is the end of that date (UTC); the official convention is not verified.",
            "verified_date": verified.isoformat(), "date_origin": v.source, "alert_policy": ref, "rule_version_id": str(app.rule_version_id), **extras.as_dict()}
    clock, _ = clocks.create_clock(db, kind=ClockKind.DOCUMENT_VALIDITY.value, deadline_type="VALIDITY", subject_type="validity_version", subject_id=v.id, policy_key="document-validity", label=f"Validity: {item.label}", app=app,
                                   trigger_event=VALIDITY_TRIGGER, trigger_at=now, due_at=expiry_instant(verified), snapshot=snap, responsible_role="compliance_officer", actor=user)
    from app.services import clock_sources

    clock_sources.refresh(db)  # the clock of the version just superseded ends now, and its pending alerts with it
    scheduler.plan_alerts(db, clock, settings)
    audit.record(db, "validity.verified", request=request, actor=user, target_type="validity_item", target_id=item.id, detail={"version": v.version_number, "verified_date": verified.isoformat(), "origin": v.source, "request_key": key})
    notifications.notify(db, NotificationEvent.VALIDITY_VERIFIED, notifications.institution_users(db, item.institution_id), kind="institution", scope_key=f"validity-verified:{v.id}",
                         title=f"Date confirmed: {item.label}", body=f"Application {app.reference}: the validity date {verified.isoformat()} was confirmed by a verifier.", link="/app/post-approval?tab=validity", application_id=app.id)
    return v, False


def reject(db: Session, user: User, version_id: uuid.UUID, reason: str, internal_note: str | None, request: Request | None) -> ValidityVersion:
    v = db.get(ValidityVersion, version_id, populate_existing=True)
    if v is None:
        raise not_found("Date")
    item = get_item(db, user, v.item_id, request)
    if Permission.COMPLIANCE_VERIFY not in permissions_for(user.role):
        raise not_found("Date")
    if v.status not in ("UNVERIFIED", "NEEDS_REVIEW"):
        raise conflict("invalid_transition", f"This date is already {v.status.lower()}.")
    v.status, v.review_reason, v.internal_note, v.verified_by_id, v.verified_at = ValidityStatus.REJECTED.value, reason.strip(), (internal_note or "").strip() or None, user.id, utcnow()
    app = db.get(Application, item.application_id)
    audit.record(db, "validity.rejected", request=request, actor=user, target_type="validity_item", target_id=item.id, detail={"version": v.version_number})
    notifications.notify(db, NotificationEvent.VALIDITY_VERIFIED, notifications.institution_users(db, item.institution_id), kind="institution", scope_key=f"validity-rejected:{v.id}",
                         title=f"Renewal not accepted as evidence: {item.label}", body=f"Application {app.reference}: {reason.strip()[:300]} The earlier date and its alerts stay in force. You may upload another renewal.", link="/app/post-approval?tab=validity", application_id=app.id)
    return v


def resolve_item(db: Session, user: User, item_id: uuid.UUID, reason: str, request: Request | None) -> ValidityItem:
    """An authorised person records that the document no longer needs tracking (for example a permitted change of policy). Its clocks end; the history stays."""
    item = get_item(db, user, item_id, request)
    if Permission.COMPLIANCE_VERIFY not in permissions_for(user.role):
        raise not_found("Document")
    if item.status != "ACTIVE":
        raise conflict("item_resolved", "This document is already resolved.")
    item.status, item.resolved_at, item.resolved_by_id, item.resolution_reason = "RESOLVED", utcnow(), user.id, reason.strip()
    db.flush()
    from app.services import clock_sources

    clock_sources.refresh(db)
    audit.record(db, "validity.resolved", request=request, actor=user, target_type="validity_item", target_id=item.id, detail={"reason": reason.strip()[:200]})
    return item


def document_for_download(db: Session, user: User, version_id: uuid.UUID, request: Request | None) -> tuple[str, str, str]:
    """(storage_key, media_type, filename) of a renewal upload, or of the submitted document a date was read from. Same scope as the item."""
    v = db.get(ValidityVersion, version_id)
    if v is None:
        raise not_found("Document")
    item = get_item(db, user, v.item_id, request)
    audit.record(db, "validity.document_downloaded", request=request, actor=user, target_type="validity_version", target_id=v.id)
    db.commit()
    if v.storage_key:
        return v.storage_key, v.media_type or "application/octet-stream", v.original_filename or "renewal"
    doc = db.get(ApplicationDocument, v.source_document_id) if v.source_document_id else None
    if doc is None or doc.application_id != item.application_id:
        raise not_found("Document")
    return doc.storage_key, doc.media_type, doc.original_filename
