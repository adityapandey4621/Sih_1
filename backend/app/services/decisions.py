"""The Executive Committee decision stage, Council ratification and closing the decision case.

Principles enforced here (and tested):
  * A case enters the queue only when every stage its approved route required is complete (services/stages.py makes the hand-off; this module re-checks).
  * Nothing here decides. No OCR result, checklist, report or recommendation is turned into an outcome: an authorised person records one, naming the records
    they considered. Only `decision:record` may do it, and administrators do not hold it.
  * A recorded decision is immutable. A correction is a NEW version with a reason; the original and every correction stay.
  * Issuing the official outcome to the institution is a separate, recorded act (a reference and/or the official file; no letter wording is generated).
  * The Council's ratification is recorded separately; the outcome vocabulary and whether issue waits for it come from the frozen configuration.
  * Closing the case needs every step the configuration requires, including that an open appeal window has run out or an appeal has been decided.
  * An external-sync problem (services/portal_sync.py) never undoes a decision and is always shown.
"""

import hashlib
import json
import uuid
from datetime import UTC

from fastapi import Request, UploadFile
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app import audit
from app.config import Settings
from app.domain import (
    DECISION_POLICY_STAGE,
    DECISION_TRANSITIONS,
    AppealEligibility,
    CommitteeStage,
    CouncilKind,
    DecisionAction,
    DecisionKind,
    DecisionState,
    GapReason,
    ReportKind,
    ReportStatus,
)
from app.evidence import current_documents
from app.models import User, utcnow
from app.models_applications import Application, ApplicationEvent
from app.models_committee import CommitteeCase, CommitteeReport, DecisionHandoff
from app.models_decisions import Appeal, BureauObservation, CouncilDecision, DecisionCase, EcDecision, OfficialIssuance, PostDecisionHandoff
from app.models_scrutiny import ScrutinyCase, ScrutinyFinding
from app.rbac import Permission, permissions_for
from app.security import as_utc
from app.services import committee_policy as cpolicy
from app.services import decision_texts as dtexts
from app.services import portal_sync
from app.services import private_files
from app.services import rules as rules_svc
from app.services import stages
from app.services.applications import config_of
from app.services.errors import Issue, conflict, invalid, not_found
from app.storage import LocalStorage


# ---- access and state -----------------------------------------------------------------------------------


def get_case(db: Session, user: User, case_id: uuid.UUID, request: Request | None) -> DecisionCase:
    """Decision cases are read by the decision roles (`decision:queue`). Anyone else: "not found" and an audit record."""
    case = db.get(DecisionCase, case_id, populate_existing=True)
    if case is None or Permission.DECISION_QUEUE not in permissions_for(user.role):
        if case is not None:
            audit.record(db, "decision.access_denied", request=request, actor=user, target_type="decision_case", target_id=case_id)
            db.commit()
        raise not_found("Decision case")
    return case


def lock_case(db: Session, case_id: uuid.UUID) -> DecisionCase:
    q = select(DecisionCase).where(DecisionCase.id == case_id).execution_options(populate_existing=True)
    if db.bind.dialect.name == "postgresql":
        q = q.with_for_update()
    case = db.scalars(q).one_or_none()
    if case is None:
        raise not_found("Decision case")
    return case


def application(db: Session, case: DecisionCase) -> Application:
    return db.get(Application, case.application_id)


def log(db: Session, case: DecisionCase, actor: User | None, action: str, *, request: Request | None = None, object_type: str | None = None, object_id: uuid.UUID | str | None = None,
        reason: str | None = None, detail: dict | None = None, from_state: str | None = None, to_state: str | None = None) -> None:
    from app.services.committee_core import log_event

    log_event(db, None, actor, action, request=request, object_type=object_type or "decision_case", object_id=object_id or case.id, from_state=from_state, to_state=to_state, reason=reason,
              detail={"decision_case_id": str(case.id), **(detail or {})}, stage="DECISION", application_id=case.application_id, institution_visible=action in ("outcome_issued", "decision_case_closed", "appeal_submitted", "appeal_decided", "ratification_recorded"))


def transition(db: Session, case: DecisionCase, action: DecisionAction, *, expected_version: int | None = None, **extra) -> DecisionState:
    current = DecisionState(case.state)
    target = DECISION_TRANSITIONS.get((current, action))
    if target is None:
        raise conflict("invalid_transition", f"That action is not allowed while the decision case is “{current.value}”.")
    if expected_version is not None and case.row_version != expected_version:
        raise conflict("stale_version", "This case was changed by someone else since you opened it. Reload it to see the latest state.")
    now = utcnow()
    res = db.execute(update(DecisionCase).where(DecisionCase.id == case.id, DecisionCase.state == current.value, DecisionCase.row_version == case.row_version).values(
        state=target.value, row_version=case.row_version + 1, updated_at=now, state_changed_at=now, **extra))
    if res.rowcount != 1:
        raise conflict("stale_version", "This case was changed by someone else at the same moment. Reload it and try again.")
    db.refresh(case)
    return target


def touch(db: Session, case: DecisionCase) -> None:
    res = db.execute(update(DecisionCase).where(DecisionCase.id == case.id, DecisionCase.row_version == case.row_version).values(row_version=case.row_version + 1, updated_at=utcnow()))
    if res.rowcount != 1:
        raise conflict("stale_version", "This case was changed by someone else at the same moment. Reload it and try again.")
    db.refresh(case)


def policy(case: DecisionCase) -> dict:
    return (case.policy_snapshot or {}).get("content", {})


def _canonical(d: dict) -> str:
    return json.dumps(d, sort_keys=True, separators=(",", ":"), default=str)


# ---- opening a case ------------------------------------------------------------------------------------


def open_case(db: Session, handoff: DecisionHandoff, app: Application, actor: User | None, request: Request | None = None) -> DecisionCase:
    """A completed route enters the decision queue. Idempotent (one case per application)."""
    from app.config import get_settings

    existing = db.scalar(select(DecisionCase).where(DecisionCase.application_id == app.id))
    if existing is not None:
        return existing
    settings = get_settings()
    config = config_of(app)
    pol = cpolicy.applicable(db, DECISION_POLICY_STAGE, config, settings)
    snapshot = cpolicy.freeze(pol) if pol else None
    blockers = cpolicy.decision_blockers(snapshot)
    needs_bureau = bool(((snapshot or {}).get("content", {}).get("decision", {}) or {}).get("bureau_observation_required", True))
    case = DecisionCase(
        application_id=app.id, handoff_id=handoff.id, rule_version_id=app.rule_version_id, state=(DecisionState.AWAITING_OBSERVATIONS if needs_bureau else DecisionState.READY_FOR_DECISION).value,
        policy_id=pol.id if pol else None, policy_snapshot=snapshot, blockers=blockers or None,
    )
    db.add(case)
    db.flush()
    log(db, case, actor, "decision_case_opened", request=request, to_state=case.state, detail={"rule_version_id": str(app.rule_version_id), "policy": cpolicy.ref(snapshot), "blockers": [b["code"] for b in blockers]})
    if blockers:
        rules_svc.record_gap(db, app.category_id, app.academic_year, GapReason.INCOMPLETE, {"missing": "decision_policy", "blockers": [b["code"] for b in blockers], "rule_version_id": str(app.rule_version_id)})
    db.flush()
    dtexts.case_opened(db, case.id, app)
    return case


# ---- what stands behind a decision ---------------------------------------------------------------------


def supporting_records(db: Session, case: DecisionCase) -> dict:
    """The exact records a decision-maker is asked to consider, with their versions and hashes. Read-only; nothing is derived from them."""
    app = application(db, case)
    sc = db.scalar(select(ScrutinyCase).where(ScrutinyCase.application_id == app.id))
    findings = list(db.scalars(select(ScrutinyFinding).where(ScrutinyFinding.application_id == app.id).order_by(ScrutinyFinding.sequence)))
    out: dict = {
        "scrutiny": {
            "case_id": str(sc.id) if sc else None, "state": sc.state if sc else None, "completion": sc.completion if sc else None,
            "findings": [{"sequence": f.sequence, "state": f.state, "issue_type": f.issue_type, "reason": f.state_reason} for f in findings],
        },
        "evc": None, "shc": None, "bureau": None,
        "documents": [{"item_key": k, "document_id": str(d.id), "version_number": d.version_number, "sha256": d.sha256} for k, d in sorted(current_documents(db, app.id).items())],
    }
    for stage, kind, key in ((CommitteeStage.EVC, ReportKind.EVC_REPORT, "evc"), (CommitteeStage.SHC, ReportKind.SHC_RECOMMENDATION, "shc")):
        cc = db.scalar(select(CommitteeCase).where(CommitteeCase.application_id == app.id, CommitteeCase.stage == stage.value))
        if cc is None:
            continue
        rep = db.scalar(select(CommitteeReport).where(CommitteeReport.case_id == cc.id, CommitteeReport.kind == kind.value, CommitteeReport.status == ReportStatus.SUBMITTED.value).order_by(CommitteeReport.version_number.desc()))
        out[key] = {"case_id": str(cc.id), "state": cc.state, "completion": cc.completion, "record": {"id": str(rep.id), "kind": rep.kind, "version": rep.version_number, "content_hash": rep.content_hash,
                                                                                               "option": (rep.content or {}).get("option")} if rep else None}
    obs = latest_observations(db, case.id)
    if obs:
        out["bureau"] = {"observation_ids": [str(o.id) for o in obs], "latest": {"id": str(obs[-1].id), "sequence": obs[-1].sequence}}
    return out


def considerable(records: dict) -> list[str]:
    """Keys of the records that exist and must be confirmed as considered."""
    keys = ["scrutiny"]
    for k in ("evc", "shc", "bureau"):
        if records.get(k):
            keys.append(k)
    return keys


def latest_observations(db: Session, case_id: uuid.UUID) -> list[BureauObservation]:
    return list(db.scalars(select(BureauObservation).where(BureauObservation.case_id == case_id).order_by(BureauObservation.sequence)))


def decisions(db: Session, case_id: uuid.UUID) -> list[EcDecision]:
    return list(db.scalars(select(EcDecision).where(EcDecision.case_id == case_id).order_by(EcDecision.version_number)))


def current_decision(db: Session, case_id: uuid.UUID) -> EcDecision | None:
    return db.scalar(select(EcDecision).where(EcDecision.case_id == case_id).order_by(EcDecision.version_number.desc()))


def live_issuance(db: Session, case_id: uuid.UUID) -> OfficialIssuance | None:
    return db.scalar(select(OfficialIssuance).where(OfficialIssuance.case_id == case_id, OfficialIssuance.status == "ISSUED"))


def live_ratification(db: Session, decision_id: uuid.UUID) -> CouncilDecision | None:
    return db.scalar(select(CouncilDecision).where(CouncilDecision.kind == CouncilKind.RATIFICATION.value, CouncilDecision.decision_id == decision_id, CouncilDecision.is_live.is_(True)))


def blockers(db: Session, case: DecisionCase) -> list[dict]:
    """Why a decision cannot be recorded now. Nothing is worked around: the case simply waits, and says why."""
    app = application(db, case)
    out = list(cpolicy.decision_blockers(case.policy_snapshot))
    why = stages._blocked_reason(db, app)
    if why:
        out.append({"code": "stage_incomplete", "message": f"A stage the approved route requires is not complete: {why}"})
    d = policy(case).get("decision", {})
    if d.get("bureau_observation_required", True) and not latest_observations(db, case.id):
        out.append({"code": "bureau_observation_missing", "message": "The Regulation Bureau's observations have not been recorded (APH Ch. I cl. 1.10(a))."})
    return out


# ---- Regulation Bureau observations -------------------------------------------------------------------------


def record_observation(db: Session, user: User, case_id: uuid.UUID, text: str, idempotency_key: str, request: Request | None) -> BureauObservation:
    case = get_case(db, user, case_id, request)
    prior = db.scalar(select(BureauObservation).where(BureauObservation.idempotency_key == idempotency_key))
    if prior is not None:
        if prior.case_id != case.id:
            raise conflict("idempotency_key_reused", "That key was used for a different case.")
        return prior
    case = lock_case(db, case.id)
    if case.state not in (DecisionState.AWAITING_OBSERVATIONS.value, DecisionState.READY_FOR_DECISION.value):
        raise conflict("observation_closed", "Observations can no longer be added: a decision has been recorded for this case.")
    if len((text or "").strip()) < 10:
        raise invalid("Write the Regulation Bureau's observations.", [Issue("text", "Observations are needed.", "text")])
    seq = (db.scalar(select(func.max(BureauObservation.sequence)).where(BureauObservation.case_id == case.id)) or 0) + 1
    obs = BureauObservation(case_id=case.id, sequence=seq, text=text.strip(), recorded_by_id=user.id, idempotency_key=idempotency_key)
    db.add(obs)
    db.flush()
    from_state = case.state
    if case.state == DecisionState.AWAITING_OBSERVATIONS.value:
        transition(db, case, DecisionAction.OBSERVE)
    else:
        touch(db, case)
    log(db, case, user, "bureau_observation_recorded", request=request, object_type="bureau_observation", object_id=obs.id, from_state=from_state, to_state=case.state, detail={"sequence": seq})
    db.flush()
    dtexts.observation_recorded(db, case.id, application(db, case), obs.id)
    return obs


# ---- the decision ---------------------------------------------------------------------------------------


def _outcomes(case: DecisionCase) -> dict[str, dict]:
    return {o["key"]: o for o in policy(case).get("decision", {}).get("outcomes", [])}


def _validate_decision(case: DecisionCase, data: dict, records: dict) -> dict:
    issues: list[Issue] = []
    outcome = data.get("outcome")
    out = _outcomes(case).get(outcome or "")
    if out is None:
        issues.append(Issue("outcome", "Choose one of the outcomes the configuration offers.", "outcome"))
    reasons = (data.get("reasons") or "").strip()
    if len(reasons) < 10:
        issues.append(Issue("reasons", "Record the reasons for the decision.", "reasons"))
    speaking = (data.get("speaking_order") or "").strip() or None
    if out and out.get("requires_speaking_order") and (speaking is None or len(speaking) < 10):
        issues.append(Issue("speaking_order", "A Speaking Order stating the reasons is required with this outcome (APH Ch. I cl. 1.10(b)).", "speaking_order"))
    need = set(considerable(records))
    given = set(data.get("considered") or [])
    missing = sorted(need - given)
    if missing:
        issues.append(Issue("consideration_missing", "Confirm that you considered: " + ", ".join(missing) + ".", "considered"))
    if issues:
        raise invalid("The decision was not recorded.", issues)
    return {"outcome": outcome, "reasons": reasons, "speaking_order": speaking, "meeting_reference": (data.get("meeting_reference") or "").strip()[:200] or None}


def _hash(case: DecisionCase, version: int, v: dict, refs: dict) -> str:
    return hashlib.sha256(_canonical({"case": str(case.id), "version": version, **v, "supporting_refs": refs}).encode()).hexdigest()


def _config_snapshot(case: DecisionCase, app: Application) -> dict:
    config = config_of(app)
    return {"id": str(case.rule_version_id), "rule_set": config["rule_set"]["key"], "number": config["version"]["number"], "classification": config["rule_set"]["classification"]}


def record_decision(db: Session, user: User, case_id: uuid.UUID, data: dict, idempotency_key: str, row_version: int, request: Request | None) -> tuple[DecisionCase, EcDecision, bool]:
    """-> (case, decision, replay). Recorded by a person; never derived."""
    case = get_case(db, user, case_id, request)
    prior = db.scalar(select(EcDecision).where(EcDecision.idempotency_key == idempotency_key))
    if prior is not None:
        if prior.case_id != case.id:
            raise conflict("idempotency_key_reused", "That key was used for a different case.")
        return case, prior, True
    case = lock_case(db, case.id)
    if case.row_version != row_version:
        raise conflict("stale_version", "This case was changed by someone else since you opened it. Reload it to see the latest state.")
    if case.state != DecisionState.READY_FOR_DECISION.value:
        raise conflict("invalid_transition", f"A decision cannot be recorded while the case is “{case.state}”.")
    bl = blockers(db, case)
    if bl:
        raise conflict("decision_blocked", bl[0]["message"], [Issue(b["code"], b["message"]) for b in bl])
    app = application(db, case)
    records = supporting_records(db, case)
    v = _validate_decision(case, data, records)
    dec = EcDecision(
        case_id=case.id, version_number=1, kind=DecisionKind.ORIGINAL.value, outcome=v["outcome"], reasons=v["reasons"], speaking_order=v["speaking_order"], meeting_reference=v["meeting_reference"],
        supporting_refs={**records, "considered": sorted(data["considered"])}, rule_version=_config_snapshot(case, app), policy_ref=cpolicy.ref(case.policy_snapshot), decided_by_id=user.id,
        content_hash=_hash(case, 1, v, records), idempotency_key=idempotency_key,
    )
    db.add(dec)
    db.flush()
    from_state = case.state
    transition(db, case, DecisionAction.RECORD)
    log(db, case, user, "decision_recorded", request=request, object_type="ec_decision", object_id=dec.id, from_state=from_state, to_state=case.state, reason=v["reasons"],
        detail={"version": 1, "outcome": v["outcome"], "content_hash": dec.content_hash, "considered": dec.supporting_refs["considered"], "recorded_by_a_person": True})
    db.flush()
    dtexts.decision_recorded(db, case.id, app, dec.id, 1, False)
    return case, dec, False


def correct_decision(db: Session, user: User, case_id: uuid.UUID, data: dict, correction_reason: str, idempotency_key: str, row_version: int, request: Request | None) -> tuple[DecisionCase, EcDecision, bool]:
    """An authorised correction is a new version; the earlier one (and any issue of it) stays on record, marked superseded where it was issued."""
    case = get_case(db, user, case_id, request)
    prior = db.scalar(select(EcDecision).where(EcDecision.idempotency_key == idempotency_key))
    if prior is not None:
        if prior.case_id != case.id:
            raise conflict("idempotency_key_reused", "That key was used for a different case.")
        return case, prior, True
    case = lock_case(db, case.id)
    if case.row_version != row_version:
        raise conflict("stale_version", "This case was changed by someone else since you opened it. Reload it to see the latest state.")
    if case.state not in (DecisionState.DECIDED.value, DecisionState.ISSUED.value):
        raise conflict("invalid_transition", f"A decision can be corrected only after it is recorded and before the case is closed (the case is “{case.state}”).")
    if len((correction_reason or "").strip()) < 10:
        raise invalid("Record why the decision is being corrected.", [Issue("correction_reason", "A reason is needed.", "correction_reason")])
    last = current_decision(db, case.id)
    app = application(db, case)
    records = supporting_records(db, case)
    v = _validate_decision(case, data, records)
    n = last.version_number + 1
    dec = EcDecision(
        case_id=case.id, version_number=n, kind=DecisionKind.CORRECTION.value, supersedes_id=last.id, correction_reason=correction_reason.strip(), outcome=v["outcome"], reasons=v["reasons"],
        speaking_order=v["speaking_order"], meeting_reference=v["meeting_reference"], supporting_refs={**records, "considered": sorted(data["considered"])}, rule_version=_config_snapshot(case, app),
        policy_ref=cpolicy.ref(case.policy_snapshot), decided_by_id=user.id, content_hash=_hash(case, n, v, records), idempotency_key=idempotency_key,
    )
    db.add(dec)
    db.flush()
    from_state = case.state
    if case.state == DecisionState.ISSUED.value:
        iss = live_issuance(db, case.id)
        if iss is not None:
            db.execute(update(OfficialIssuance).where(OfficialIssuance.id == iss.id, OfficialIssuance.status == "ISSUED").values(status="SUPERSEDED", superseded_at=utcnow()))
        transition(db, case, DecisionAction.REOPEN_FOR_ISSUE)  # the corrected decision needs its own issue; the appeal window stays anchored at the first issue
    else:
        touch(db, case)
    log(db, case, user, "decision_corrected", request=request, object_type="ec_decision", object_id=dec.id, from_state=from_state, to_state=case.state, reason=correction_reason,
        detail={"version": n, "supersedes": str(last.id), "outcome": v["outcome"], "previous_outcome": last.outcome, "content_hash": dec.content_hash})
    db.flush()
    dtexts.decision_recorded(db, case.id, app, dec.id, n, True)
    return case, dec, False


# ---- issuing the official outcome -----------------------------------------------------------------------


def issue_outcome(
    db: Session, user: User, case_id: uuid.UUID, reference: str | None, upload: UploadFile | None, idempotency_key: str, row_version: int, request: Request | None, settings: Settings, storage: LocalStorage,
) -> tuple[DecisionCase, OfficialIssuance, bool]:
    """Issue the recorded decision to the institution: a document reference and/or the official file. No letter text is produced by the system."""
    case = get_case(db, user, case_id, request)
    prior = db.scalar(select(OfficialIssuance).where(OfficialIssuance.idempotency_key == idempotency_key))
    if prior is not None:
        if prior.case_id != case.id:
            raise conflict("idempotency_key_reused", "That key was used for a different case.")
        return case, prior, True
    case = lock_case(db, case.id)
    if case.row_version != row_version:
        raise conflict("stale_version", "This case was changed by someone else since you opened it. Reload it to see the latest state.")
    if case.state != DecisionState.DECIDED.value:
        raise conflict("invalid_transition", f"There is no recorded decision waiting to be issued (the case is “{case.state}”).")
    dec = current_decision(db, case.id)
    ref = (reference or "").strip() or None
    if ref is None and upload is None:
        raise invalid("Give the official document reference, upload the official file, or both.", [Issue("document_reference", "A reference or a file is needed.", "document_reference")])
    rat = policy(case).get("ratification", {})
    if rat.get("required") and rat.get("issue_requires_ratification") and (live_ratification(db, dec.id) is None or live_ratification(db, dec.id).outcome != "RATIFIED"):
        raise conflict("ratification_required", "The configuration requires the Council's ratification before the outcome is issued, and it has not been recorded for this decision.")
    stored = private_files.store(upload, settings, storage) if upload is not None else None
    try:
        iss = OfficialIssuance(
            case_id=case.id, decision_id=dec.id, document_reference=ref, storage_key=stored.key if stored else None, original_filename=stored.name if stored else None, media_type=stored.media_type if stored else None,
            size_bytes=stored.size if stored else None, sha256=stored.sha256 if stored else None, issued_by_id=user.id, idempotency_key=idempotency_key,
        )
        db.add(iss)
        db.flush()
        now = utcnow()
        from_state = case.state
        extra = {} if case.first_issued_at else {"first_issued_at": now}
        transition(db, case, DecisionAction.ISSUE, **extra)
        app = application(db, case)
        db.add(ApplicationEvent(application_id=app.id, actor_id=user.id, action="decision_issued", detail={"outcome": dec.outcome, "decision_version": dec.version_number, "approved_by_system": False}))
        log(db, case, user, "outcome_issued", request=request, object_type="official_issuance", object_id=iss.id, from_state=from_state, to_state=case.state,
            detail={"outcome": dec.outcome, "decision_version": dec.version_number, "reference": ref, "has_file": stored is not None, "first_issue": bool(extra)})
        db.flush()
        dtexts.outcome_issued(db, case.id, app, iss.id, dec.outcome, ref, dec.version_number > 1)
        sync = portal_sync.record(db, app.id, "official_issuance", iss.id, "DECISION_ISSUED")
        if sync.status in ("SYNC_PENDING", "FAILED", "RECONCILIATION_REQUIRED"):
            dtexts.sync_pending(db, case.id, app, sync.id)
    except BaseException:
        if stored is not None:
            storage.delete(stored.key)
        raise
    return case, iss, False


# ---- Council ratification --------------------------------------------------------------------------------


def record_ratification(db: Session, user: User, case_id: uuid.UUID, outcome: str, reasons: str, reference: str | None, correction_reason: str | None, idempotency_key: str, request: Request | None) -> tuple[CouncilDecision, bool]:
    case = get_case(db, user, case_id, request)
    prior = db.scalar(select(CouncilDecision).where(CouncilDecision.idempotency_key == idempotency_key))
    if prior is not None:
        return prior, True
    case = lock_case(db, case.id)
    rat = policy(case).get("ratification", {})
    if not rat.get("required"):
        raise conflict("ratification_not_configured", "The configuration does not establish Council ratification for this case, so none can be recorded.")
    if case.state not in (DecisionState.DECIDED.value, DecisionState.ISSUED.value):
        raise conflict("invalid_transition", f"A decision must be recorded before the Council ratifies it (the case is “{case.state}”).")
    if outcome not in {o["key"] for o in rat.get("outcomes", [])}:
        raise invalid("Choose one of the outcomes the configuration offers.", [Issue("outcome", "Not a configured outcome.", "outcome")])
    if len((reasons or "").strip()) < 5:
        raise invalid("Record the Council's reasons or note.", [Issue("reasons", "A note is needed.", "reasons")])
    dec = current_decision(db, case.id)
    live = live_ratification(db, dec.id)
    if live is not None and not (correction_reason and len(correction_reason.strip()) >= 10):
        raise conflict("already_ratified", "The Council's step on this decision is already recorded. A correction needs a reason and supersedes it; the original stays.")
    if live is not None:
        db.execute(update(CouncilDecision).where(CouncilDecision.id == live.id).values(is_live=False))  # retire the earlier record first: one live record per decision
        db.flush()
    row = CouncilDecision(
        kind=CouncilKind.RATIFICATION.value, decision_id=dec.id, outcome=outcome, reasons=reasons.strip(), reference=(reference or "").strip()[:300] or None, recorded_by_id=user.id,
        supersedes_id=live.id if live else None, correction_reason=correction_reason.strip() if live else None, policy_ref=cpolicy.ref(case.policy_snapshot), idempotency_key=idempotency_key,
        content_hash=hashlib.sha256(_canonical({"decision": dec.content_hash, "outcome": outcome, "reasons": reasons.strip()}).encode()).hexdigest(),
    )
    db.add(row)
    db.flush()
    if live is not None:
        db.execute(update(CouncilDecision).where(CouncilDecision.id == live.id).values(superseded_by_id=row.id))
    touch(db, case)
    app = application(db, case)
    log(db, case, user, "ratification_recorded", request=request, object_type="council_decision", object_id=row.id, reason=reasons,
        detail={"outcome": outcome, "decision_version": dec.version_number, "correction": live is not None})
    db.flush()
    dtexts.ratification_recorded(db, case.id, app, row.id, outcome)
    portal_sync.record(db, app.id, "council_decision", row.id, "RATIFICATION_RECORDED")
    return row, False


# ---- closing the case -----------------------------------------------------------------------------------


def closure_blockers(db: Session, case: DecisionCase, settings: Settings) -> list[dict]:
    """What still keeps the decision stage open. The case closes only when none remains."""
    from app.services import appeals

    out: list[dict] = []
    dec = current_decision(db, case.id)
    rat = policy(case).get("ratification", {})
    if rat.get("required"):
        r = live_ratification(db, dec.id) if dec else None
        if r is None:
            out.append({"code": "ratification_pending", "message": "The Council's step on the decision has not been recorded."})
        elif r.outcome != "RATIFIED":
            out.append({"code": "not_ratified", "message": "The Council did not ratify the decision. An authorised correction or a further decision is needed before the case can close."})
    app = application(db, case)
    ap = db.scalar(select(Appeal).where(Appeal.application_id == app.id))
    if ap is not None:
        if ap.status != "COUNCIL_DECIDED":
            out.append({"code": "appeal_open", "message": "An appeal is open; the case closes after the Council's decision on it is recorded."})
    else:
        e = appeals.eligibility(db, app, case, settings)
        if e["state"] == AppealEligibility.ELIGIBLE.value:
            out.append({"code": "appeal_window_open", "message": f"The institution may still appeal until {e['deadline_at']}."})
        elif e["state"] == AppealEligibility.BLOCKED_BY_CONFIGURATION.value:
            out.append({"code": "appeal_window_unknown", "message": "Whether and until when the institution may appeal cannot be determined from the configuration, so the case is not closed on a guess."})
    return out


def close_case(db: Session, user: User, case_id: uuid.UUID, row_version: int, request: Request | None, settings: Settings) -> DecisionCase:
    case = get_case(db, user, case_id, request)
    case = lock_case(db, case.id)
    if case.row_version != row_version:
        raise conflict("stale_version", "This case was changed by someone else since you opened it. Reload it to see the latest state.")
    if case.state != DecisionState.ISSUED.value:
        raise conflict("invalid_transition", f"Only a case whose outcome has been issued can be closed (the case is “{case.state}”).")
    bl = closure_blockers(db, case, settings)
    if bl:
        raise conflict("closure_blocked", bl[0]["message"], [Issue(b["code"], b["message"]) for b in bl])
    return _close(db, case, user, request)


def _close(db: Session, case: DecisionCase, actor: User | None, request: Request | None) -> DecisionCase:
    app = application(db, case)
    dec = current_decision(db, case.id)
    iss = live_issuance(db, case.id)
    ap = db.scalar(select(Appeal).where(Appeal.application_id == app.id))
    cd = db.scalar(select(CouncilDecision).where(CouncilDecision.appeal_id == ap.id, CouncilDecision.superseded_by_id.is_(None))) if ap else None
    rat = live_ratification(db, dec.id) if dec else None
    basis = {
        "decision": {"id": str(dec.id), "version": dec.version_number, "outcome": dec.outcome, "content_hash": dec.content_hash},
        "issuance": {"id": str(iss.id), "issued_at": iss.issued_at.isoformat()} if iss else None,
        "ratification": {"id": str(rat.id), "outcome": rat.outcome} if rat else None,
        "appeal": {"id": str(ap.id), "status": ap.status, "council_outcome": cd.outcome if cd else None} if ap else None,
        "official_outcome": cd.outcome if cd else dec.outcome, "note": "A hand-off record only. The later post-approval stage is not part of this system yet.",
    }
    from_state = case.state
    now = utcnow()
    transition(db, case, DecisionAction.CLOSE, closed_at=now, closure=basis)
    db.add(PostDecisionHandoff(application_id=app.id, decision_case_id=case.id, basis=basis, created_by_id=actor.id if actor else None))
    log(db, case, actor, "decision_case_closed", request=request, from_state=from_state, to_state=case.state, detail={"official_outcome": basis["official_outcome"]})
    db.flush()
    dtexts.case_closed(db, case.id, app)
    return case


def finalise_expired(db: Session, settings: Settings) -> int:
    """Close every issued case whose remaining conditions have been met by the passage of time (an appeal window that has run out). Idempotent; used by the reconcile command."""
    closed = 0
    for case in list(db.scalars(select(DecisionCase).where(DecisionCase.state == DecisionState.ISSUED.value))):
        if not closure_blockers(db, case, settings):
            _close(db, lock_case(db, case.id), None, None)
            closed += 1
    db.commit()
    return closed


_ = (UTC, as_utc)
