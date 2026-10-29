"""Institution-facing notices and responses, plus in-app notifications.

An institution sees a notice only once it is ISSUED (drafts and notices under review do not exist for it), only
for its own applications, and only the institution-facing content. Responses are received, not accepted.
"""

import uuid

from fastapi import APIRouter, Depends, File, Query, Request, Response, UploadFile
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app import scrutiny_presenters as pr
from app.config import Settings, get_settings
from app.db import get_db
from app.deps import CurrentUser, csrf_protect, require_permission
from app.models import utcnow
from app.models_applications import Application
from app.models_scrutiny import Notification
from app.rbac import Permission
from app.schemas_scrutiny import InstNotice, InstNoticeSummary, InstScrutiny, NotificationOut, NotificationPage, ResponseDraftIn, ResponseSubmitIn
from app.services import applications as app_svc
from app.services import responses as rsvc
from app.services.errors import not_found
from app.storage import LocalStorage, get_storage

router = APIRouter(tags=["notices"], dependencies=[Depends(csrf_protect)])

can_read = require_permission(Permission.NOTICE_READ_OWN)
can_respond = require_permission(Permission.NOTICE_RESPOND)


def _notice(db: Session, current: CurrentUser, notice_id: uuid.UUID, request: Request) -> InstNotice:
    n, app = rsvc.institution_notice(db, current.user, notice_id, request)
    return pr.inst_notice(db, n, app)


@router.get("/api/notices", response_model=list[InstNoticeSummary])
def list_notices(current: CurrentUser = Depends(can_read), db: Session = Depends(get_db), open_only: bool = False) -> list[InstNoticeSummary]:
    """Issued notices for the signed-in user's institution (never drafts)."""
    if current.user.institution_id is None:
        return []
    q = select(Application).where(Application.institution_id == current.user.institution_id)
    out: list[InstNoticeSummary] = []
    for app in db.scalars(q):
        for s in pr.inst_notice_summaries(db, app):
            if not open_only or s.status == "ISSUED":
                out.append(s)
    return sorted(out, key=lambda s: s.issued_at or utcnow(), reverse=True)


@router.get("/api/notices/{notice_id}", response_model=InstNotice)
def get_notice(notice_id: uuid.UUID, request: Request, current: CurrentUser = Depends(can_read), db: Session = Depends(get_db)) -> InstNotice:
    return _notice(db, current, notice_id, request)


@router.put("/api/notices/{notice_id}/response", response_model=InstNotice)
def save_response(notice_id: uuid.UUID, body: ResponseDraftIn, request: Request, current: CurrentUser = Depends(can_respond), db: Session = Depends(get_db)) -> InstNotice:
    rsvc.save_draft(db, current.user, notice_id, body.explanation, [i.model_dump() for i in body.items], body.row_version, request)
    db.commit()
    return _notice(db, current, notice_id, request)


@router.post("/api/notices/{notice_id}/items/{item_id}/documents", response_model=InstNotice, status_code=201)
def upload_correction(
    notice_id: uuid.UUID, item_id: uuid.UUID, request: Request, response: Response, file: UploadFile = File(), current: CurrentUser = Depends(can_respond),
    db: Session = Depends(get_db), settings: Settings = Depends(get_settings), storage: LocalStorage = Depends(get_storage),
) -> InstNotice:
    """Corrected evidence: a new version of the requirement's document, run through the same checks and processing as any upload."""
    _, outcome = rsvc.upload_correction(db, current.user, notice_id, item_id, file, request, settings, storage)
    if outcome == "duplicate":
        response.status_code = 200
        response.headers["X-Upload-Result"] = "duplicate_of_current"
    return _notice(db, current, notice_id, request)


@router.post("/api/notices/{notice_id}/response/submit", response_model=InstNotice)
def submit_response(notice_id: uuid.UUID, body: ResponseSubmitIn, request: Request, response: Response, current: CurrentUser = Depends(can_respond), db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> InstNotice:
    _, replay = rsvc.submit(db, current.user, notice_id, body.idempotency_key, body.row_version, request, settings)
    db.commit()
    if replay:
        response.headers["X-Idempotent-Replay"] = "true"
    return _notice(db, current, notice_id, request)


@router.get("/api/applications/{application_id}/scrutiny", response_model=InstScrutiny)
def application_scrutiny(application_id: uuid.UUID, request: Request, current: CurrentUser = Depends(require_permission(Permission.APPLICATION_READ_OWN)), db: Session = Depends(get_db)) -> InstScrutiny:
    """Where the application stands in scrutiny, in the institution's terms. No internal state, names or notes."""
    app = app_svc.get_owned(db, current.user, application_id, request)
    return pr.inst_scrutiny(db, app)


# ---- notifications ------------------------------------------------------------------------------

can_notifications = require_permission(Permission.NOTIFICATIONS_READ)


def _out(n: Notification, ref: str | None) -> NotificationOut:
    return NotificationOut(id=n.id, event_type=n.event_type, title=n.title, body=n.body, link=n.link, created_at=n.created_at, read=n.read_at is not None, delivery_state=n.delivery_state,
                           external_delivery=n.external_delivery, application_reference=ref)


@router.get("/api/notifications", response_model=NotificationPage)
def notifications(current: CurrentUser = Depends(can_notifications), db: Session = Depends(get_db), unread_only: bool = False, page: int = Query(1, ge=1, le=10_000), page_size: int = Query(20, ge=1, le=50)) -> NotificationPage:
    mine = Notification.recipient_user_id == current.user.id
    q = select(Notification, Application.reference).outerjoin(Application, Application.id == Notification.application_id).where(mine)
    if unread_only:
        q = q.where(Notification.read_at.is_(None))
    total = db.scalar(select(func.count()).select_from(q.subquery())) or 0
    unread = db.scalar(select(func.count()).select_from(Notification).where(mine, Notification.read_at.is_(None))) or 0
    rows = db.execute(q.order_by(Notification.created_at.desc()).limit(page_size).offset((page - 1) * page_size)).all()
    return NotificationPage(items=[_out(n, ref) for n, ref in rows], total=total, unread=unread, page=page, page_size=page_size)


@router.post("/api/notifications/{notification_id}/read", response_model=NotificationOut)
def mark_read(notification_id: uuid.UUID, current: CurrentUser = Depends(can_notifications), db: Session = Depends(get_db)) -> NotificationOut:
    n = db.get(Notification, notification_id)
    if n is None or n.recipient_user_id != current.user.id:
        raise not_found("Notification")
    if n.read_at is None:
        n.read_at = utcnow()
        db.commit()
    return _out(n, None)


@router.post("/api/notifications/read-all", response_model=dict)
def mark_all_read(current: CurrentUser = Depends(can_notifications), db: Session = Depends(get_db)) -> dict:
    res = db.execute(update(Notification).where(Notification.recipient_user_id == current.user.id, Notification.read_at.is_(None)).values(read_at=utcnow()))
    db.commit()
    return {"marked": res.rowcount}
