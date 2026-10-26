"""Shape scrutiny records for the API.

Staff views include internal fields. Institution views are built from the `Inst*` whitelist models and from the
notice's *frozen issued content*, so they cannot show an internal reviewer note even by mistake.
"""

import uuid

from sqlalchemy import exists, func, or_, select
from sqlalchemy.orm import Session, selectinload

from app import evidence_presenters as ep
from app.config import Settings
from app.domain import (
    DEMO_BANNER,
    NOTICE_PREPARING,
    CaseState,
    HandoffStatus,
    NoticeStatus,
    ResponseStatus,
)
from app.evidence import current_documents
from app.models import Institution, User
from app.models_applications import Application, ApplicationDocument
from app.models_catalogue import ApplicationCategory
from app.models_processing import DocumentProcessing, DocumentSlot
from app.models_scrutiny import (
    DeficiencyNotice,
    FindingEvent,
    NoticeItem,
    ScrutinyAssignment,
    ScrutinyCase,
    ScrutinyEvent,
    ScrutinyFinding,
    ScrutinyRound,
    StageHandoff,
)
from app.rbac import Permission, permissions_for
from app.schemas_scrutiny import (
    AssignableOut,
    AssignmentOut,
    CaseDetailOut,
    CaseEventOut,
    FindingEventOut,
    FindingOut,
    HandoffListItem,
    HandoffOut,
    InstEvent,
    InstItem,
    InstNotice,
    InstNoticeSummary,
    InstResponse,
    InstResponseDoc,
    InstScrutiny,
    NoticeItemOut,
    NoticePreviewOut,
    NoticeStaffOut,
    ProcessingSummary,
    QueueItemOut,
    QueuePage,
    ResponseDocOut,
    RuleVersionRef,
    RoundOut,
    SelectionOut,
    SourceOptionOut,
    StaffResponseOut,
)
from app.scrutiny_access import Scope
from app.security import as_utc
from app.services import notices as nsvc
from app.services import responses as rsvc
from app.services import scrutiny as sc
from app.services.applications import config_of

NOT_AN_APPROVAL = "Completing scrutiny is not an approval. Approval or rejection is decided only by the authorised decision process, which is not part of this release."

STATE_LABELS = {
    CaseState.AWAITING_ASSIGNMENT: "Awaiting assignment",
    CaseState.READY_FOR_SCRUTINY: "Ready for scrutiny",
    CaseState.UNDER_SCRUTINY: "Under scrutiny",
    CaseState.AWAITING_NOTICE_DECISION: "Awaiting notice decision",
    CaseState.AWAITING_RESPONSE: "Notice issued — awaiting response",
    CaseState.AWAITING_RESCRUTINY: "Response received — awaiting re-scrutiny",
    CaseState.UNDER_RESCRUTINY: "Under re-scrutiny",
    CaseState.SCRUTINY_COMPLETED: "Scrutiny completed",
    CaseState.CONFIGURATION_EXCEPTION: "Configuration exception",
}

INST_STATE = {
    CaseState.AWAITING_ASSIGNMENT: ("Received — awaiting scrutiny", "Your application was submitted and is waiting for scrutiny to begin. Nothing is needed from you."),
    CaseState.READY_FOR_SCRUTINY: ("Received — awaiting scrutiny", "Your application was submitted and is waiting for scrutiny to begin. Nothing is needed from you."),
    CaseState.UNDER_SCRUTINY: ("Under scrutiny", "Your application is being examined. If something is needed from you, a formal notice will appear here."),
    CaseState.AWAITING_NOTICE_DECISION: ("Under scrutiny", "Your application is being examined. If something is needed from you, a formal notice will appear here."),
    CaseState.AWAITING_RESPONSE: ("Deficiency notice issued — your response is awaited", "A formal deficiency notice has been issued. Open it to see what is requested and respond."),
    CaseState.AWAITING_RESCRUTINY: ("Response received — awaiting re-scrutiny", "Your response was received. It has not been accepted or rejected; re-scrutiny will examine it."),
    CaseState.UNDER_RESCRUTINY: ("Under re-scrutiny", "Your response is being examined. It has not been accepted or rejected yet."),
    CaseState.SCRUTINY_COMPLETED: ("Scrutiny completed", "Scrutiny is complete. This is not an approval; your application moves to its next stage."),
    CaseState.CONFIGURATION_EXCEPTION: ("Being processed", "Your application is being processed. Nothing is needed from you."),
}

NOTICE_STATUS_INST = {
    NoticeStatus.ISSUED.value: "Issued — response awaited",
    NoticeStatus.RESPONSE_RECEIVED.value: "Response submitted — awaiting re-scrutiny",
    NoticeStatus.CLOSED.value: "Re-scrutiny of your response is recorded",
}

INST_EVENT_LABELS = {
    "case_opened": "Application received for scrutiny",
    "notice_issued": "Deficiency notice {reference} issued",
    "response_submitted": "Your response to {reference} was submitted",
    "notice_closed": "Re-scrutiny of {reference} was recorded",
    "scrutiny_completed": "Scrutiny completed (this is not an approval)",
}


# ---- small helpers ------------------------------------------------------------------------------


def _name(db: Session, user_id: uuid.UUID | None) -> str | None:
    u = db.get(User, user_id) if user_id else None
    return u.full_name if u else None


def rule_ref(config: dict) -> RuleVersionRef:
    c = config["rule_set"]["classification"]
    return RuleVersionRef(rule_set=config["rule_set"]["key"], number=config["version"]["number"], classification=c, demo_banner=DEMO_BANNER if c == "DEMO" else None)


def processing_summary(db: Session, app_id: uuid.UUID) -> ProcessingSummary:
    docs = list(current_documents(db, app_id).values())
    procs = [d.processing for d in docs if d.processing is not None]
    return ProcessingSummary(
        documents=len(docs), still_processing=sum(1 for p in procs if p.extraction_status in ("QUEUED", "RUNNING")),
        failed=sum(1 for p in procs if p.extraction_status in ("FAILED", "BLOCKED") or p.technical_status == "FAILED"), review_recommended=sum(1 for p in procs if p.review_recommended),
    )


def _item_label(config: dict, item_key: str | None) -> str | None:
    return next((i["label"] for i in config["checklist"] if i["item_key"] == item_key), None) if item_key else None


def _assignee_names(db: Session, case_id: uuid.UUID) -> list[str]:
    return list(db.scalars(select(User.full_name).join(ScrutinyAssignment, ScrutinyAssignment.user_id == User.id).where(ScrutinyAssignment.case_id == case_id, ScrutinyAssignment.revoked_at.is_(None)).order_by(User.full_name)))


def _pending_findings(db: Session, case_id: uuid.UUID) -> int:
    return db.scalar(select(func.count()).select_from(ScrutinyFinding).where(ScrutinyFinding.case_id == case_id, ScrutinyFinding.state.in_(("OPEN", "NOTICED")))) or 0


# ---- queue --------------------------------------------------------------------------------------


def _outstanding(db: Session, case: ScrutinyCase, proc: ProcessingSummary, handoff: StageHandoff | None, selection_available: bool) -> list[str]:
    s = CaseState(case.state)
    out: list[str] = []
    if s == CaseState.AWAITING_ASSIGNMENT:
        out.append("Assign a reviewer through the authorised process" if selection_available else "Waiting for the authorised selection process (no interface is configured)")
    elif s == CaseState.READY_FOR_SCRUTINY:
        out.append("Reviewer to start scrutiny")
    elif s in (CaseState.UNDER_SCRUTINY, CaseState.UNDER_RESCRUTINY):
        n = _pending_findings(db, case.id)
        out.append(f"{n} finding(s) to conclude or put in a notice" if n else "Record findings or complete the round")
    elif s == CaseState.AWAITING_NOTICE_DECISION:
        out.append("Authorised issuer to inspect and decide on the prepared notice")
    elif s == CaseState.AWAITING_RESPONSE:
        out.append("Waiting for the institution's response")
    elif s == CaseState.AWAITING_RESCRUTINY:
        out.append("Reviewer to start re-scrutiny of the response")
    elif s == CaseState.SCRUTINY_COMPLETED:
        if handoff and handoff.status == HandoffStatus.AWAITING_DETERMINATION.value:
            out.append("Determine the next stage (the route offers a conditional or multiple stages)")
        elif handoff and handoff.status == HandoffStatus.QUEUED.value:
            out.append(f"Hand-off queued for: {handoff.to_stage_label} (that stage's workflow is not available in this release)")
        else:
            out.append("No further stage is configured for this route")
    elif s == CaseState.CONFIGURATION_EXCEPTION:
        out.append("Configuration review needed: " + (case.exception_detail or "the approved route cannot be applied"))
    if proc.still_processing:
        out.append(f"{proc.still_processing} document(s) still being processed")
    if proc.failed:
        out.append(f"{proc.failed} document(s) could not be processed — inspect manually")
    return out


def queue_item(db: Session, case: ScrutinyCase, app: Application, settings: Settings) -> QueueItemOut:
    config = config_of(app)
    proc = processing_summary(db, app.id)
    handoff = db.scalar(select(StageHandoff).where(StageHandoff.case_id == case.id))
    rnd = sc.open_round(db, case.id) or sc.latest_round(db, case.id)
    notice = db.scalar(select(DeficiencyNotice).where(DeficiencyNotice.case_id == case.id, DeficiencyNotice.status == NoticeStatus.ISSUED.value))
    sel = sc.selection_info(config, settings)
    due_basis = None
    if notice is not None and notice.response_due_at is not None:
        pr = notice.policy_resolution or {}
        due_basis = ("DEMO ONLY — " if pr.get("demo") else "") + (pr.get("message") or "Response period from the approved configuration")
    return QueueItemOut(
        id=case.id, application_id=app.id, reference=app.reference, institution_name=app.institution.name, category_name=app.category.name, academic_year=app.academic_year,
        state=case.state, state_label=STATE_LABELS[CaseState(case.state)], assignees=_assignee_names(db, case.id), submitted_at=app.submitted_at, updated_at=case.updated_at,
        outstanding_actions=_outstanding(db, case, proc, handoff, sel.available), due_at=notice.response_due_at if notice else None, due_basis=due_basis, rule_version=rule_ref(config),
        processing=proc, round_number=rnd.number if rnd else None, round_kind=rnd.kind if rnd else None, exception=case.exception_detail if case.state == CaseState.CONFIGURATION_EXCEPTION.value else None,
        handoff_status=handoff.status if handoff else None, handoff_stage=handoff.to_stage_label if handoff else None, row_version=case.row_version,
    )


def _scope_kind(perms: frozenset[Permission]) -> str:
    if Permission.SCRUTINY_ASSIGN in perms:
        return "coordinator"
    if Permission.SCRUTINY_WORK in perms:
        return "reviewer"
    return "issuer"


def _scope_filter(user: User, kind: str):
    if kind == "coordinator":
        return None
    if kind == "reviewer":
        return exists().where(ScrutinyAssignment.case_id == ScrutinyCase.id, ScrutinyAssignment.user_id == user.id, ScrutinyAssignment.revoked_at.is_(None))
    return exists().where(DeficiencyNotice.case_id == ScrutinyCase.id, DeficiencyNotice.submitted_for_issuance_at.is_not(None))


def _processing_exception():
    return exists().where(
        ApplicationDocument.application_id == ScrutinyCase.application_id, ApplicationDocument.removed_at.is_(None), ApplicationDocument.superseded_at.is_(None),
        DocumentProcessing.document_id == ApplicationDocument.id, or_(DocumentProcessing.extraction_status.in_(("FAILED", "BLOCKED")), DocumentProcessing.technical_status == "FAILED"),
    )


def list_queue(
    db: Session, user: User, settings: Settings, *, state: str | None, processing_exceptions: bool, q: str | None, category_id: uuid.UUID | None, academic_year: str | None,
    assigned: str | None, page: int, page_size: int,
) -> QueuePage:
    kind = _scope_kind(permissions_for(user.role))
    scope = _scope_filter(user, kind)
    base = select(ScrutinyCase).join(Application, Application.id == ScrutinyCase.application_id).join(Institution, Institution.id == Application.institution_id)
    if scope is not None:
        base = base.where(scope)
    counts = {s.value: 0 for s in CaseState}
    for st, n in db.execute(select(ScrutinyCase.state, func.count()).join(Application, Application.id == ScrutinyCase.application_id).where(*( [scope] if scope is not None else [] )).group_by(ScrutinyCase.state)):
        counts[st] = n
    exc_count = db.scalar(select(func.count()).select_from(ScrutinyCase).where(*([scope] if scope is not None else []), _processing_exception())) or 0

    f = base
    if state:
        f = f.where(ScrutinyCase.state == state)
    if processing_exceptions:
        f = f.where(_processing_exception())
    if category_id:
        f = f.where(Application.category_id == category_id)
    if academic_year:
        f = f.where(Application.academic_year == academic_year)
    if q and q.strip():
        term = q.strip().replace("%", "").replace("_", "")
        f = f.where(or_(Application.reference.ilike(f"%{term.upper()}%"), Institution.name.ilike(f"%{term}%")))
    if assigned == "me":
        f = f.where(exists().where(ScrutinyAssignment.case_id == ScrutinyCase.id, ScrutinyAssignment.user_id == user.id, ScrutinyAssignment.revoked_at.is_(None)))
    elif assigned == "unassigned":
        f = f.where(~exists().where(ScrutinyAssignment.case_id == ScrutinyCase.id, ScrutinyAssignment.revoked_at.is_(None)))
    total = db.scalar(select(func.count()).select_from(f.subquery())) or 0
    rows = db.scalars(f.order_by(Application.submitted_at.asc(), ScrutinyCase.created_at.asc()).limit(page_size).offset((page - 1) * page_size)).all()
    items = []
    for c in rows:
        app = db.scalars(select(Application).where(Application.id == c.application_id).options(selectinload(Application.institution), selectinload(Application.category))).one()
        items.append(queue_item(db, c, app, settings))
    return QueuePage(items=items, total=total, page=page, page_size=page_size, counts=counts, state_labels={s.value: STATE_LABELS[s] for s in CaseState}, processing_exceptions=exc_count, scope=kind)


def handoff_list(db: Session) -> list[HandoffListItem]:
    rows = db.execute(
        select(StageHandoff, ScrutinyCase, Application, Institution, ApplicationCategory)
        .join(ScrutinyCase, ScrutinyCase.id == StageHandoff.case_id).join(Application, Application.id == StageHandoff.application_id)
        .join(Institution, Institution.id == Application.institution_id).join(ApplicationCategory, ApplicationCategory.id == Application.category_id).order_by(StageHandoff.created_at)
    ).all()
    return [HandoffListItem(case_id=h.case_id, reference=a.reference, institution_name=i.name, category_name=c.name, status=h.status, to_stage_label=h.to_stage_label, reason=h.reason, completed_at=k.completed_at) for h, k, a, i, c in rows]


def assignable(db: Session) -> list[AssignableOut]:
    users = db.scalars(select(User).where(User.role == "scrutiny_officer", User.is_active.is_(True)).order_by(User.full_name)).all()
    out = []
    for u in users:
        n = db.scalar(select(func.count()).select_from(ScrutinyAssignment).join(ScrutinyCase, ScrutinyCase.id == ScrutinyAssignment.case_id).where(
            ScrutinyAssignment.user_id == u.id, ScrutinyAssignment.revoked_at.is_(None), ScrutinyCase.state != CaseState.SCRUTINY_COMPLETED.value)) or 0
        out.append(AssignableOut(id=u.id, name=u.full_name, email=u.email, active_cases=n))
    return out


# ---- case detail --------------------------------------------------------------------------------


def _doc_info(db: Session, doc_ids: set[uuid.UUID]) -> dict[uuid.UUID, tuple[ApplicationDocument, int | None, str]]:
    """document id -> (document, the slot's current version number, display state label)."""
    if not doc_ids:
        return {}
    docs = db.scalars(select(ApplicationDocument).where(ApplicationDocument.id.in_(doc_ids)).options(selectinload(ApplicationDocument.processing))).all()
    jobs = ep._jobs_by_doc(db, [d.id for d in docs])
    slots = {s.id: s for s in db.scalars(select(DocumentSlot).where(DocumentSlot.id.in_({d.slot_id for d in docs})))}
    cur_ids = {s.current_version_id for s in slots.values() if s.current_version_id}
    cur = {d.id: d.version_number for d in db.scalars(select(ApplicationDocument).where(ApplicationDocument.id.in_(cur_ids)))} if cur_ids else {}
    return {d.id: (d, cur.get(slots[d.slot_id].current_version_id) if d.slot_id in slots else None, ep.state_out(d, jobs.get(d.id)).label) for d in docs}


def finding_out(db: Session, f: ScrutinyFinding, config: dict, docs: dict, rounds: dict[uuid.UUID, int], notice_ref: str | None) -> FindingOut:
    d = docs.get(f.document_id) if f.document_id else None
    events = db.scalars(select(FindingEvent).where(FindingEvent.finding_id == f.id).options(selectinload(FindingEvent.actor)).order_by(FindingEvent.at)).all()
    return FindingOut(
        id=f.id, sequence=f.sequence, round_number=rounds.get(f.round_id, 0), item_key=f.item_key, item_label=_item_label(config, f.item_key), document_id=f.document_id,
        document_version=d[0].version_number if d else None, document_current_version=d[1] if d else None, document_superseded=bool(d and d[0].superseded_at is not None),
        page_number=f.page_number, issue_type=f.issue_type, issue_label=sc.ISSUE_TYPES.get(f.issue_type, f.issue_type), description=f.description, institution_text=f.institution_text,
        requested_correction=f.requested_correction, source_ref=f.source_ref, source_ref_status=f.source_ref_status, preliminary_finding_id=f.preliminary_finding_id,
        visibility=f.visibility, state=f.state, state_reason=f.state_reason, created_by=f.created_by.full_name, created_at=f.created_at, updated_at=f.updated_at, row_version=f.row_version,
        notice_reference=notice_ref,
        history=[FindingEventOut(at=e.at, actor=e.actor.full_name if e.actor else "System", action=e.action, from_state=e.from_state, to_state=e.to_state, reason=e.reason, round_number=rounds.get(e.round_id, 0)) for e in events],
    )


def notice_staff(db: Session, n: DeficiencyNotice, docs: dict, rounds: dict[uuid.UUID, int], finding_states: dict[uuid.UUID, str]) -> NoticeStaffOut:
    resp = rsvc.response_of(db, n.id)
    prev = db.get(DeficiencyNotice, n.previous_notice_id) if n.previous_notice_id else None
    items = []
    for it in n.items:
        row = next((r for r in resp.items if r.notice_item_id == it.id), None) if resp else None
        d = docs.get(it.document_id) if it.document_id else None
        rdocs = []
        for rd in (row.documents if row else []):
            info = docs.get(rd.document_id)
            if info:
                rdocs.append(ResponseDocOut(document_id=rd.document_id, version_number=info[0].version_number, filename=info[0].original_filename, uploaded_at=info[0].uploaded_at, current=info[0].superseded_at is None and info[0].removed_at is None, state_label=info[2]))
        items.append(NoticeItemOut(
            id=it.id, position=it.position, finding_id=it.finding_id, finding_state=finding_states.get(it.finding_id, ""), title=it.title, description=it.description, requested_correction=it.requested_correction,
            item_key=it.item_key, document_id=it.document_id, document_version=it.document_version, document_current_version=d[1] if d else None, page_number=it.page_number,
            source_ref=it.source_ref, source_ref_status=it.source_ref_status, response_state=rsvc.item_state(resp, it, finding_states.get(it.finding_id)).value,
            response_explanation=(row.explanation if row else "") or "", response_documents=rdocs,
        ))
    sresp = None
    if resp is not None:
        due = as_utc(n.response_due_at) if n.response_due_at else None
        sresp = StaffResponseOut(id=resp.id, status=resp.status, explanation=resp.explanation, submitted_by=resp.submitted_by.full_name if resp.submitted_by else None, submitted_at=resp.submitted_at,
                                 after_due_date=bool(due and resp.submitted_at and as_utc(resp.submitted_at) > due))
    return NoticeStaffOut(
        id=n.id, reference=n.reference, sequence=n.sequence, previous_reference=prev.reference if prev else None, status=n.status, round_number=rounds.get(n.round_id, 0), summary=n.summary,
        items=items, prepared_by=n.prepared_by.full_name, issued_by=n.issued_by.full_name if n.issued_by else None, issuer_role=n.issuer_role, issued_at=n.issued_at, issue_reason=n.issue_reason,
        returned_reason=n.returned_reason, withdrawn_reason=n.withdrawn_reason, response_due_at=n.response_due_at, policy_resolution=n.policy_resolution, template=n.template,
        content_hash=n.content_hash, authority=n.authority, row_version=n.row_version, response=sresp,
    )


def preview_out(n: DeficiencyNotice, pv: nsvc.Preview) -> NoticePreviewOut:
    return NoticePreviewOut(
        notice_id=n.id, content=pv.content, content_hash=pv.content_hash, policy=pv.policy.stable(), blockers=[{"code": b.code, "message": b.message} for b in pv.blockers],
        unsourced_positions=pv.unsourced, can_issue=pv.can_issue and n.status == NoticeStatus.IN_REVIEW.value, row_version=n.row_version, notice_status=n.status,
    )


def _actions(case: ScrutinyCase, scope: Scope, selection_available: bool, has_draft: bool, handoff: StageHandoff | None, notice_in_review: bool) -> list[str]:
    s = CaseState(case.state)
    a: list[str] = []
    if "coordinator" in scope:
        if s == CaseState.AWAITING_ASSIGNMENT and selection_available:
            a.append("assign")
        if s in sc.REASSIGNABLE and selection_available:
            a.append("reassign")
        if handoff is not None and handoff.status == HandoffStatus.AWAITING_DETERMINATION.value:
            a.append("determine_handoff")
    if "reviewer" in scope:
        if s in (CaseState.READY_FOR_SCRUTINY, CaseState.AWAITING_RESCRUTINY):
            a.append("start")
        if s in (CaseState.UNDER_SCRUTINY, CaseState.UNDER_RESCRUTINY):
            a += ["record_finding", "edit_finding", "conclude_finding", "save_notes", "complete"]
            a.append("edit_notice" if has_draft else "prepare_notice")
            if has_draft:
                a += ["submit_notice", "withdraw_notice"]
        if s == CaseState.AWAITING_NOTICE_DECISION and notice_in_review:
            a.append("withdraw_notice")
    if "issuer" in scope and s == CaseState.AWAITING_NOTICE_DECISION and notice_in_review:
        a += ["issue_notice", "return_notice"]
    return a


def case_detail(db: Session, user: User, case: ScrutinyCase, scope: Scope, settings: Settings) -> CaseDetailOut:
    app = db.scalars(select(Application).where(Application.id == case.application_id).options(selectinload(Application.institution), selectinload(Application.category))).one()
    config = config_of(app)
    sel = sc.selection_info(config, settings)
    rounds_rows = db.scalars(select(ScrutinyRound).where(ScrutinyRound.case_id == case.id).order_by(ScrutinyRound.number)).all()
    rounds = {r.id: r.number for r in rounds_rows}
    notices = db.scalars(select(DeficiencyNotice).where(DeficiencyNotice.case_id == case.id).options(selectinload(DeficiencyNotice.items), selectinload(DeficiencyNotice.prepared_by), selectinload(DeficiencyNotice.issued_by)).order_by(DeficiencyNotice.sequence)).all()
    if "coordinator" in scope and not (scope.kinds & {"reviewer", "issuer"}):
        notices = []  # the coordinator manages the queue; notice content is for reviewers and issuers
    findings = [] if (scope.kinds == {"coordinator"}) else list(db.scalars(select(ScrutinyFinding).where(ScrutinyFinding.case_id == case.id).options(selectinload(ScrutinyFinding.created_by)).order_by(ScrutinyFinding.sequence)))
    doc_ids = {f.document_id for f in findings if f.document_id} | {i.document_id for n in notices for i in n.items if i.document_id}
    for n in notices:
        resp = rsvc.response_of(db, n.id)
        doc_ids |= {rd.document_id for r in (resp.items if resp else []) for rd in r.documents}
    docs = _doc_info(db, doc_ids)
    finding_states = {f.id: f.state for f in findings}
    notice_of: dict[uuid.UUID, str] = {}
    for n in notices:
        if n.status != NoticeStatus.WITHDRAWN.value:
            for it in n.items:
                notice_of[it.finding_id] = n.reference
    handoff = db.scalar(select(StageHandoff).where(StageHandoff.case_id == case.id))
    draft = any(n.status in NOTICE_PREPARING and n.status == NoticeStatus.DRAFT.value for n in notices)
    in_review = any(n.status == NoticeStatus.IN_REVIEW.value for n in notices)
    assignments = db.scalars(select(ScrutinyAssignment).where(ScrutinyAssignment.case_id == case.id).options(selectinload(ScrutinyAssignment.user)).order_by(ScrutinyAssignment.created_at)).all()
    events = db.scalars(select(ScrutinyEvent).where(ScrutinyEvent.case_id == case.id).order_by(ScrutinyEvent.at)).all()
    cur_docs = current_documents(db, app.id)
    checklist = []
    for i in config["checklist"]:
        d = cur_docs.get(i["item_key"])
        checklist.append({
            "item_key": i["item_key"], "label": i["label"], "explanation": i["explanation"], "required": i["required"], "item_type": i["item_type"], "clause_ref": i.get("clause_ref"),
            "answer": (app.form_data or {}).get(i["item_key"]) if i["item_type"] != "document_upload" else None,
            "document_id": str(d.id) if d else None, "document_version": d.version_number if d else None, "document_filename": d.original_filename if d else None,
        })
    h = None
    if handoff is not None:
        h = HandoffOut(
            id=handoff.id, status=handoff.status, from_stage_key=handoff.from_stage_key, to_stage_key=handoff.to_stage_key, to_stage_label=handoff.to_stage_label, reason=handoff.reason,
            options=(handoff.route_basis or {}).get("options", []), determined_by=_name(db, handoff.determined_by_id), determined_at=handoff.determined_at,
            determination_reason=handoff.determination_reason, created_at=handoff.created_at, module_available=False,
        )
    return CaseDetailOut(
        queue=queue_item(db, case, app, settings), scope=sorted(scope.kinds), selection=SelectionOut(mode=sel.mode, available=sel.available, label=sel.label, notice=sel.notice),
        assignments=[AssignmentOut(id=a.id, user_id=a.user_id, user_name=a.user.full_name, source=a.source, is_demo=a.is_demo, made_by=a.made_by_label, selection_ref=a.selection_ref, reason=a.reason,
                                   created_at=a.created_at, revoked_at=a.revoked_at, revoked_reason=a.revoked_reason) for a in assignments],
        rounds=[RoundOut(id=r.id, number=r.number, kind=r.kind, status=r.status, opened_at=r.opened_at, started_at=r.started_at, closed_at=r.closed_at,
                         triggered_by_notice=next((n.reference for n in notices if n.id == r.triggered_by_notice_id), None), outcome=r.outcome, outcome_reason=r.outcome_reason,
                         review_notes=r.review_notes if "reviewer" in scope else None, review_notes_at=r.review_notes_at if "reviewer" in scope else None, row_version=r.row_version) for r in rounds_rows],
        findings=[finding_out(db, f, config, docs, rounds, notice_of.get(f.id)) for f in findings],
        notices=[notice_staff(db, n, docs, rounds, finding_states) for n in notices],
        source_options=[SourceOptionOut(**o) for o in sc.source_options(config)], issue_types=sc.ISSUE_TYPES,
        events=[CaseEventOut(id=e.id, at=e.at, actor=e.actor_label, action=e.action, object_type=e.object_type, object_id=e.object_id, from_state=e.from_state, to_state=e.to_state, reason=e.reason, detail=e.detail) for e in events],
        allowed_actions=_actions(case, scope, sel.available, draft, handoff, in_review), completion=case.completion, handoff=h, checklist=checklist, not_an_approval=NOT_AN_APPROVAL,
    )


# ---- institution views --------------------------------------------------------------------------


def _source_texts(it: NoticeItem) -> tuple[str | None, str]:
    s = it.source_ref
    if it.source_ref_status != "ESTABLISHED" or not s:
        return None, "No source reference has been established for this item."
    label = s["label"] + (f" — {s['clause_ref']}" if s.get("clause_ref") else "")
    ver = s.get("verification_status")
    note = "Source verified as official." if ver == "VERIFIED_OFFICIAL" else "This source reference has not been verified as official."
    return label, note


def inst_notice(db: Session, n: DeficiencyNotice, app: Application) -> InstNotice:
    config = config_of(app)
    snap = n.content_snapshot or {}
    resp = rsvc.response_of(db, n.id)
    cur = current_documents(db, app.id)
    doc_ids = {rd.document_id for r in (resp.items if resp else []) for rd in r.documents}
    docs = _doc_info(db, doc_ids)
    prev = db.get(DeficiencyNotice, n.previous_notice_id) if n.previous_notice_id else None
    findings = {f.id: f.state for f in db.scalars(select(ScrutinyFinding).where(ScrutinyFinding.id.in_([i.finding_id for i in n.items])))}
    items: list[InstItem] = []
    for it in n.items:
        row = next((r for r in resp.items if r.notice_item_id == it.id), None) if resp else None
        label, note = _source_texts(it)
        rdocs = []
        for rd in (row.documents if row else []):
            info = docs.get(rd.document_id)
            if info:
                rdocs.append(InstResponseDoc(document_id=rd.document_id, version_number=info[0].version_number, filename=info[0].original_filename, uploaded_at=info[0].uploaded_at,
                                             current=info[0].superseded_at is None and info[0].removed_at is None, state_label=info[2]))
        cd = cur.get(it.item_key) if it.item_key else None
        is_upload = bool(it.item_key and next((c for c in config["checklist"] if c["item_key"] == it.item_key and c["item_type"] == "document_upload"), None))
        items.append(InstItem(
            id=it.id, position=it.position, title=it.title, description=it.description, requested_correction=it.requested_correction, item_key=it.item_key, item_label=_item_label(config, it.item_key),
            accepts_files=is_upload, document_version=it.document_version, document_current_version=cd.version_number if cd else None, page_number=it.page_number, source_label=label, source_note=note,
            response_state=rsvc.item_state(resp, it, findings.get(it.finding_id)).value, response_explanation=(row.explanation if row else "") or "", response_documents=rdocs,
        ))
    pr = n.policy_resolution or {}
    if n.response_due_at:
        due_note = ("DEMO ONLY — " if pr.get("demo") else "") + "Respond by the date shown."
    else:
        due_note = "No response deadline has been established for this notice." + (" (DEMO ONLY)" if pr.get("demo") else "")
    template = snap.get("template")
    return InstNotice(
        id=n.id, reference=n.reference, sequence=n.sequence, previous_reference=prev.reference if prev else None, status=n.status, status_label=NOTICE_STATUS_INST.get(n.status, n.status),
        application_id=app.id, application_reference=app.reference, issued_at=n.issued_at, response_due_at=n.response_due_at, due_note=due_note, demo_notice=snap.get("demo_notice"),
        template_label=template["label"] if template else None, summary=snap.get("summary", n.summary), items=items,
        response=InstResponse(status=resp.status, explanation=resp.explanation, row_version=resp.row_version, submitted_at=resp.submitted_at, submitted_by=resp.submitted_by.full_name if resp.submitted_by else None) if resp else None,
        can_respond=n.status == NoticeStatus.ISSUED.value and not (resp and resp.status == ResponseStatus.SUBMITTED.value), not_an_approval="A deficiency notice is a request for information. It is not a rejection, and your response is not accepted until re-scrutiny has examined it.",
    )


def inst_notice_summaries(db: Session, app: Application) -> list[InstNoticeSummary]:
    out = []
    for n in db.scalars(select(DeficiencyNotice).where(DeficiencyNotice.application_id == app.id, DeficiencyNotice.status.in_(("ISSUED", "RESPONSE_RECEIVED", "CLOSED"))).options(selectinload(DeficiencyNotice.items)).order_by(DeficiencyNotice.sequence)):
        resp = rsvc.response_of(db, n.id)
        answered = sum(1 for it in n.items if rsvc.item_state(resp, it, None) in ("SUBMITTED", "REVIEWED"))
        out.append(InstNoticeSummary(id=n.id, reference=n.reference, sequence=n.sequence, status=n.status, status_label=NOTICE_STATUS_INST[n.status], application_id=app.id,
                                     application_reference=app.reference, issued_at=n.issued_at, response_due_at=n.response_due_at, items=len(n.items), items_answered=answered))
    return out


def inst_scrutiny(db: Session, app: Application) -> InstScrutiny:
    case = db.scalar(select(ScrutinyCase).where(ScrutinyCase.application_id == app.id))
    label, expl = INST_STATE[CaseState(case.state)] if case else ("Not yet submitted", "Scrutiny begins after the application is submitted.")
    events = []
    if case:
        for e in db.scalars(select(ScrutinyEvent).where(ScrutinyEvent.case_id == case.id, ScrutinyEvent.institution_visible.is_(True)).order_by(ScrutinyEvent.at)):
            tpl = INST_EVENT_LABELS.get(e.action)
            if tpl:
                events.append(InstEvent(at=e.at, label=tpl.format(reference=(e.detail or {}).get("reference", ""))))
    return InstScrutiny(application_id=app.id, state_label=label, explanation=expl, events=events, notices=inst_notice_summaries(db, app), not_an_approval=NOT_AN_APPROVAL)
