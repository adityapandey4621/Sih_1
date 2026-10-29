"""Shape decision cases, issued outcomes and appeals for the API.

Staff with `decision:queue` see the whole record. The institution sees only: what was officially issued (outcome, reasons, Speaking Order, reference, file), the
Council's recorded steps, whether it may appeal (with the clause and deadline), its own appeal and its documents, and the truth about synchronisation with AICTE's
portal. It never sees Bureau observations, supporting-record details, internal committee notes or the SAC's recommendation.
"""

import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.domain import AppealStatus, DecisionState
from app.models import User
from app.models_applications import Application
from app.models_committee import CommitteeCase, CommitteeEventRow, DecisionHandoff
from app.models_decisions import Appeal, BureauObservation, CouncilDecision, DecisionCase, EcDecision, OfficialIssuance, PostDecisionHandoff
from app.rbac import Permission, permissions_for
from app.services import appeals, decisions, portal_sync
from app.services import committee_policy as cpolicy
from app.services.decision_texts import LABEL

NOT_A_DECISION = "This system records what the authorised bodies decide. Nothing on this page was decided by the software, and no report or recommendation is a decision."

STATE_LABELS = {
    "AWAITING_OBSERVATIONS": "Awaiting the Regulation Bureau's observations", "READY_FOR_DECISION": "Ready for the Executive Committee's decision", "DECIDED": "Decision recorded — not yet issued",
    "ISSUED": "Official outcome issued", "CLOSED": "Decision stage closed",
}
APPEAL_WORDS = {
    "UNDER_SAC_REVIEW": "Under review by the Standing Appellate Committee", "AWAITING_COUNCIL_DECISION": "The SAC stage is complete; awaiting the Council's decision", "COUNCIL_DECIDED": "The Council's decision is recorded (final under the handbook)",
}


def _app(db: Session, a: Application) -> dict:
    return {"id": a.id, "reference": a.reference, "institution_name": a.institution.name, "category_name": a.category.name, "academic_year": a.academic_year, "status": a.status}


def label(outcome: str | None) -> str | None:
    return LABEL.get(outcome, outcome) if outcome else None


def _decision_out(d: EcDecision, full: bool = True) -> dict:
    out = {"id": d.id, "version_number": d.version_number, "kind": d.kind, "outcome": d.outcome, "outcome_label": label(d.outcome), "reasons": d.reasons, "speaking_order": d.speaking_order,
           "meeting_reference": d.meeting_reference, "decided_by": d.decided_by.full_name, "decided_at": d.decided_at, "correction_reason": d.correction_reason, "supersedes_id": d.supersedes_id, "content_hash": d.content_hash}
    if full:
        out.update({"supporting_refs": d.supporting_refs, "rule_version": d.rule_version, "policy": d.policy_ref})
    return out


def _council_out(c: CouncilDecision | None) -> dict | None:
    if c is None:
        return None
    return {"id": c.id, "kind": c.kind, "outcome": c.outcome, "outcome_label": label(c.outcome) or c.outcome.replace("_", " ").title(), "reasons": c.reasons, "speaking_order": c.speaking_order, "reference": c.reference,
            "recorded_by": c.recorded_by.full_name, "recorded_at": c.recorded_at, "supersedes_id": c.supersedes_id, "correction_reason": c.correction_reason, "content_hash": c.content_hash}


def _issuance_out(i: OfficialIssuance) -> dict:
    return {"id": i.id, "decision_id": i.decision_id, "status": i.status, "document_reference": i.document_reference, "issued_at": i.issued_at, "superseded_at": i.superseded_at,
            "file": {"filename": i.original_filename, "size_bytes": i.size_bytes, "sha256": i.sha256, "download": f"/api/decisions/issuances/{i.id}/download"} if i.storage_key else None}


def policy_view(case: DecisionCase) -> dict | None:
    p = case.policy_snapshot
    if not p:
        return None
    c = p["content"]
    return {"id": p["id"], "key": p["key"], "version": p["version"], "classification": p["classification"], "title": p["title"], "banner": cpolicy.verification_note(p) and c.get("banner") or c.get("banner"),
            "sources": p["sources"], "decision": c.get("decision"), "ratification": c.get("ratification"), "appeal": c.get("appeal")}


# ---- queue ------------------------------------------------------------------------------------------------


def list_queue(db: Session, user: User, *, state: str | None, q: str | None, page: int, page_size: int) -> dict:
    stmt = select(DecisionCase).join(Application, Application.id == DecisionCase.application_id)
    counts = dict(db.execute(select(DecisionCase.state, func.count()).group_by(DecisionCase.state)).all())
    if state:
        stmt = stmt.where(DecisionCase.state == state)
    if q:
        stmt = stmt.where(Application.reference.ilike(f"%{q}%"))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = list(db.scalars(stmt.order_by(DecisionCase.opened_at.desc()).limit(page_size).offset((page - 1) * page_size)))
    items = []
    for c in rows:
        a = db.get(Application, c.application_id)
        d = decisions.current_decision(db, c.id)
        ap = appeals.get_for_application(db, a.id)
        items.append({"id": c.id, "application": _app(db, a), "state": c.state, "state_label": STATE_LABELS[c.state], "row_version": c.row_version, "opened_at": c.opened_at,
                      "outcome": d.outcome if d else None, "outcome_label": label(d.outcome) if d else None, "blocked": bool(decisions.blockers(db, c)) and c.state in ("AWAITING_OBSERVATIONS", "READY_FOR_DECISION"),
                      "appeal_status": ap.status if ap else None, "appeal_label": APPEAL_WORDS.get(ap.status) if ap else None, "demo": bool(c.policy_snapshot and c.policy_snapshot["classification"] == "DEMO")})
    perms = permissions_for(user.role)
    return {"total": total, "page": page, "page_size": page_size, "counts": counts, "items": items, "can": {"observe": Permission.DECISION_OBSERVE in perms, "record": Permission.DECISION_RECORD in perms, "council": Permission.COUNCIL_RECORD in perms}}


# ---- detail -----------------------------------------------------------------------------------------------


def _events(db: Session, case: DecisionCase) -> list[dict]:
    q = select(CommitteeEventRow).where(CommitteeEventRow.application_id == case.application_id, CommitteeEventRow.stage == "DECISION").order_by(CommitteeEventRow.at)
    return [{"at": e.at, "actor": e.actor_label, "action": e.action, "from_state": e.from_state, "to_state": e.to_state, "reason": e.reason} for e in db.scalars(q)]


def actions_for(db: Session, user: User, case: DecisionCase, bl: list[dict], ap: Appeal | None, closure_bl: list[dict]) -> list[str]:
    perms = permissions_for(user.role)
    s, out = case.state, []
    if Permission.DECISION_OBSERVE in perms and s in (DecisionState.AWAITING_OBSERVATIONS.value, DecisionState.READY_FOR_DECISION.value):
        out.append("observe")
    if Permission.DECISION_RECORD in perms:
        if s == DecisionState.READY_FOR_DECISION.value and not bl:
            out.append("record_decision")
        if s in (DecisionState.DECIDED.value, DecisionState.ISSUED.value):
            out.append("correct_decision")
    if Permission.DECISION_ISSUE in perms:
        if s == DecisionState.DECIDED.value:
            out.append("issue_outcome")
        if s == DecisionState.ISSUED.value and not closure_bl:
            out.append("close_case")
    if Permission.COUNCIL_RECORD in perms:
        if decisions.policy(case).get("ratification", {}).get("required") and s in (DecisionState.DECIDED.value, DecisionState.ISSUED.value):
            out.append("record_ratification")
        if ap is not None and ap.status in (AppealStatus.AWAITING_COUNCIL_DECISION.value, AppealStatus.COUNCIL_DECIDED.value):
            out.append("record_appeal_decision")
    return out


def case_detail(db: Session, user: User, case: DecisionCase, settings: Settings) -> dict:
    app = decisions.application(db, case)
    bl = decisions.blockers(db, case) if case.state in ("AWAITING_OBSERVATIONS", "READY_FOR_DECISION") else []
    decs = decisions.decisions(db, case.id)
    cur = decs[-1] if decs else None
    iss_all = list(db.scalars(select(OfficialIssuance).where(OfficialIssuance.case_id == case.id).order_by(OfficialIssuance.issued_at)))
    live_iss = next((i for i in iss_all if i.status == "ISSUED"), None)
    rats = list(db.scalars(select(CouncilDecision).where(CouncilDecision.decision_id.in_([d.id for d in decs] or [uuid.uuid4()])).order_by(CouncilDecision.recorded_at)))
    ap = appeals.get_for_application(db, app.id)
    closure_bl = decisions.closure_blockers(db, case, settings) if case.state == DecisionState.ISSUED.value else []
    elig = appeals.eligibility(db, app, case, settings)
    appeal_view = None
    if ap is not None:
        council = list(db.scalars(select(CouncilDecision).where(CouncilDecision.appeal_id == ap.id).order_by(CouncilDecision.recorded_at)))
        sac = db.get(CommitteeCase, ap.sac_case_id) if ap.sac_case_id else None
        from app.services import sac as sac_svc

        rec = sac_svc.submitted(db, sac.id)[-1] if sac and sac_svc.submitted(db, sac.id) else None
        appeal_view = {
            "id": ap.id, "status": ap.status, "status_label": APPEAL_WORDS[ap.status], "submitted_at": ap.submitted_at, "grounds": ap.grounds, "eligibility": ap.eligibility, "sac_case_id": ap.sac_case_id,
            "sac_state": sac.state if sac else None, "sac_recommendation": {"version": rec.version_number, "option": (rec.content or {}).get("option"), "content": rec.content, "content_hash": rec.content_hash,
                                                                              "submitted_at": rec.submitted_at} if rec else None,
            "documents": [{"id": x.id, "kind": x.kind, "description": x.description, "filename": x.original_filename, "url": x.url, "uploaded_at": x.uploaded_at,
                           "download": f"/api/sac/appeal-documents/{x.id}/download" if x.kind == "FILE" else None} for x in appeals.documents(db, ap.id)],
            "council_decisions": [_council_out(c) for c in council], "live_council_decision": _council_out(next((c for c in council if c.superseded_by_id is None), None)),
            "sync": portal_sync.state_view(portal_sync.sync_for(db, "council_decision", council[-1].id)) if council else None,
        }
    handoff = db.get(DecisionHandoff, case.handoff_id)
    return {
        "id": case.id, "application": _app(db, app), "state": case.state, "state_label": STATE_LABELS[case.state], "row_version": case.row_version, "opened_at": case.opened_at, "first_issued_at": case.first_issued_at,
        "closed_at": case.closed_at, "closure": case.closure, "policy": policy_view(case), "blockers": bl, "route": {"stages": [s["stage_key"] for s in handoff.basis["stages"]], "basis": handoff.basis},
        "supporting": decisions.supporting_records(db, case), "considerable": decisions.considerable(decisions.supporting_records(db, case)),
        "observations": [{"id": o.id, "sequence": o.sequence, "text": o.text, "by": o.recorded_by.full_name, "at": o.recorded_at} for o in decisions.latest_observations(db, case.id)],
        "decisions": [_decision_out(d) for d in decs], "current_decision": _decision_out(cur) if cur else None,
        "issuances": [_issuance_out(i) for i in iss_all], "live_issuance": _issuance_out(live_iss) if live_iss else None,
        "issuance_sync": portal_sync.state_view(portal_sync.sync_for(db, "official_issuance", live_iss.id)) if live_iss else None,
        "ratifications": [_council_out(r) for r in rats], "live_ratification": _council_out(decisions.live_ratification(db, cur.id)) if cur else None,
        "appeal_eligibility": elig, "appeal": appeal_view, "closure_blockers": closure_bl, "events": _events(db, case), "allowed_actions": actions_for(db, user, case, bl, ap, closure_bl),
        "not_a_decision": NOT_A_DECISION, "post_decision": bool(db.scalar(select(PostDecisionHandoff.id).where(PostDecisionHandoff.decision_case_id == case.id))),
    }


# ---- the institution ------------------------------------------------------------------------------------


def official_status(db: Session, case: DecisionCase | None, ap: Appeal | None) -> dict:
    if case is None:
        return {"state": "NO_DECISION_CASE", "label": "Not yet at the decision stage"}
    council = db.scalar(select(CouncilDecision).where(CouncilDecision.appeal_id == ap.id, CouncilDecision.superseded_by_id.is_(None))) if ap else None
    if council is not None:
        return {"state": "COUNCIL_DECISION_ON_APPEAL", "label": f"Council decision on your appeal: {label(council.outcome)} (final under the handbook)", "outcome": council.outcome}
    iss = decisions.live_issuance(db, case.id)
    if iss is not None:
        d = db.get(EcDecision, iss.decision_id)
        return {"state": "EXECUTIVE_COMMITTEE_DECISION_ISSUED", "label": f"Executive Committee decision issued: {label(d.outcome)}", "outcome": d.outcome}
    return {"state": "AWAITING_DECISION", "label": "At the decision stage: the Executive Committee has not issued an outcome yet"}


def institution_view(db: Session, app: Application, settings: Settings) -> dict:
    case = db.scalar(select(DecisionCase).where(DecisionCase.application_id == app.id))
    ap = appeals.get_for_application(db, app.id)
    out: dict = {"official_status": official_status(db, case, ap), "decision": None, "appeal": None, "closed": False, "not_a_decision": NOT_A_DECISION}
    if case is None:
        return out
    out["closed"] = case.state == DecisionState.CLOSED.value
    iss = decisions.live_issuance(db, case.id)
    if iss is not None:
        d = db.get(EcDecision, iss.decision_id)
        rat_cfg = decisions.policy(case).get("ratification", {})
        rat = decisions.live_ratification(db, d.id)
        ver = list(db.scalars(select(OfficialIssuance).where(OfficialIssuance.case_id == case.id, OfficialIssuance.status == "SUPERSEDED").order_by(OfficialIssuance.issued_at)))
        out["decision"] = {
            "outcome": d.outcome, "outcome_label": label(d.outcome), "reasons": d.reasons, "speaking_order": d.speaking_order, "issued_at": iss.issued_at, "first_issued_at": case.first_issued_at,
            "document_reference": iss.document_reference, "file": {"filename": iss.original_filename, "size_bytes": iss.size_bytes, "download": f"/api/decisions/issuances/{iss.id}/download"} if iss.storage_key else None,
            "version_number": d.version_number, "corrected": d.version_number > 1, "correction_reason": d.correction_reason,
            "earlier_issues": [{"issued_at": v.issued_at, "superseded_at": v.superseded_at} for v in ver],
            "ratification": {"required": bool(rat_cfg.get("required")), "recorded": rat is not None, "outcome": rat.outcome if rat else None, "recorded_at": rat.recorded_at if rat else None,
                             "reasons": rat.reasons if rat else None, "clause_ref": rat_cfg.get("clause_ref")},
            "sync": portal_sync.state_view(portal_sync.sync_for(db, "official_issuance", iss.id)),
        }
    elif case.first_issued_at is not None:
        out["correction_pending"] = "A corrected decision is being issued. The earlier outcome is superseded once it is."
    elig = appeals.eligibility(db, app, case, settings)
    appeal = {"eligibility": {k: elig[k] for k in ("state", "message", "deadline_at", "trigger_at", "outcome", "clause_ref")}, "window": elig.get("window"), "submitted": None}
    if ap is not None:
        council = db.scalar(select(CouncilDecision).where(CouncilDecision.appeal_id == ap.id, CouncilDecision.superseded_by_id.is_(None)))
        appeal["submitted"] = {
            "id": ap.id, "status": ap.status, "status_label": APPEAL_WORDS[ap.status], "submitted_at": ap.submitted_at, "grounds": ap.grounds,
            "documents": [{"id": x.id, "kind": x.kind, "description": x.description, "filename": x.original_filename, "size_bytes": x.size_bytes, "url": x.url, "uploaded_at": x.uploaded_at,
                           "download": f"/api/appeals/documents/{x.id}/download" if x.kind == "FILE" else None} for x in appeals.documents(db, ap.id)],
            "can_add_documents": ap.status == AppealStatus.UNDER_SAC_REVIEW.value,
            "council": {"outcome": council.outcome, "outcome_label": label(council.outcome), "reasons": council.reasons, "speaking_order": council.speaking_order, "reference": council.reference, "recorded_at": council.recorded_at,
                        "sync": portal_sync.state_view(portal_sync.sync_for(db, "council_decision", council.id))} if council else None,
            "note": "An appeal is decided by the Council, with the SAC's recommendation. Submitting it or adding evidence does not promise any outcome.",
        }
    out["appeal"] = appeal
    return out


_ = (BureauObservation,)
