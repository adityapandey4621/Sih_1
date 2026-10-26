"""The institution's response to an issued deficiency notice.

A response is received, not accepted: submitting it records it, links the evidence versions the institution
supplied, moves the case into re-scrutiny and notifies the reviewers. It closes no finding and completes
nothing. One notice has one response; a further round needs a further notice.
"""

import uuid
from contextlib import nullcontext

from fastapi import Request, UploadFile
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app import audit, jobs
from app.config import Settings
from app.domain import NOTICE_VISIBLE_TO_INSTITUTION, CaseAction, ItemResponseState, NoticeStatus, ResponseStatus, RoundKind, RoundStatus
from app.models import User, utcnow
from app.models_applications import Application, ApplicationDocument, ApplicationEvent
from app.models_scrutiny import DeficiencyNotice, NoticeItem, NoticeResponse, NoticeResponseItem, ResponseDocument, ScrutinyRound
from app.security import as_utc
from app.services import notices as nsvc
from app.services.applications import config_of
from app.services import scrutiny as sc
from app.services import scrutiny_texts as texts
from app.services.documents import upload_version
from app.services.errors import Issue, ServiceError, conflict, invalid, not_found
from app.storage import LocalStorage

MAX_TEXT = 8000


def institution_notice(db: Session, user: User, notice_id: uuid.UUID, request: Request | None) -> tuple[DeficiencyNotice, Application]:
    """A notice addressed to the user's own institution and actually issued. Drafts, other institutions' notices: "not found"."""
    n = db.scalars(select(DeficiencyNotice).where(DeficiencyNotice.id == notice_id).options(selectinload(DeficiencyNotice.items)).execution_options(populate_existing=True)).one_or_none()
    app = db.get(Application, n.application_id) if n is not None else None
    if n is None or app is None or user.institution_id is None or app.institution_id != user.institution_id or n.status not in NOTICE_VISIBLE_TO_INSTITUTION:
        if n is not None:
            audit.record(db, "notice.access_denied", request=request, actor=user, target_type="deficiency_notice", target_id=n.id)
            db.commit()
        raise not_found("Notice")
    return n, app


def response_of(db: Session, notice_id: uuid.UUID) -> NoticeResponse | None:
    return db.scalars(
        select(NoticeResponse).where(NoticeResponse.notice_id == notice_id)
        .options(selectinload(NoticeResponse.items).selectinload(NoticeResponseItem.documents), selectinload(NoticeResponse.created_by), selectinload(NoticeResponse.submitted_by))
        .execution_options(populate_existing=True)
    ).one_or_none()


def _require_open(n: DeficiencyNotice) -> None:
    if n.status != NoticeStatus.ISSUED.value:
        raise conflict("notice_not_open", "This notice is no longer open for a response.")


def _get_or_create(db: Session, n: DeficiencyNotice, app: Application, user: User) -> NoticeResponse:
    r = response_of(db, n.id)
    if r is not None:
        return r
    try:
        with db.begin_nested() if db.bind.dialect.name == "postgresql" else nullcontext():
            db.add(NoticeResponse(notice_id=n.id, application_id=app.id, institution_id=app.institution_id, created_by_id=user.id))
            db.flush()
    except IntegrityError:
        if db.bind.dialect.name != "postgresql":
            raise  # no savepoint was taken, so the session cannot continue
        # PostgreSQL: the savepoint rolled back only the insert; another request created the response first, so use theirs
    r = response_of(db, n.id)
    if r is None:
        raise ServiceError(500, "response_unavailable", "The response could not be started. Please try again.")
    return r


def _item_row(db: Session, r: NoticeResponse, item_id: uuid.UUID) -> NoticeResponseItem:
    row = next((i for i in r.items if i.notice_item_id == item_id), None)
    if row is None:
        row = NoticeResponseItem(response_id=r.id, notice_item_id=item_id)
        db.add(row)
        db.flush()
        db.refresh(r)
    return row


def save_draft(db: Session, user: User, notice_id: uuid.UUID, explanation: str | None, items: list[dict], row_version: int | None, request: Request | None) -> NoticeResponse:
    n, app = institution_notice(db, user, notice_id, request)
    _require_open(n)
    existing = response_of(db, n.id)
    if existing is not None and existing.status == ResponseStatus.SUBMITTED.value:
        raise conflict("response_already_submitted", "The response to this notice has already been submitted.")
    valid_ids = {i.id for i in n.items}
    issues = []
    for it in items:
        if it["notice_item_id"] not in valid_ids:
            issues.append(Issue("unknown_item", "That item does not belong to this notice.", str(it["notice_item_id"])))
        if len(it.get("explanation") or "") > MAX_TEXT:
            issues.append(Issue("too_long", "That explanation is too long.", str(it["notice_item_id"])))
    if explanation is not None and len(explanation) > MAX_TEXT:
        issues.append(Issue("too_long", "The explanation is too long.", "explanation"))
    if issues:
        raise invalid("The response was not saved.", issues)
    if existing is not None and row_version != existing.row_version:
        raise conflict("stale_version", "This response was changed elsewhere since you opened it. Reload it before saving.")
    r = existing or _get_or_create(db, n, app, user)
    res = db.execute(update(NoticeResponse).where(NoticeResponse.id == r.id, NoticeResponse.status == ResponseStatus.DRAFT.value, NoticeResponse.row_version == r.row_version).values(
        explanation=(explanation.strip() if explanation is not None else r.explanation), row_version=r.row_version + 1, updated_at=utcnow()))
    if res.rowcount != 1:
        raise conflict("stale_version", "This response was changed at the same moment. Reload it before saving.")
    now = utcnow()
    for it in items:
        row = _item_row(db, r, it["notice_item_id"])
        db.execute(update(NoticeResponseItem).where(NoticeResponseItem.id == row.id).values(explanation=(it.get("explanation") or "").strip(), updated_at=now))
    audit.record(db, "notice.response_saved", request=request, actor=user, target_type="deficiency_notice", target_id=n.id, detail={"reference": n.reference, "items": len(items)})
    db.flush()
    return response_of(db, n.id)


def upload_correction(
    db: Session, user: User, notice_id: uuid.UUID, item_id: uuid.UUID, upload: UploadFile, request: Request | None, settings: Settings, storage: LocalStorage,
) -> tuple[NoticeResponse, str]:
    """Corrected evidence for one notice item: a new version of the requirement's document through the Phase 3 pipeline."""
    n, app = institution_notice(db, user, notice_id, request)
    _require_open(n)
    item = next((i for i in n.items if i.id == item_id), None)
    if item is None:
        raise not_found("Notice item")
    requirement = next((c for c in config_of(app)["checklist"] if c["item_key"] == item.item_key), None) if item.item_key else None
    if requirement is None or requirement["item_type"] != "document_upload":
        raise invalid("This item does not concern a document requirement, so there is nothing to upload against it. Use the explanation field.")
    existing = response_of(db, n.id)
    if existing is not None and existing.status == ResponseStatus.SUBMITTED.value:
        raise conflict("response_already_submitted", "The response to this notice has already been submitted.")

    def link(doc: ApplicationDocument) -> None:
        r = _get_or_create(db, n, app, user)
        row = _item_row(db, r, item.id)
        if not any(d.document_id == doc.id for d in row.documents):
            db.add(ResponseDocument(response_item_id=row.id, document_id=doc.id, linked_by_id=user.id))
        db.add(ApplicationEvent(application_id=app.id, actor_id=user.id, action="correction_uploaded", detail={"notice": n.reference, "item_key": item.item_key, "document_id": str(doc.id), "version": doc.version_number}))

    _, outcome = upload_version(db, user, app.id, item.item_key, upload, request, settings, storage, correction=True, on_stored=link)
    return response_of(db, n.id), outcome


def submit(db: Session, user: User, notice_id: uuid.UUID, idempotency_key: str, row_version: int, request: Request | None, settings: Settings) -> tuple[NoticeResponse, bool]:
    """-> (response, replay). Moves the case into re-scrutiny; closes nothing."""
    n, app = institution_notice(db, user, notice_id, request)
    case = sc.lock_case(db, n.case_id)
    n = nsvc.load_notice(db, n.id)
    r = response_of(db, n.id)
    if r is not None and r.status == ResponseStatus.SUBMITTED.value:
        if r.idempotency_key == idempotency_key:
            return r, True
        raise conflict("response_already_submitted", "The response to this notice has already been submitted. It was not submitted a second time.")
    _require_open(n)
    if r is None:
        raise invalid("There is nothing to submit yet. Add an explanation or corrected evidence to at least one item first.", code="empty_response")
    if r.row_version != row_version:
        raise conflict("stale_version", "This response was changed elsewhere since you opened it. Reload it before submitting.")
    answered = [i for i in r.items if (i.explanation or "").strip() or i.documents]
    if not answered:
        raise invalid("Add an explanation or corrected evidence to at least one item before submitting.", code="empty_response")

    now = utcnow()
    res = db.execute(update(NoticeResponse).where(NoticeResponse.id == r.id, NoticeResponse.status == ResponseStatus.DRAFT.value, NoticeResponse.row_version == row_version).values(
        status=ResponseStatus.SUBMITTED.value, submitted_by_id=user.id, submitted_at=now, idempotency_key=idempotency_key, row_version=row_version + 1, updated_at=now))
    if res.rowcount != 1:
        raise conflict("stale_version", "This response was changed or submitted at the same moment. It was not submitted twice.")
    nsvc._set_status(db, n, NoticeStatus.RESPONSE_RECEIVED)
    from_state = case.state
    sc.transition(db, case, CaseAction.RECEIVE_RESPONSE)
    last = sc.latest_round(db, case.id)
    rnd = ScrutinyRound(case_id=case.id, number=(last.number if last else 0) + 1, kind=RoundKind.RE_SCRUTINY.value, status=RoundStatus.OPEN.value, triggered_by_notice_id=n.id)
    db.add(rnd)
    late = bool(n.response_due_at and now > as_utc(n.response_due_at))  # recorded, never enforced: no verified late-response rule exists
    sc.log_event(db, case, user, "response_submitted", request=request, object_type="deficiency_notice", object_id=n.id, from_state=from_state, to_state=case.state, institution_visible=True,
                 detail={"reference": n.reference, "items_answered": len(answered), "items_total": len(n.items), "after_due_date": late, "round_opened": rnd.number})
    db.add(ApplicationEvent(application_id=app.id, actor_id=user.id, action="notice_response_submitted", detail={"reference": n.reference}))
    jobs.enqueue_cross_check(db, app.id, settings)  # refresh the automated checks against the corrected evidence
    db.flush()
    db.refresh(r)
    texts.response_submitted(db, case, n, app)
    texts.ready_for_rescrutiny(db, case, n, app)
    return r, False


# ---- derived item state -------------------------------------------------------------------------


def item_state(resp: NoticeResponse | None, item: NoticeItem, finding_state: str | None) -> ItemResponseState:
    """NOT_RESPONDED / DRAFT / SUBMITTED / REVIEWED. REVIEWED is derived from re-scrutiny's conclusion, never stored."""
    row = next((i for i in resp.items if i.notice_item_id == item.id), None) if resp else None
    has_content = bool(row and ((row.explanation or "").strip() or row.documents))
    if resp is not None and resp.status == ResponseStatus.SUBMITTED.value:
        if not has_content:
            return ItemResponseState.NOT_RESPONDED
        return ItemResponseState.REVIEWED if finding_state in ("RESOLVED", "UNRESOLVED", "NOT_SUPPORTED") else ItemResponseState.SUBMITTED
    return ItemResponseState.DRAFT if has_content else ItemResponseState.NOT_RESPONDED
