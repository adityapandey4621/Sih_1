"""Staff-facing scrutiny API: queue, case workspace, findings, notices (drafting and issuance), completion, hand-off.

Every endpoint is checked twice: by role permission here (`require_permission`) and by case scope in the service
(`scrutiny_access.get_case`). Mutating endpoints return the refreshed case so the workspace updates in one round trip.
"""

import uuid

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy.orm import Session

from app import audit, scrutiny_access
from app import evidence_presenters as ep
from app import scrutiny_presenters as pr
from app.config import Settings, get_settings
from app.db import get_db
from app.deps import CurrentUser, csrf_protect, require_any_permission, require_permission
from app.domain import FindingState
from app.rbac import Permission
from app.schemas_evidence import ApplicationEvidenceOut
from app.schemas_scrutiny import (
    AssignableOut,
    AssignIn,
    CaseDetailOut,
    CompleteIn,
    DetermineIn,
    DispositionIn,
    FindingIn,
    FindingPatchIn,
    HandoffListItem,
    IssueIn,
    NoticeCreateIn,
    NoticePreviewOut,
    NoticeSubmitIn,
    NoticeUpdateIn,
    NotesIn,
    QueuePage,
    ReasonIn,
    StartIn,
)
from app.services import notices as nsvc
from app.services import scrutiny as sc
from app.services.errors import not_found

router = APIRouter(prefix="/api/scrutiny", tags=["scrutiny"], dependencies=[Depends(csrf_protect)])

can_queue = require_permission(Permission.SCRUTINY_QUEUE)
can_assign = require_permission(Permission.SCRUTINY_ASSIGN)
can_determine = require_permission(Permission.SCRUTINY_ROUTE_DETERMINE)
can_work = require_permission(Permission.SCRUTINY_WORK)
can_issue = require_permission(Permission.NOTICE_ISSUE)
can_evidence = require_permission(Permission.EVIDENCE_READ)


def _detail(db: Session, current: CurrentUser, case_id: uuid.UUID, settings: Settings, request: Request) -> CaseDetailOut:
    case, scope = scrutiny_access.get_case(db, current.user, case_id, request)
    return pr.case_detail(db, current.user, case, scope, settings)


# ---- queue and case -----------------------------------------------------------------------------


@router.get("/cases", response_model=QueuePage)
def queue(
    current: CurrentUser = Depends(can_queue), db: Session = Depends(get_db), settings: Settings = Depends(get_settings),
    state: str | None = Query(None, pattern="^[A-Z_]{3,32}$"), processing_exceptions: bool = False, q: str | None = Query(None, max_length=80),
    category_id: uuid.UUID | None = None, academic_year: str | None = Query(None, max_length=9), assigned: str | None = Query(None, pattern="^(me|unassigned)$"),
    page: int = Query(1, ge=1, le=10_000), page_size: int = Query(20, ge=1, le=50),
) -> QueuePage:
    return pr.list_queue(db, current.user, settings, state=state, processing_exceptions=processing_exceptions, q=q, category_id=category_id, academic_year=academic_year, assigned=assigned, page=page, page_size=page_size)


@router.get("/cases/{case_id}", response_model=CaseDetailOut)
def case_detail(case_id: uuid.UUID, request: Request, current: CurrentUser = Depends(can_queue), db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> CaseDetailOut:
    return _detail(db, current, case_id, settings, request)


@router.get("/cases/{case_id}/evidence", response_model=ApplicationEvidenceOut)
def case_evidence(case_id: uuid.UUID, request: Request, current: CurrentUser = Depends(can_evidence), db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> ApplicationEvidenceOut:
    """The Phase 3 evidence package for this case (documents, extracted values with pages, automated preliminary findings)."""
    case, _ = scrutiny_access.get_case(db, current.user, case_id, request, documents=True)
    from app.models_applications import Application

    app = db.get(Application, case.application_id)
    audit.record(db, "evidence.viewed", request=request, actor=current.user, target_type="application", target_id=app.id, detail={"case_id": str(case.id)})
    db.commit()
    return ep.application_evidence(db, app, "reviewer", settings)


@router.get("/handoffs", response_model=list[HandoffListItem])
def handoffs(current: CurrentUser = Depends(can_assign), db: Session = Depends(get_db)) -> list[HandoffListItem]:
    return pr.handoff_list(db)


@router.get("/assignable", response_model=list[AssignableOut])
def assignable(current: CurrentUser = Depends(can_assign), db: Session = Depends(get_db)) -> list[AssignableOut]:
    return pr.assignable(db)


# ---- assignment and hand-off --------------------------------------------------------------------


@router.post("/cases/{case_id}/assign", response_model=CaseDetailOut)
def assign(case_id: uuid.UUID, body: AssignIn, request: Request, current: CurrentUser = Depends(can_assign), db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> CaseDetailOut:
    sc.assign(db, current.user, case_id, body.user_id, body.reason, body.row_version, request, settings)
    db.commit()
    return _detail(db, current, case_id, settings, request)


@router.post("/cases/{case_id}/handoff/determine", response_model=CaseDetailOut)
def determine(case_id: uuid.UUID, body: DetermineIn, request: Request, current: CurrentUser = Depends(can_determine), db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> CaseDetailOut:
    sc.determine_handoff(db, current.user, case_id, body.stage_key, body.reason, request)
    db.commit()
    return _detail(db, current, case_id, settings, request)


# ---- reviewer work ------------------------------------------------------------------------------


@router.post("/cases/{case_id}/start", response_model=CaseDetailOut)
def start(case_id: uuid.UUID, body: StartIn, request: Request, current: CurrentUser = Depends(can_work), db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> CaseDetailOut:
    sc.start(db, current.user, case_id, body.row_version, request)
    db.commit()
    return _detail(db, current, case_id, settings, request)


@router.put("/cases/{case_id}/notes", response_model=CaseDetailOut)
def notes(case_id: uuid.UUID, body: NotesIn, request: Request, current: CurrentUser = Depends(can_work), db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> CaseDetailOut:
    sc.save_notes(db, current.user, case_id, body.notes, body.row_version, request)
    db.commit()
    return _detail(db, current, case_id, settings, request)


@router.post("/cases/{case_id}/findings", response_model=CaseDetailOut, status_code=201)
def create_finding(case_id: uuid.UUID, body: FindingIn, request: Request, current: CurrentUser = Depends(can_work), db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> CaseDetailOut:
    sc.create_finding(db, current.user, case_id, body.model_dump(), request)
    db.commit()
    return _detail(db, current, case_id, settings, request)


@router.patch("/cases/{case_id}/findings/{finding_id}", response_model=CaseDetailOut)
def edit_finding(case_id: uuid.UUID, finding_id: uuid.UUID, body: FindingPatchIn, request: Request, current: CurrentUser = Depends(can_work), db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> CaseDetailOut:
    data = body.model_dump(exclude_unset=True)
    version = data.pop("row_version")
    sc.update_finding(db, current.user, case_id, finding_id, data, version, request)
    db.commit()
    return _detail(db, current, case_id, settings, request)


@router.post("/cases/{case_id}/findings/{finding_id}/disposition", response_model=CaseDetailOut)
def disposition(case_id: uuid.UUID, finding_id: uuid.UUID, body: DispositionIn, request: Request, current: CurrentUser = Depends(can_work), db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> CaseDetailOut:
    sc.disposition(db, current.user, case_id, finding_id, FindingState(body.state), body.reason, body.row_version, request)
    db.commit()
    return _detail(db, current, case_id, settings, request)


@router.post("/cases/{case_id}/complete", response_model=CaseDetailOut)
def complete(case_id: uuid.UUID, body: CompleteIn, request: Request, current: CurrentUser = Depends(can_work), db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> CaseDetailOut:
    sc.complete(db, current.user, case_id, body.row_version, body.summary, body.acknowledge_unresolved, request)
    db.commit()
    return _detail(db, current, case_id, settings, request)


# ---- notices ------------------------------------------------------------------------------------


@router.post("/cases/{case_id}/notices", response_model=CaseDetailOut, status_code=201)
def create_notice(case_id: uuid.UUID, body: NoticeCreateIn, request: Request, current: CurrentUser = Depends(can_work), db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> CaseDetailOut:
    nsvc.create_draft(db, current.user, case_id, body.finding_ids, body.summary, request)
    db.commit()
    return _detail(db, current, case_id, settings, request)


@router.patch("/notices/{notice_id}", response_model=CaseDetailOut)
def update_notice(notice_id: uuid.UUID, body: NoticeUpdateIn, request: Request, current: CurrentUser = Depends(can_work), db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> CaseDetailOut:
    items = [i.model_dump(exclude_unset=True) for i in body.items] if body.items is not None else None
    n = nsvc.update_draft(db, current.user, notice_id, body.summary, items, body.add_finding_ids, body.row_version, request)
    db.commit()
    return _detail(db, current, n.case_id, settings, request)


@router.post("/notices/{notice_id}/submit", response_model=CaseDetailOut)
def submit_notice(notice_id: uuid.UUID, body: NoticeSubmitIn, request: Request, current: CurrentUser = Depends(can_work), db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> CaseDetailOut:
    n = nsvc.submit_for_issuance(db, current.user, notice_id, body.row_version, request)
    db.commit()
    return _detail(db, current, n.case_id, settings, request)


@router.post("/notices/{notice_id}/withdraw", response_model=CaseDetailOut)
def withdraw_notice(notice_id: uuid.UUID, body: ReasonIn, request: Request, current: CurrentUser = Depends(can_work), db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> CaseDetailOut:
    n = nsvc.withdraw(db, current.user, notice_id, body.reason, request)
    db.commit()
    return _detail(db, current, n.case_id, settings, request)


@router.get("/notices/{notice_id}/preview", response_model=NoticePreviewOut)
def preview_notice(notice_id: uuid.UUID, request: Request, current: CurrentUser = Depends(require_any_permission(Permission.SCRUTINY_WORK, Permission.NOTICE_ISSUE)), db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> NoticePreviewOut:
    """Exactly what would be issued, with the hash an issuer must confirm. Visible only to the preparing reviewer and to issuers."""
    n = nsvc.load_notice(db, notice_id)
    _, scope = scrutiny_access.get_case(db, current.user, n.case_id, request)
    if not (scope.kinds & {"reviewer", "issuer"}):
        raise not_found("Notice")
    return pr.preview_out(n, nsvc.preview(db, n, settings))


@router.post("/notices/{notice_id}/issue", response_model=CaseDetailOut)
def issue_notice(notice_id: uuid.UUID, body: IssueIn, request: Request, response: Response, current: CurrentUser = Depends(can_issue), db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> CaseDetailOut:
    n, replay = nsvc.issue(db, current.user, notice_id, body.content_hash, body.issue_reason, body.acknowledge_unsourced, body.idempotency_key, body.row_version, request, settings)
    db.commit()
    if replay:
        response.headers["X-Idempotent-Replay"] = "true"
    return _detail(db, current, n.case_id, settings, request)


@router.post("/notices/{notice_id}/return", response_model=CaseDetailOut)
def return_notice(notice_id: uuid.UUID, body: ReasonIn, request: Request, current: CurrentUser = Depends(can_issue), db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> CaseDetailOut:
    n = nsvc.return_notice(db, current.user, notice_id, body.reason, request)
    db.commit()
    return _detail(db, current, n.case_id, settings, request)
