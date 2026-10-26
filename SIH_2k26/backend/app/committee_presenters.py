"""Shape committee cases for the API. Every state shown here is read from a persisted record; nothing is inferred or decorated.

Staff see everything about a case they have scope for. The institution sees only what the process puts to it: schedules, the compliance requests addressed to
it, and (once the EVC stage is complete) the report's text without internal notes. Nothing here ever says "approved": a report or a recommendation is a record.
"""

import uuid
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.domain import CommitteeStage, CState
from app.models import User
from app.models_applications import Application, ApplicationDocument
from app.models_committee import (
    CommitteeAssignment,
    CommitteeCase,
    CommitteeEventRow,
    CommitteeReport,
    CommitteeSchedule,
    DecisionHandoff,
    RouteTransition,
    SelectionRequest,
)
from app.models_scrutiny import ScrutinyCase, StageHandoff
from app.rbac import Permission
from app.security import as_utc
from app.services import committee_policy as cpolicy
from app.services import evc as evc_svc
from app.services import scheduling, selection
from app.services import shc as shc_svc

NOT_AN_APPROVAL = "This stage produces a record for the later decision process. It is not an approval, a rejection or a decision."

STATE_LABELS = {
    "SELECTION_PENDING": "Awaiting authorised selection", "SELECTION_REQUESTED": "Selection requested — awaiting result", "SCHEDULE_PENDING": "Awaiting schedule confirmation",
    "SCHEDULED": "Visit scheduled", "VISIT_IN_PROGRESS": "Visit in progress — report being prepared", "REPORT_SUBMITTED": "Report submitted — awaiting stage completion",
    "PREPARATION": "Being prepared by the coordinator", "AWAITING_COMPLIANCE": "Awaiting institution compliance", "COMPLIANCE_RECEIVED": "Compliance received — awaiting review",
    "UNDER_REVIEW": "Under review", "RECOMMENDATION_SUBMITTED": "Recommendation submitted — awaiting stage completion",
}
COMPLETED_LABEL = {"EVC": "EVC stage completed (not an approval)", "SHC": "SHC stage completed (not a decision)"}
INSTITUTION_LABELS = {
    "EVC": {"SELECTION_PENDING": "Being arranged", "SELECTION_REQUESTED": "Being arranged", "SCHEDULE_PENDING": "Visit date being arranged", "SCHEDULED": "Visit scheduled", "VISIT_IN_PROGRESS": "Visit under way",
            "REPORT_SUBMITTED": "Visit finished; the report is being finalised", "COMPLETED": "Expert visit stage complete"},
    "SHC": {"PREPARATION": "Being prepared", "AWAITING_COMPLIANCE": "Waiting for your compliance response", "COMPLIANCE_RECEIVED": "Compliance received; awaiting review", "UNDER_REVIEW": "Under review",
            "RECOMMENDATION_SUBMITTED": "Review finished; being completed", "COMPLETED": "Review complete; the case moves to the next stage"},
}


def state_label(case: CommitteeCase) -> str:
    return COMPLETED_LABEL[case.stage] if case.state == CState.COMPLETED.value else STATE_LABELS.get(case.state, case.state)


def _local(dt, tz: str) -> str | None:
    return as_utc(dt).astimezone(ZoneInfo(tz)).strftime("%Y-%m-%dT%H:%M") if dt else None


def _doc(d: ApplicationDocument | None) -> dict | None:
    return {"id": str(d.id), "item_key": d.item_key, "version_number": d.version_number, "filename": d.original_filename} if d else None


def schedule_out(s: CommitteeSchedule) -> dict:
    return {
        "id": s.id, "kind": s.kind, "sequence": s.sequence, "status": s.status, "start_at": as_utc(s.start_at), "timezone": s.timezone, "start_local": _local(s.start_at, s.timezone), "mode": s.mode, "mode_reason": s.mode_reason,
        "location": s.location, "access_details": s.access_details, "supersedes_id": s.supersedes_id, "change_reason": s.change_reason, "created_at": s.created_at, "confirmed_at": s.confirmed_at,
        "closed_at": s.closed_at, "closed_reason": s.closed_reason, "row_version": s.row_version, "policy": s.policy_ref,
    }


def schedule_for_institution(s: CommitteeSchedule) -> dict:
    return {k: v for k, v in schedule_out(s).items() if k in ("id", "kind", "status", "start_at", "timezone", "start_local", "mode", "location", "access_details", "closed_reason", "change_reason")}


def _app_summary(db: Session, app: Application) -> dict:
    return {"id": app.id, "reference": app.reference, "institution_name": app.institution.name, "category_name": app.category.name, "academic_year": app.academic_year, "status": app.status}


# ---- queue ------------------------------------------------------------------------------------------------


def _visible(db: Session, user: User, stage: CommitteeStage):
    """The select of cases this user may see in this stage: every case for a coordinator, only assigned cases for a member."""
    from app.rbac import permissions_for

    perms = permissions_for(user.role)
    coord = Permission.EVC_COORDINATE if stage == CommitteeStage.EVC else Permission.SHC_COORDINATE
    q = select(CommitteeCase).where(CommitteeCase.stage == stage.value)
    if coord in perms:
        return q, "coordinator"
    return q.where(CommitteeCase.id.in_(select(CommitteeAssignment.case_id).where(CommitteeAssignment.user_id == user.id, CommitteeAssignment.revoked_at.is_(None)))), "member"


def case_row(db: Session, case: CommitteeCase) -> dict:
    app = db.get(Application, case.application_id)
    sch = scheduling.active(db, case.id)
    people = [a.user.full_name for a in selection.active_assignments(db, case.id)]
    rt = db.scalar(select(RouteTransition).where(RouteTransition.application_id == app.id, RouteTransition.from_stage_key == case.trigger.get("stage_key")))
    pol = case.policy_snapshot
    return {
        "id": case.id, "stage": case.stage, "application": _app_summary(db, app), "state": case.state, "state_label": state_label(case), "row_version": case.row_version, "activated_at": case.activated_at,
        "assignees": people, "schedule": {"status": sch.status, "start_at": sch.start_at, "start_local": _local(sch.start_at, sch.timezone), "timezone": sch.timezone, "mode": sch.mode} if sch else None,
        "blocked": bool(case.blockers), "blockers": [b["code"] for b in (case.blockers or [])], "demo": bool(pol and pol["classification"] == "DEMO"),
        "awaiting_determination": bool(rt and rt.status == "AWAITING_DETERMINATION"), "completed_at": case.completed_at,
    }


def list_queue(db: Session, user: User, stage: CommitteeStage, *, state: str | None, q: str | None, category_id: uuid.UUID | None, mine: bool, blocked: bool, visit_from, visit_to, page: int, page_size: int) -> dict:
    base, scope = _visible(db, user, stage)
    ids = base.with_only_columns(CommitteeCase.id).subquery()
    counts = dict(db.execute(select(CommitteeCase.state, func.count()).where(CommitteeCase.id.in_(select(ids.c.id))).group_by(CommitteeCase.state)).all())
    stmt = base.join(Application, Application.id == CommitteeCase.application_id)
    if state:
        stmt = stmt.where(CommitteeCase.state == state)
    if category_id:
        stmt = stmt.where(Application.category_id == category_id)
    if q:
        stmt = stmt.where(Application.reference.ilike(f"%{q}%"))
    if blocked:
        stmt = stmt.where(CommitteeCase.blockers.is_not(None))
    if mine:
        stmt = stmt.where(CommitteeCase.id.in_(select(CommitteeAssignment.case_id).where(CommitteeAssignment.user_id == user.id, CommitteeAssignment.revoked_at.is_(None))))
    if visit_from or visit_to:
        sub = select(CommitteeSchedule.case_id).where(CommitteeSchedule.status.in_(("PROPOSED", "CONFIRMED")))
        if visit_from:
            sub = sub.where(CommitteeSchedule.start_at >= visit_from)
        if visit_to:
            sub = sub.where(CommitteeSchedule.start_at <= visit_to)
        stmt = stmt.where(CommitteeCase.id.in_(sub))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = list(db.scalars(stmt.order_by(CommitteeCase.activated_at.desc()).limit(page_size).offset((page - 1) * page_size)))
    return {"scope": scope, "total": total, "page": page, "page_size": page_size, "counts": counts, "items": [case_row(db, c) for c in rows]}


# ---- detail -----------------------------------------------------------------------------------------------


def _selection_view(db: Session, case: CommitteeCase, settings: Settings) -> dict:
    avail = selection.availability(db, case, settings)
    reqs = list(db.scalars(select(SelectionRequest).where(SelectionRequest.case_id == case.id).order_by(SelectionRequest.requested_at)))
    return {
        "availability": {"available": avail.available, "mode": avail.mode, "label": avail.label, "notice": avail.notice, "demo": avail.demo},
        "requests": [{"id": r.id, "request_ref": r.request_ref, "provider": r.provider, "demo": r.is_demo, "status": r.status, "requested_at": r.requested_at, "result_at": r.result_at, "result_source": r.result_source,
                      "members": (r.result or {}).get("members", []), "error_code": r.error_code, "error_detail": r.error_detail, "external_ref": r.external_ref} for r in reqs],
        "assignees": [{"user_id": a.user_id, "name": a.user.full_name, "source": a.source, "demo": a.is_demo, "since": a.created_at} for a in selection.active_assignments(db, case.id)],
        "min_members": int((core_policy(case).get("composition", {}) or {}).get("min_members") or 1),
    }


def core_policy(case: CommitteeCase) -> dict:
    return (case.policy_snapshot or {}).get("content", {})


def _policy_view(case: CommitteeCase) -> dict | None:
    p = case.policy_snapshot
    if not p:
        return None
    c = p["content"]
    return {"id": p["id"], "key": p["key"], "version": p["version"], "classification": p["classification"], "title": p["title"], "banner": cpolicy.verification_note(p), "sources": p["sources"],
            "content_hash": p["content_hash"], "modes": cpolicy.mode_options(p, CommitteeStage(case.stage)), "unverified_modes": c.get("visit", {}).get("unverified_modes", []),
            "checklist": c.get("checklist", []), "report_sections": c.get("report", {}).get("sections", []), "shc": c.get("shc"), "composition": c.get("composition")}


def _report_out(r: CommitteeReport, full: bool = True) -> dict:
    out = {"id": r.id, "kind": r.kind, "version_number": r.version_number, "status": r.status, "content": r.content, "row_version": r.row_version, "created_at": r.created_at, "submitted_at": r.submitted_at,
           "submitted_by": r.submitted_by.full_name if r.submitted_by else None, "created_by": r.created_by.full_name, "content_hash": r.content_hash, "supersedes_id": r.supersedes_id, "amendment_reason": r.amendment_reason}
    if full:
        out["source_snapshot"] = r.source_snapshot
    return out


def _events(db: Session, case_id: uuid.UUID, institution_only: bool = False) -> list[dict]:
    q = select(CommitteeEventRow).where(CommitteeEventRow.case_id == case_id).order_by(CommitteeEventRow.at)
    if institution_only:
        q = q.where(CommitteeEventRow.institution_visible.is_(True))
    return [{"at": e.at, "actor": e.actor_label, "action": e.action, "from_state": e.from_state, "to_state": e.to_state, "reason": e.reason, "detail": e.detail if not institution_only else None} for e in db.scalars(q)]


def _transition_view(db: Session, case: CommitteeCase) -> dict | None:
    rt = db.scalar(select(RouteTransition).where(RouteTransition.application_id == case.application_id, RouteTransition.from_stage_key == case.trigger.get("stage_key")))
    if rt is None:
        return None
    return {"status": rt.status, "to_stage_key": rt.to_stage_key, "to_stage_label": rt.to_stage_label, "reason": rt.reason, "options": (rt.route_basis or {}).get("options", []), "determined_at": rt.determined_at,
            "determination_reason": rt.determination_reason, "route_basis": {k: v for k, v in (rt.route_basis or {}).items() if k != "options"}}


def actions_for(db: Session, case: CommitteeCase, scope, avail: selection.Availability, sch: CommitteeSchedule | None) -> list[str]:
    s = CState(case.state)
    a: list[str] = []
    stage = CommitteeStage(case.stage)
    blocked = {b["code"] for b in (case.blockers or [])}
    if "coordinator" in scope:
        if stage == CommitteeStage.EVC:
            if s == CState.SELECTION_PENDING and avail.available:
                a.append("request_selection")
            if s == CState.SELECTION_REQUESTED:
                a += ["refresh_selection", "cancel_selection"]
            if s in (CState.SCHEDULE_PENDING, CState.SCHEDULED):
                if not blocked & {"no_committee_policy", "no_visit_modes"}:
                    if sch is None:
                        a.append("propose_schedule")
                    else:
                        a.append("reschedule")
                        if sch.status == "PROPOSED":
                            a.append("confirm_schedule")
                if sch is not None:
                    a.append("cancel_schedule")
                a.append("reselect")
            if s == CState.REPORT_SUBMITTED:
                a += ["complete", "open_amendment"]
        else:
            if s in (CState.PREPARATION, CState.AWAITING_COMPLIANCE, CState.COMPLIANCE_RECEIVED, CState.UNDER_REVIEW):
                if avail.available and not db.scalar(select(SelectionRequest.id).where(SelectionRequest.case_id == case.id, SelectionRequest.status == "REQUESTED")):
                    a.append("request_selection")
                if db.scalar(select(SelectionRequest.id).where(SelectionRequest.case_id == case.id, SelectionRequest.status == "REQUESTED")):
                    a += ["refresh_selection", "cancel_selection"]
                if (core_policy(case).get("shc", {}).get("hearing") in ("OPTIONAL", "REQUIRED")) and not blocked & {"no_committee_policy"}:
                    if sch is None:
                        a.append("propose_schedule")
                    else:
                        a.append("reschedule")
                        if sch.status == "PROPOSED":
                            a.append("confirm_schedule")
                        a.append("cancel_schedule")
            if s in (CState.PREPARATION, CState.UNDER_REVIEW):
                a += ["add_requirement", "edit_requirement"]
                if core_policy(case).get("shc", {}).get("compliance_submission") == "PERMITTED":
                    a.append("request_compliance")
            if s in (CState.COMPLIANCE_RECEIVED,) or (s == CState.PREPARATION and core_policy(case).get("shc", {}).get("compliance_submission") == "NOT_PERMITTED"):
                a.append("start_review")
            if s == CState.RECOMMENDATION_SUBMITTED:
                a += ["complete", "open_amendment"]
        if s == CState.COMPLETED:
            rt = _transition_view(db, case)
            if rt and rt["status"] == "AWAITING_DETERMINATION":
                a.append("determine_next")
    if "member" in scope:
        if stage == CommitteeStage.EVC:
            if s == CState.SCHEDULED:
                a.append("start_visit")
            if s == CState.VISIT_IN_PROGRESS:
                a += ["record_observation", "add_evidence", "save_report", "submit_report"]
        elif s == CState.UNDER_REVIEW:
            a += ["record_finding", "edit_finding", "record_proceeding", "save_recommendation", "submit_recommendation"]
    return a


def outstanding(case: CommitteeCase, scope, sch: CommitteeSchedule | None, n_assigned: int) -> list[str]:
    s, out = CState(case.state), []
    for b in case.blockers or []:
        out.append(f"Blocked by configuration: {b['message']}")
    if "coordinator" in scope:
        out += {
            CState.SELECTION_PENDING: ["Request an authorised selection of members."], CState.SELECTION_REQUESTED: ["Waiting for the selection result."],
            CState.SCHEDULE_PENDING: ["Confirm the visit schedule." if sch else "Propose a visit schedule."], CState.REPORT_SUBMITTED: ["Complete the EVC stage, or open an amendment."],
            CState.PREPARATION: ["Prepare the requirements the institution is asked to address, then request compliance."], CState.COMPLIANCE_RECEIVED: ["Assign members if needed, then start review."],
            CState.RECOMMENDATION_SUBMITTED: ["Complete the SHC stage, or open an amendment."],
        }.get(s, [])
    if "member" in scope:
        out += {CState.SCHEDULED: ["Start the visit when it takes place."], CState.VISIT_IN_PROGRESS: ["Record observations and submit the report."], CState.UNDER_REVIEW: ["Assess the evidence and submit the recommendation."]}.get(s, [])
    return out


def case_detail(db: Session, user: User, case: CommitteeCase, scope, settings: Settings) -> dict:
    app = db.get(Application, case.application_id)
    stage = CommitteeStage(case.stage)
    sel = _selection_view(db, case, settings)
    sch = scheduling.active(db, case.id)
    avail = selection.availability(db, case, settings)
    sc = db.scalar(select(ScrutinyCase).where(ScrutinyCase.application_id == app.id))
    handoff = db.scalar(select(StageHandoff).where(StageHandoff.case_id == sc.id)) if sc else None
    dh = db.scalar(select(DecisionHandoff).where(DecisionHandoff.application_id == app.id))
    detail = {
        "id": case.id, "stage": case.stage, "application": _app_summary(db, app), "state": case.state, "state_label": state_label(case), "row_version": case.row_version, "scope": sorted(scope.kinds),
        "activated_at": case.activated_at, "completed_at": case.completed_at, "completion": case.completion,
        "trigger": {k: case.trigger.get(k) for k in ("stage_key", "from_stage_key", "source", "determination", "scrutiny_outcome", "rule_version", "route_clause")},
        "policy": _policy_view(case), "blockers": case.blockers or [], "selection": sel, "schedule": {"active": schedule_out(sch) if sch else None, "history": [schedule_out(x) for x in scheduling.history(db, case.id)]},
        "scrutiny": {"state": sc.state if sc else None, "completion": sc.completion if sc else None, "handoff": {"status": handoff.status, "to_stage_key": handoff.to_stage_key, "reason": handoff.reason} if handoff else None},
        "next": _transition_view(db, case), "decision_handoff": {"status": dh.status, "created_at": dh.created_at, "basis": dh.basis} if dh else None,
        "events": _events(db, case.id), "allowed_actions": actions_for(db, case, scope, avail, sch), "outstanding_actions": outstanding(case, scope, sch, len(sel["assignees"])), "not_an_approval": NOT_AN_APPROVAL,
    }
    if stage == CommitteeStage.EVC:
        obs = evc_svc.observations(db, case.id)
        detail["evc"] = {
            "observations": [{"id": o.id, "item_key": o.item_key, "status": o.status, "note": o.note, "internal_note": o.internal_note, "shortcoming": o.shortcoming, "document_refs": o.document_refs or [],
                              "row_version": o.row_version, "recorded_by": o.recorded_by.full_name, "updated_at": o.updated_at} for o in obs],
            "evidence": [{"id": e.id, "kind": e.kind, "item_key": e.item_key, "description": e.description, "filename": e.original_filename, "media_type": e.media_type, "size_bytes": e.size_bytes, "sha256": e.sha256,
                          "url": e.url, "uploaded_at": e.uploaded_at, "download": f"/api/evc/evidence/{e.id}/download" if e.kind == "FILE" else None} for e in evc_svc.live_evidence(db, case.id)],
            "draft": _report_out(d, False) if (d := evc_svc.draft_report(db, case.id)) else None,
            "versions": [_report_out(r) for r in evc_svc.submitted_reports(db, case.id)],
        }
    else:
        rounds = shc_svc.rounds(db, case.id)
        detail["shc"] = {
            "shortcomings": case.trigger.get("shortcomings", []),
            "prepared": [_req_out(r) for r in shc_svc.prepared_requirements(db, case.id)],
            "rounds": [{"id": r.id, "number": r.number, "status": r.status, "instructions": r.instructions, "requested_at": r.requested_at, "response_received_at": r.response_received_at, "policy_resolution": r.policy_resolution,
                        "requirements": [_req_out(q) for q in shc_svc.round_requirements(db, r.id)], "response": _resp_staff(db, r)} for r in rounds],
            "findings": [{"id": f.id, "sequence": f.sequence, "requirement_id": f.requirement_id, "assessment": f.assessment, "text": f.text, "row_version": f.row_version, "by": f.created_by.full_name,
                          "by_id": f.created_by_id, "at": f.updated_at} for f in shc_svc.findings(db, case.id)],
            "proceedings": [{"id": p.id, "schedule_id": p.schedule_id, "held_at": p.held_at, "participants": p.participants, "text": p.text, "by": p.recorded_by.full_name, "recorded_at": p.recorded_at} for p in shc_svc.proceedings(db, case.id)],
            "draft": _report_out(d, False) if (d := shc_svc.draft_recommendation(db, case.id)) else None,
            "versions": [_report_out(r) for r in shc_svc.submitted_recommendations(db, case.id)],
        }
    return detail


def _req_out(r) -> dict:
    return {"id": r.id, "position": r.position, "title": r.title, "description": r.description, "requested_evidence": r.requested_evidence, "item_key": r.item_key, "origin": r.origin, "source_ref": r.source_ref,
            "source_ref_status": r.source_ref_status, "row_version": r.row_version, "round_id": r.round_id}


def _resp_staff(db: Session, rnd) -> dict | None:
    r = shc_svc.response_of(db, rnd.id)
    if r is None:
        return None
    docs = {d.id: d for d in db.scalars(select(ApplicationDocument).where(ApplicationDocument.id.in_([x.document_id for i in r.items for x in i.documents] or [uuid.uuid4()])))}
    return {"id": r.id, "status": r.status, "explanation": r.explanation, "submitted_at": r.submitted_at, "items": [
        {"requirement_id": i.requirement_id, "explanation": i.explanation, "documents": [_doc(docs.get(x.document_id)) for x in i.documents]} for i in r.items]}


# ---- institution ---------------------------------------------------------------------------------------


def institution_status(db: Session, app: Application) -> dict:
    """The institution's view of the committee stages for ONE of its applications. No names, no internal notes, no recommendation."""
    out: dict = {"evc": None, "shc": None, "ready_for_decision_stage": False, "not_a_decision": NOT_AN_APPROVAL}
    evc = db.scalar(select(CommitteeCase).where(CommitteeCase.application_id == app.id, CommitteeCase.stage == "EVC"))
    if evc is not None:
        sch = db.scalar(select(CommitteeSchedule).where(CommitteeSchedule.case_id == evc.id, CommitteeSchedule.status.in_(("PROPOSED", "CONFIRMED", "COMPLETED"))).order_by(CommitteeSchedule.sequence.desc()))
        report = None
        if evc.state == CState.COMPLETED.value and core_policy(evc).get("report", {}).get("visible_to_institution_on") == "COMPLETED":
            last = evc_svc.submitted_reports(db, evc.id)
            if last:
                r = last[-1]
                snap = r.source_snapshot or {}
                secs = {s["key"]: s["label"] for s in core_policy(evc).get("report", {}).get("sections", [])}
                labels = {i["item_key"]: i["label"] for i in core_policy(evc).get("checklist", [])}
                report = {"version_number": r.version_number, "submitted_at": r.submitted_at,
                          "sections": [{"key": k, "label": secs.get(k, k), "text": v} for k, v in (r.content or {}).get("sections", {}).items() if (v or "").strip()],
                          "observations": [{"item_key": o["item_key"], "label": labels.get(o["item_key"], o["item_key"]), "status": o["status"], "note": o["note"]} for o in snap.get("observations", [])],
                          "demo": evc.policy_snapshot and evc.policy_snapshot["classification"] == "DEMO"}
        out["evc"] = {"state": evc.state, "label": INSTITUTION_LABELS["EVC"][evc.state], "schedule": schedule_for_institution(sch) if sch else None, "report": report, "timeline": _events(db, evc.id, True)}
    shc = db.scalar(select(CommitteeCase).where(CommitteeCase.application_id == app.id, CommitteeCase.stage == "SHC"))
    if shc is not None:
        hearing = db.scalar(select(CommitteeSchedule).where(CommitteeSchedule.case_id == shc.id, CommitteeSchedule.status.in_(("PROPOSED", "CONFIRMED", "COMPLETED"))).order_by(CommitteeSchedule.sequence.desc()))
        out["shc"] = {"state": shc.state, "label": INSTITUTION_LABELS["SHC"][shc.state], "hearing": schedule_for_institution(hearing) if hearing else None,
                      "rounds": [{"id": r.id, "number": r.number, "status": r.status, "requested_at": r.requested_at} for r in shc_svc.rounds(db, shc.id)], "timeline": _events(db, shc.id, True)}
    out["ready_for_decision_stage"] = db.scalar(select(DecisionHandoff.id).where(DecisionHandoff.application_id == app.id)) is not None
    return out


def inst_round(db: Session, rnd, case: CommitteeCase, app: Application) -> dict:
    resp = shc_svc.response_of(db, rnd.id)
    reqs = shc_svc.round_requirements(db, rnd.id)
    row = {i.requirement_id: i for i in (resp.items if resp else [])}
    ids = [x.document_id for i in (resp.items if resp else []) for x in i.documents] or [uuid.uuid4()]
    docs = {d.id: d for d in db.scalars(select(ApplicationDocument).where(ApplicationDocument.id.in_(ids)))}
    can = rnd.status == "OPEN" and case.state == CState.AWAITING_COMPLIANCE.value and (resp is None or resp.status == "DRAFT")
    demo = bool(case.policy_snapshot and case.policy_snapshot["classification"] == "DEMO")
    return {
        "id": rnd.id, "number": rnd.number, "status": rnd.status, "case_state": case.state, "instructions": rnd.instructions, "requested_at": rnd.requested_at, "response_due_at": rnd.response_due_at,
        "due_note": "No response deadline has been established." if rnd.response_due_at is None else None, "can_respond": can, "demo_notice": cpolicy.verification_note(case.policy_snapshot) if demo else None,
        "application": {"id": app.id, "reference": app.reference},
        "requirements": [{"id": q.id, "position": q.position, "title": q.title, "description": q.description, "requested_evidence": q.requested_evidence, "item_key": q.item_key, "source_ref": q.source_ref,
                          "explanation": row[q.id].explanation if q.id in row else "", "documents": [_doc(docs.get(x.document_id)) for x in (row[q.id].documents if q.id in row else [])]} for q in reqs],
        "response": {"id": resp.id, "status": resp.status, "explanation": resp.explanation, "row_version": resp.row_version, "submitted_at": resp.submitted_at} if resp else None,
        "received_note": "A submitted response is received and goes to the committee for review. It is not accepted or assessed automatically." if resp and resp.status == "SUBMITTED" else None,
    }


def inst_round_summaries(db: Session, app: Application) -> list[dict]:
    case = db.scalar(select(CommitteeCase).where(CommitteeCase.application_id == app.id, CommitteeCase.stage == "SHC"))
    if case is None:
        return []
    return [{"id": r.id, "number": r.number, "status": r.status, "requested_at": r.requested_at, "application": {"id": app.id, "reference": app.reference}, "case_state": case.state,
             "can_respond": r.status == "OPEN" and case.state == CState.AWAITING_COMPLIANCE.value} for r in shc_svc.rounds(db, case.id)]

