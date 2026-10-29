"""Compliance obligations, document validity and deadline clocks (Phase 7).

Permissions are checked here; scope (which institution, which assigned verifier) is checked in the services, and anything out of scope answers "not found" and is audited.
Nothing here marks an obligation verified because a file arrived, and nothing revokes an approval or imposes a penalty.
"""

import uuid
from datetime import UTC, date, datetime
from pathlib import PurePosixPath

from fastapi import APIRouter, Depends, File, Form, Query, Request, Response, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import audit
from app import compliance_presenters as cp
from app.config import Settings, get_settings
from app.db import get_db
from app.deps import CurrentUser, csrf_protect, get_current_user, require_any_permission, require_permission
from app.domain import CLOCK_LIVE, ReviewOutcome, WorkStatus
from app.models import User, utcnow
from app.models_applications import Application
from app.models_clocks import Clock, ScheduledWork
from app.models_decisions import Appeal, CouncilDecision, DecisionCase, EcDecision, OfficialIssuance
from app.rbac import Permission, permissions_for
from app.routers.decisions import _file_response
from app.schemas_compliance import ObligationIn, PauseIn, ReassignIn, RejectDateIn, ResolveIn, ResumeIn, ReviewIn, VerifyDateIn, WithdrawIn
from app.services import clocks, compliance, validity
from app.services.decision_texts import LABEL
from app.services.errors import ServiceError, invalid, not_found
from app.storage import LocalStorage, get_storage

router = APIRouter(tags=["compliance"], dependencies=[Depends(csrf_protect)])

can_see = require_any_permission(Permission.COMPLIANCE_QUEUE, Permission.COMPLIANCE_READ_OWN)
can_define = require_permission(Permission.COMPLIANCE_DEFINE)
can_verify = require_permission(Permission.COMPLIANCE_VERIFY)
can_submit = require_permission(Permission.COMPLIANCE_SUBMIT)
can_read_own = require_permission(Permission.COMPLIANCE_READ_OWN)
can_clock = require_any_permission(Permission.CLOCK_READ, Permission.COMPLIANCE_READ_OWN)
can_manage_clock = require_permission(Permission.CLOCK_MANAGE)


def _replay(response: Response, replay: bool) -> None:
    if replay:
        response.status_code = 200
        response.headers["X-Idempotent-Replay"] = "true"


def _detail(db: Session, current: CurrentUser, o_id: uuid.UUID, settings: Settings, request: Request) -> dict:
    o = compliance.get_obligation(db, current.user, o_id, request)
    return cp.obligation_detail(db, o, settings, current.user, datetime.now(UTC))


# =====================================================================================================
# Obligations
# =====================================================================================================


@router.get("/api/post-approval/obligations")
def obligations(
    current: CurrentUser = Depends(can_see), db: Session = Depends(get_db), settings: Settings = Depends(get_settings), status: str | None = Query(None, pattern="^[A-Z_]{3,24}$"),
    q: str | None = Query(None, max_length=80), due: str | None = Query(None, pattern="^(overdue|soon|ontrack)$"), page: int = Query(1, ge=1, le=10_000), page_size: int = Query(20, ge=1, le=100),
) -> dict:
    return cp.list_obligations(db, current.user, settings, status=status, q=q, due=due, page=page, page_size=page_size)


@router.get("/api/post-approval/obligations/{obligation_id}")
def obligation(obligation_id: uuid.UUID, request: Request, current: CurrentUser = Depends(can_see), db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> dict:
    return _detail(db, current, obligation_id, settings, request)


@router.get("/api/post-approval/overview")
def overview(current: CurrentUser = Depends(can_read_own), db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> dict:
    """The institution's own obligations, tracked documents and deadlines in one response."""
    return cp.institution_overview(db, current.user, settings)


@router.get("/api/post-approval/cases")
def issued_cases(current: CurrentUser = Depends(can_define), db: Session = Depends(get_db), q: str | None = Query(None, max_length=80)) -> dict:
    """Applications whose official outcome has been issued, so an obligation can be recorded against one."""
    stmt = (select(DecisionCase, Application).join(Application, Application.id == DecisionCase.application_id).where(DecisionCase.state.in_(("ISSUED", "CLOSED"))))
    if q:
        stmt = stmt.where(Application.reference.ilike(f"%{q.strip()}%"))
    rows = db.execute(stmt.order_by(DecisionCase.first_issued_at.desc()).limit(50)).all()
    out = []
    for case, app in rows:
        iss = db.scalar(select(OfficialIssuance).where(OfficialIssuance.case_id == case.id, OfficialIssuance.status == "ISSUED"))
        dec = db.get(EcDecision, iss.decision_id) if iss else None
        ap = db.scalar(select(Appeal).where(Appeal.application_id == app.id))
        council = db.scalar(select(CouncilDecision).where(CouncilDecision.appeal_id == ap.id, CouncilDecision.kind == "APPEAL_DECISION", CouncilDecision.is_live.is_(True))) if ap else None
        out.append({"application_id": app.id, "reference": app.reference, "institution_name": app.institution.name, "outcome": dec.outcome if dec else None, "outcome_label": LABEL.get(dec.outcome) if dec else None,
                    "decision_version": dec.version_number if dec else None, "issued_at": case.first_issued_at,
                    "council_appeal_outcome": (LABEL.get(council.outcome) or council.outcome) if council else None})
    return {"items": out}


@router.get("/api/post-approval/verifiers")
def verifiers(current: CurrentUser = Depends(can_define), db: Session = Depends(get_db)) -> dict:
    users = db.scalars(select(User).where(User.role == "compliance_officer", User.is_active.is_(True)).order_by(User.full_name)).all()
    return {"items": [{"id": u.id, "name": u.full_name} for u in users]}


@router.post("/api/post-approval/applications/{application_id}/obligations", status_code=201)
def define_obligation(application_id: uuid.UUID, body: ObligationIn, request: Request, response: Response, current: CurrentUser = Depends(can_define), db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> dict:
    o, replay = compliance.define(db, current.user, application_id, body.model_dump(exclude={"idempotency_key"}), body.idempotency_key, request, settings)
    db.commit()
    _replay(response, replay)
    return cp.obligation_detail(db, o, settings, current.user, datetime.now(UTC))


@router.post("/api/post-approval/obligations/{obligation_id}/evidence", status_code=201)
def submit_evidence(
    obligation_id: uuid.UUID, request: Request, response: Response, files: list[UploadFile] = File(), labels: list[str] = Form(default_factory=list), note: str = Form(""),
    idempotency_key: str = Form(..., min_length=8, max_length=80), row_version: int = Form(...), current: CurrentUser = Depends(can_submit), db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings), storage: LocalStorage = Depends(get_storage),
) -> dict:
    """The institution submits evidence. It is stored and shown to the verifier; it is NOT verification."""
    uploads = [((labels[i] if i < len(labels) and labels[i].strip() else PurePosixPath(f.filename or "Evidence").stem), f) for i, f in enumerate(files)]
    if len(note) > 8000:
        raise invalid("The note is too long.")
    o, replay = compliance.submit_evidence(db, current.user, obligation_id, note, uploads, idempotency_key, row_version, request, settings, storage)
    db.commit()
    _replay(response, replay)
    return cp.obligation_detail(db, o, settings, current.user, datetime.now(UTC))


@router.post("/api/post-approval/obligations/{obligation_id}/review", status_code=201)
def review(obligation_id: uuid.UUID, body: ReviewIn, request: Request, response: Response, current: CurrentUser = Depends(can_verify), db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> dict:
    o, replay = compliance.review(db, current.user, obligation_id, ReviewOutcome(body.outcome), body.submission_id, body.reason, body.internal_note, body.idempotency_key, body.row_version, request)
    db.commit()
    _replay(response, replay)
    return cp.obligation_detail(db, o, settings, current.user, datetime.now(UTC))


@router.post("/api/post-approval/obligations/{obligation_id}/withdraw")
def withdraw(obligation_id: uuid.UUID, body: WithdrawIn, request: Request, current: CurrentUser = Depends(can_define), db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> dict:
    o = compliance.withdraw(db, current.user, obligation_id, body.reason, body.row_version, request)
    db.commit()
    return cp.obligation_detail(db, o, settings, current.user, datetime.now(UTC))


@router.post("/api/post-approval/obligations/{obligation_id}/reassign")
def reassign(obligation_id: uuid.UUID, body: ReassignIn, request: Request, current: CurrentUser = Depends(can_define), db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> dict:
    o = compliance.reassign(db, current.user, obligation_id, body.verifier_id, body.reason, request)
    db.commit()
    return cp.obligation_detail(db, o, settings, current.user, datetime.now(UTC))


@router.get("/api/post-approval/documents/{document_id}/download")
def download_document(document_id: uuid.UUID, request: Request, current: CurrentUser = Depends(can_see), db: Session = Depends(get_db), storage: LocalStorage = Depends(get_storage)) -> StreamingResponse:
    doc = compliance.document_for_download(db, current.user, document_id, request)
    return _file_response(storage, doc.storage_key, doc.media_type, doc.original_filename)


# =====================================================================================================
# Document validity
# =====================================================================================================


@router.get("/api/post-approval/validity")
def validity_list(
    current: CurrentUser = Depends(can_see), db: Session = Depends(get_db), settings: Settings = Depends(get_settings), status: str | None = Query(None, pattern="^(needs_review|expiring|resolved)$"),
    q: str | None = Query(None, max_length=80), page: int = Query(1, ge=1, le=10_000), page_size: int = Query(20, ge=1, le=100),
) -> dict:
    return cp.list_validity(db, current.user, settings, status=status, q=q, page=page, page_size=page_size)


def _item_out(db: Session, current: CurrentUser, item_id: uuid.UUID, settings: Settings) -> dict:
    it = db.get(validity.ValidityItem, item_id, populate_existing=True)
    official = Permission.COMPLIANCE_READ_OWN not in permissions_for(current.user.role)
    d = cp.validity_item_out(db, it, settings, datetime.now(UTC), official=official)
    d["can"]["review"] = official and Permission.COMPLIANCE_VERIFY in current.permissions
    return d


@router.post("/api/post-approval/validity/items/{item_id}/renewals", status_code=201)
def renew(
    item_id: uuid.UUID, request: Request, response: Response, file: UploadFile = File(), declared_date: date = Form(...), idempotency_key: str = Form(..., min_length=8, max_length=80),
    current: CurrentUser = Depends(can_submit), db: Session = Depends(get_db), settings: Settings = Depends(get_settings), storage: LocalStorage = Depends(get_storage),
) -> dict:
    """The institution uploads a renewed document and states its new date. This is a claim, not a verified date; the earlier date's alerts stay until it is verified."""
    _, replay = validity.renew(db, current.user, item_id, declared_date, idempotency_key, file, request, settings, storage)
    db.commit()
    _replay(response, replay)
    return _item_out(db, current, item_id, settings)


@router.post("/api/post-approval/validity/versions/{version_id}/verify", status_code=201)
def verify_date(version_id: uuid.UUID, body: VerifyDateIn, request: Request, response: Response, current: CurrentUser = Depends(can_verify), db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> dict:
    v, replay = validity.verify(db, current.user, version_id, body.verified_date, body.reason, body.internal_note, body.idempotency_key, request, settings)
    db.commit()
    _replay(response, replay)
    return _item_out(db, current, v.item_id, settings)


@router.post("/api/post-approval/validity/versions/{version_id}/reject")
def reject_date(version_id: uuid.UUID, body: RejectDateIn, request: Request, current: CurrentUser = Depends(can_verify), db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> dict:
    v = validity.reject(db, current.user, version_id, body.reason, body.internal_note, request)
    db.commit()
    return _item_out(db, current, v.item_id, settings)


@router.post("/api/post-approval/validity/items/{item_id}/resolve")
def resolve(item_id: uuid.UUID, body: ResolveIn, request: Request, current: CurrentUser = Depends(can_verify), db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> dict:
    validity.resolve_item(db, current.user, item_id, body.reason, request)
    db.commit()
    return _item_out(db, current, item_id, settings)


@router.get("/api/post-approval/validity/versions/{version_id}/download")
def download_version(version_id: uuid.UUID, request: Request, current: CurrentUser = Depends(can_see), db: Session = Depends(get_db), storage: LocalStorage = Depends(get_storage)) -> StreamingResponse:
    key, media, name = validity.document_for_download(db, current.user, version_id, request)
    return _file_response(storage, key, media, name)


# =====================================================================================================
# Clocks
# =====================================================================================================


def _visible_clock(db: Session, current: CurrentUser, clock_id: uuid.UUID, request: Request) -> Clock:
    c = db.get(Clock, clock_id, populate_existing=True)
    perms = permissions_for(current.user.role)
    ok = c is not None and c.kind in cp.visible_kinds(current.user)
    if ok and Permission.COMPLIANCE_READ_OWN in perms:
        ok = current.user.institution_id is not None and c.institution_id == current.user.institution_id
    if not ok:
        if c is not None:
            audit.record(db, "clock.access_denied", request=request, actor=current.user, target_type="clock", target_id=c.id)
            db.commit()
        raise not_found("Deadline")
    return c


@router.get("/api/clocks")
def clock_list(
    current: CurrentUser = Depends(can_clock), db: Session = Depends(get_db), settings: Settings = Depends(get_settings), kind: str | None = Query(None, pattern="^[A-Z_]{3,24}$"),
    status: str | None = Query(None, pattern="^[A-Z_]{3,24}$"), due: str | None = Query(None, pattern="^(overdue|soon|ontrack)$"), q: str | None = Query(None, max_length=80),
    page: int = Query(1, ge=1, le=10_000), page_size: int = Query(25, ge=1, le=100),
) -> dict:
    return cp.list_clocks(db, current.user, settings, kind=kind, status=status, due=due, q=q, page=page, page_size=page_size)


@router.get("/api/clocks/health")
def scheduler_health(current: CurrentUser = Depends(can_manage_clock), db: Session = Depends(get_db)) -> dict:
    """What the scheduler has and has not done, from the work table itself: backlog, failures, the oldest unprocessed alert. Not a metric about AICTE."""
    now = utcnow()
    counts = dict(db.execute(select(ScheduledWork.status, func.count()).group_by(ScheduledWork.status)).all())
    due_pending = db.scalar(select(func.count()).select_from(ScheduledWork).where(ScheduledWork.status.in_((WorkStatus.PENDING.value, WorkStatus.RETRY_WAIT.value)), ScheduledWork.run_at <= now)) or 0
    oldest = db.scalar(select(func.min(ScheduledWork.run_at)).where(ScheduledWork.status.in_((WorkStatus.PENDING.value, WorkStatus.RETRY_WAIT.value)), ScheduledWork.run_at <= now))
    failed = db.scalars(select(ScheduledWork).where(ScheduledWork.status == WorkStatus.FAILED_EXHAUSTED.value).order_by(ScheduledWork.updated_at.desc()).limit(10)).all()
    live = db.scalar(select(func.count()).select_from(Clock).where(Clock.status.in_(CLOCK_LIVE))) or 0
    lag = int((now - oldest.replace(tzinfo=UTC if oldest.tzinfo is None else oldest.tzinfo)).total_seconds()) if oldest else 0
    return {"work": counts, "due_and_waiting": due_pending, "oldest_waiting_lag_seconds": lag, "live_clocks": live,
            "failed": [{"id": w.id, "clock_id": w.clock_id, "alert_kind": w.alert_kind, "attempts": w.attempts, "last_error": w.last_error, "updated_at": w.updated_at} for w in failed],
            "note": "If 'due_and_waiting' stays above zero, the scheduler process is not running. Start it with `python -m app.scheduler`."}


@router.get("/api/clocks/{clock_id}")
def clock_detail(clock_id: uuid.UUID, request: Request, current: CurrentUser = Depends(can_clock), db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> dict:
    c = _visible_clock(db, current, clock_id, request)
    official = Permission.COMPLIANCE_READ_OWN not in permissions_for(current.user.role)
    return cp.clock_out(db, c, settings, datetime.now(UTC), official=official, detail=True)


@router.post("/api/clocks/{clock_id}/pause")
def pause_clock(clock_id: uuid.UUID, body: PauseIn, request: Request, current: CurrentUser = Depends(can_manage_clock), db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> dict:
    """Pause a clock - only if its own policy permits. Otherwise 409: a deadline cannot be stopped, moved or reset by anyone."""
    _visible_clock(db, current, clock_id, request)
    c = clocks.pause(db, current.user, clock_id, body.reason_code, body.reason, body.reference, body.row_version, request)
    db.commit()
    return cp.clock_out(db, c, settings, datetime.now(UTC), official=True, detail=True)


@router.post("/api/clocks/{clock_id}/resume")
def resume_clock(clock_id: uuid.UUID, body: ResumeIn, request: Request, current: CurrentUser = Depends(can_manage_clock), db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> dict:
    _visible_clock(db, current, clock_id, request)
    c = clocks.resume(db, current.user, clock_id, body.row_version, request, settings)
    db.commit()
    return cp.clock_out(db, c, settings, datetime.now(UTC), official=True, detail=True)


_ = (get_current_user, ServiceError)
