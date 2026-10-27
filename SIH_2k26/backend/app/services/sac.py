"""The Standing Appellate Committee's review of one appeal and its recommendation to the Council.

An appeal is heard against the Executive Committee's decision (cl. 1.11, 2.24). The SAC either recommends or does not recommend the case to the Council, or recommends
further scrutiny / an expert visit (cl. 1.11(d)); the Council decides. So the recommendation here is a versioned, immutable record that waits for the Council: it is never
the decision, and completing the SAC stage only moves the appeal to "awaiting the Council's decision". Members are assigned through the same selection boundary as the other
committees (demonstration mode labelled; the real mechanism is not connected). Appeal documents and the hearing record are inputs to the review, not conclusions.
"""

import hashlib
import json
import uuid

from fastapi import Request
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app import committee_access
from app.domain import CAction, CommitteeStage, CState, ReportKind, ReportStatus
from app.models import User, utcnow
from app.models_committee import CommitteeCase, CommitteeReport, ShcProceeding
from app.models_decisions import Appeal, EcDecision
from app.services import appeals, scheduling
from app.services import committee_core as core
from app.services import committee_texts as ctexts
from app.services.errors import Issue, conflict, invalid, not_found

STAGE = CommitteeStage.SAC
KIND = ReportKind.SAC_RECOMMENDATION


def _member_case(db: Session, user: User, case_id: uuid.UUID, request: Request | None) -> CommitteeCase:
    case, _ = committee_access.get_case(db, user, case_id, request, stage=STAGE, need="member")
    return core.lock_case(db, case.id)


def _coordinator_case(db: Session, user: User, case_id: uuid.UUID, request: Request | None) -> CommitteeCase:
    case, _ = committee_access.get_case(db, user, case_id, request, stage=STAGE, need="coordinator")
    return core.lock_case(db, case.id)


def sac_policy(case: CommitteeCase) -> dict:
    return core.policy(case).get("content", {}).get("sac", {})


def draft(db: Session, case_id: uuid.UUID) -> CommitteeReport | None:
    return db.scalar(select(CommitteeReport).where(CommitteeReport.case_id == case_id, CommitteeReport.kind == KIND.value, CommitteeReport.status == ReportStatus.DRAFT.value).execution_options(populate_existing=True))


def submitted(db: Session, case_id: uuid.UUID) -> list[CommitteeReport]:
    return list(db.scalars(select(CommitteeReport).where(CommitteeReport.case_id == case_id, CommitteeReport.kind == KIND.value, CommitteeReport.status == ReportStatus.SUBMITTED.value).order_by(CommitteeReport.version_number)))


def proceedings(db: Session, case_id: uuid.UUID) -> list[ShcProceeding]:
    return list(db.scalars(select(ShcProceeding).where(ShcProceeding.case_id == case_id).order_by(ShcProceeding.recorded_at)))


def ensure_draft(db: Session, case: CommitteeCase, actor: User | None) -> None:
    """Called when members are recorded and the review begins: the recommendation draft is opened once."""
    if draft(db, case.id) is None and not submitted(db, case.id):
        db.add(CommitteeReport(case_id=case.id, kind=KIND.value, version_number=1, status=ReportStatus.DRAFT.value, content={"option": None, "sections": {}}, created_by_id=actor.id if actor else _any_user(db)))


def _any_user(db: Session) -> uuid.UUID:
    return db.scalar(select(User.id).limit(1))


def appeal_of(db: Session, case: CommitteeCase) -> Appeal:
    ap = db.scalar(select(Appeal).where(Appeal.sac_case_id == case.id))
    if ap is None:
        raise not_found("Appeal")
    return ap


# ---- review records -----------------------------------------------------------------------------------------


def record_proceeding(db: Session, user: User, case_id: uuid.UUID, data: dict, request: Request | None) -> ShcProceeding:
    """What happened at a hearing or review sitting. Append-only; naming a confirmed hearing records that it took place."""
    case = _member_case(db, user, case_id, request)
    core.require_state(case, CState.UNDER_REVIEW)
    text = (data.get("text") or "").strip()
    if len(text) < 5:
        raise invalid("Record what happened.", [Issue("text", "A record is needed.", "text")])
    held_at = scheduling.to_utc(data["held_local"], data["timezone"])
    if held_at > utcnow():
        raise invalid("A sitting cannot be recorded before it has happened.", [Issue("held_local", "Choose a time that has passed.", "held_local")])
    sch = None
    if data.get("schedule_id") is not None:
        sch = scheduling.active(db, case.id)
        if sch is None or str(sch.id) != str(data["schedule_id"]) or sch.status != "CONFIRMED":
            raise conflict("no_confirmed_hearing", "That hearing is not a confirmed, open schedule for this case.")
    row = ShcProceeding(case_id=case.id, schedule_id=sch.id if sch else None, held_at=held_at, participants=(data.get("participants") or "").strip(), text=text, recorded_by_id=user.id)
    db.add(row)
    db.flush()
    if sch is not None:
        scheduling.mark_completed(db, case, user, request)
    core.log_event(db, case, user, "proceeding_recorded", request=request, object_type="shc_proceeding", object_id=row.id, institution_visible=sch is not None, detail={"schedule_id": str(sch.id) if sch else None, "held_at": held_at.isoformat()})
    return row


def save_draft(db: Session, user: User, case_id: uuid.UUID, option: str | None, sections: dict[str, str], row_version: int, request: Request | None) -> CommitteeReport:
    case = _member_case(db, user, case_id, request)
    core.require_state(case, CState.UNDER_REVIEW)
    d = draft(db, case.id)
    if d is None:
        raise conflict("no_draft_recommendation", "There is no draft recommendation to edit.")
    pol = sac_policy(case)
    if option is not None and option not in {o["key"] for o in pol.get("recommendation_options", [])}:
        raise invalid("Choose one of the configured recommendation options.", [Issue("option", "Not a configured option.", "option")])
    known = {s["key"] for s in pol.get("recommendation_sections", [])}
    unknown = sorted(set(sections) - known)
    if unknown:
        raise invalid("Some sections are not part of the configured recommendation.", [Issue("sections", f"Unknown section: {k}", k) for k in unknown])
    res = db.execute(update(CommitteeReport).where(CommitteeReport.id == d.id, CommitteeReport.row_version == row_version, CommitteeReport.status == ReportStatus.DRAFT.value).values(
        content={"option": option, "sections": sections}, row_version=row_version + 1, updated_at=utcnow()))
    if res.rowcount != 1:
        raise conflict("stale_version", "The draft was changed by someone else since you opened it. Reload it before saving.")
    db.refresh(d)
    core.log_event(db, case, user, "recommendation_draft_saved", request=request, object_type="committee_report", object_id=d.id, detail={"version": d.version_number})
    return d


def _canonical(d: dict) -> str:
    return json.dumps(d, sort_keys=True, separators=(",", ":"), default=str)


def _validate(db: Session, case: CommitteeCase, d: CommitteeReport) -> tuple[list[Issue], dict]:
    pol = sac_policy(case)
    issues: list[Issue] = []
    content = d.content or {}
    if content.get("option") not in {o["key"] for o in pol.get("recommendation_options", [])}:
        issues.append(Issue("option_missing", "Choose the recommendation.", "option"))
    for s in pol.get("recommendation_sections", []):
        if s.get("required") and len(((content.get("sections") or {}).get(s["key"]) or "").strip()) < 3:
            issues.append(Issue("section_missing", f"The section “{s['label']}” is required.", s["key"]))
    sch = scheduling.active(db, case.id)
    if sch is not None:
        issues.append(Issue("hearing_pending", "A hearing is scheduled but has not been recorded as held or cancelled.", str(sch.id)))
    if pol.get("hearing") == "REQUIRED" and not any(p.schedule_id for p in proceedings(db, case.id)):
        issues.append(Issue("hearing_required", "The configuration makes a hearing part of this process, and none has been recorded.", "hearing"))
    ap = appeal_of(db, case)
    dec = db.get(EcDecision, ap.against_decision_id)
    snapshot = {
        "appeal": {"id": str(ap.id), "submitted_at": ap.submitted_at.isoformat(), "grounds": ap.grounds, "eligibility": {k: ap.eligibility.get(k) for k in ("state", "deadline_at", "clause_ref")}},
        "against_decision": {"id": str(dec.id), "version": dec.version_number, "outcome": dec.outcome, "content_hash": dec.content_hash},
        "appeal_documents": [{"id": str(x.id), "kind": x.kind, "filename": x.original_filename, "sha256": x.sha256, "url": x.url, "description": x.description} for x in appeals.documents(db, ap.id)],
        "proceedings": [{"id": str(p.id), "held_at": p.held_at.isoformat(), "schedule_id": str(p.schedule_id) if p.schedule_id else None} for p in proceedings(db, case.id)],
        "policy": {k: core.policy(case)[k] for k in ("id", "key", "version", "classification", "content_hash")} if core.policy(case) else None,
        "rule_version": case.trigger.get("rule_version"), "not_a_council_decision": True,
    }
    return issues, snapshot


def submit_recommendation(db: Session, user: User, case_id: uuid.UUID, idempotency_key: str, row_version: int, request: Request | None) -> tuple[CommitteeCase, CommitteeReport, bool]:
    case = _member_case(db, user, case_id, request)
    prior = db.scalar(select(CommitteeReport).where(CommitteeReport.case_id == case.id, CommitteeReport.kind == KIND.value, CommitteeReport.idempotency_key == idempotency_key))
    if prior is not None:
        return case, prior, True
    core.require_state(case, CState.UNDER_REVIEW)
    d = draft(db, case.id)
    if d is None:
        raise conflict("no_draft_recommendation", "There is no draft recommendation to submit.")
    if d.row_version != row_version:
        raise conflict("stale_version", "The draft was changed since you opened it. Reload it before submitting.")
    issues, snapshot = _validate(db, case, d)
    if issues:
        raise conflict("recommendation_incomplete", "The recommendation cannot be submitted yet.", issues)
    content = {"option": d.content["option"], "sections": d.content.get("sections", {})}
    digest = hashlib.sha256(_canonical({"content": content, "snapshot": snapshot}).encode()).hexdigest()
    now = utcnow()
    res = db.execute(update(CommitteeReport).where(CommitteeReport.id == d.id, CommitteeReport.status == ReportStatus.DRAFT.value, CommitteeReport.row_version == row_version).values(
        status=ReportStatus.SUBMITTED.value, content=content, source_snapshot=snapshot, content_hash=digest, submitted_by_id=user.id, submitted_at=now, idempotency_key=idempotency_key, row_version=row_version + 1, updated_at=now))
    if res.rowcount != 1:
        raise conflict("stale_version", "The recommendation was changed or submitted at the same moment. Reload it.")
    db.refresh(d)
    from_state = case.state
    core.transition(db, case, CAction.SUBMIT_RECOMMENDATION)
    core.log_event(db, case, user, "recommendation_submitted", request=request, object_type="committee_report", object_id=d.id, from_state=from_state, to_state=case.state,
                   detail={"version": d.version_number, "content_hash": digest, "option": content["option"], "decided": False})
    db.flush()
    ctexts.report_submitted(db, case, core.application(db, case), d.id, d.version_number)
    return case, d, False


def open_amendment(db: Session, user: User, case_id: uuid.UUID, reason: str, row_version: int, request: Request | None) -> CommitteeCase:
    case = _coordinator_case(db, user, case_id, request)
    core.require_version(case, row_version)
    core.require_state(case, CState.RECOMMENDATION_SUBMITTED)
    if not reason or len(reason.strip()) < 5:
        raise invalid("Record why an amendment is needed.")
    last = submitted(db, case.id)[-1]
    from_state = case.state
    core.transition(db, case, CAction.OPEN_AMENDMENT, expected_version=row_version)
    new = CommitteeReport(case_id=case.id, kind=KIND.value, version_number=last.version_number + 1, status=ReportStatus.DRAFT.value, content=dict(last.content or {}), supersedes_id=last.id,
                          amendment_reason=reason.strip(), amendment_authorised_by_id=user.id, created_by_id=user.id)
    db.add(new)
    db.flush()
    core.log_event(db, case, user, "amendment_opened", request=request, object_type="committee_report", object_id=new.id, from_state=from_state, to_state=case.state, reason=reason, detail={"supersedes": str(last.id), "version": new.version_number})
    return case


def complete(db: Session, user: User, case_id: uuid.UUID, summary: str, row_version: int, request: Request | None) -> CommitteeCase:
    """Record that the SAC stage is finished. NOT the Council's decision: the appeal now waits for it."""
    case = _coordinator_case(db, user, case_id, request)
    core.require_version(case, row_version)
    core.require_state(case, CState.RECOMMENDATION_SUBMITTED)
    if not summary or len(summary.strip()) < 5:
        raise invalid("Record a short note on completing the SAC stage.")
    last = submitted(db, case.id)[-1]
    completion = {"summary": summary.strip(), "recommendation_id": str(last.id), "recommendation_version": last.version_number, "recommendation_hash": last.content_hash, "option": (last.content or {}).get("option"), "decided": False}
    from_state = case.state
    core.transition(db, case, CAction.COMPLETE, expected_version=row_version, completed_at=utcnow(), completed_by_id=user.id, completion=completion)
    core.log_event(db, case, user, "stage_completed", request=request, object_type="committee_case", object_id=case.id, from_state=from_state, to_state=case.state, reason=summary,
                   institution_visible=True, detail={"recommendation_version": last.version_number, "decided": False})
    db.flush()
    ctexts.stage_completed(db, case, core.application(db, case))
    appeals.on_sac_completed(db, case, user, request)
    return case
