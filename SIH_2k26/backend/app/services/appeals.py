"""Appeals against an issued Executive Committee decision, the SAC stage they open, and the Council's decision on them.

An appeal is against the applicable Executive Committee decision (cl. 1.11, 2.24), not against an EVC report or an SHC recommendation in isolation. It is
allowed at most once. Whether it is allowed, and until when, is evaluated from the frozen configuration (`appeal_rules.py`, the decision policy); where that
configuration cannot answer, the appeal is blocked and says why, and no deadline is invented. Submitting an appeal, or evidence with it, decides nothing and
promises nothing: the SAC recommends, and the Council decides (finally, under the handbook).
"""

import hashlib
import json
import uuid
from urllib.parse import urlparse

from fastapi import Request, UploadFile
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import audit
from app.config import Settings, get_settings
from app.domain import (
    DECISION_POLICY_STAGE,
    INITIAL_STATE,
    AppealEligibility,
    AppealStatus,
    CommitteeStage,
    CouncilKind,
    DecisionState,
    GapReason,
)
from app.models import User, utcnow
from app.models_applications import Application, ApplicationEvent
from app.models_committee import CommitteeCase
from app.models_decisions import Appeal, AppealDocument, CouncilDecision, DecisionCase, EcDecision
from app.security import as_utc
from app.services import appeal_rules
from app.services import applications as app_svc
from app.services import committee_core as core
from app.services import committee_policy as cpolicy
from app.services import decision_texts as dtexts
from app.services import decisions as dsvc
from app.services import portal_sync, private_files
from app.services import rules as rules_svc
from app.services.applications import config_of
from app.services.errors import Issue, ServiceError, conflict, invalid, not_found
from app.storage import LocalStorage

MIN_GROUNDS = 20


def _canonical(d: dict) -> str:
    return json.dumps(d, sort_keys=True, separators=(",", ":"), default=str)


def get_for_application(db: Session, application_id: uuid.UUID) -> Appeal | None:
    return db.scalar(select(Appeal).where(Appeal.application_id == application_id).execution_options(populate_existing=True))


def eligibility(db: Session, app: Application, case: DecisionCase | None, settings: Settings) -> dict:
    """May this institution appeal now? Evaluated from the frozen configuration each time it is asked; the answer names the clause and the deadline."""
    now = utcnow()
    base: dict = {"state": None, "message": "", "deadline_at": None, "trigger_at": None, "outcome": None, "window": None, "clause_ref": None, "evaluated_at": now.isoformat()}

    def done(state: AppealEligibility, message: str, **extra) -> dict:
        return {**base, "state": state.value, "message": message, **extra}

    if case is None or case.state == DecisionState.AWAITING_OBSERVATIONS.value:
        return done(AppealEligibility.NOT_ISSUED, "No official outcome has been issued, so there is nothing to appeal against.")
    pol = (case.policy_snapshot or {}).get("content", {})
    ap = pol.get("appeal")
    if not ap:
        return done(AppealEligibility.BLOCKED_BY_CONFIGURATION, "No verified configuration states whether and how an appeal may be made for this application's rule set. Nothing is assumed; it is recorded for the rule configuration reviewers.")
    if not ap.get("permitted"):
        return done(AppealEligibility.NOT_PERMITTED, "The configuration does not permit an appeal here.", clause_ref=ap.get("clause_ref"))
    existing = get_for_application(db, app.id)
    if existing is not None:
        return done(AppealEligibility.ALREADY_APPEALED, "An appeal was already made. The handbook allows an appeal once.", clause_ref=ap.get("clause_ref"))
    iss = dsvc.live_issuance(db, case.id)
    if case.state not in (DecisionState.ISSUED.value, DecisionState.CLOSED.value) or iss is None or case.first_issued_at is None:
        return done(AppealEligibility.NOT_ISSUED, "No official outcome is currently issued, so there is nothing to appeal against.")
    dec = db.get(EcDecision, iss.decision_id)
    if dec.outcome not in ap.get("appealable_outcomes", []):
        return done(AppealEligibility.OUTCOME_NOT_APPEALABLE, "The configuration does not list this outcome as one that can be appealed.", outcome=dec.outcome, clause_ref=ap.get("clause_ref"))
    window = appeal_rules.resolve_window(config_of(app), settings, dec.outcome)
    if not window.usable:
        return done(AppealEligibility.BLOCKED_BY_CONFIGURATION, f"The appeal deadline cannot be established: {window.message}", outcome=dec.outcome, window=window.stable(), clause_ref=window.clause_ref or ap.get("clause_ref"))
    trigger = as_utc(case.first_issued_at)
    deadline = window.deadline(trigger)
    extra = dict(outcome=dec.outcome, window=window.stable(), trigger_at=trigger.isoformat(), deadline_at=deadline.isoformat(), clause_ref=window.clause_ref or ap.get("clause_ref"))
    if now > deadline:
        return done(AppealEligibility.WINDOW_CLOSED, "The appeal period has ended.", **extra)
    return done(AppealEligibility.ELIGIBLE, "The institution may appeal once, against this decision, until the deadline.", **extra)


# ---- submitting -----------------------------------------------------------------------------------------


def _activate_sac(db: Session, app: Application, appeal: Appeal, actor: User, request: Request | None, settings: Settings) -> CommitteeCase:
    config = config_of(app)
    pol = cpolicy.applicable(db, CommitteeStage.SAC, config, settings)
    snapshot = cpolicy.freeze(pol) if pol else None
    bl = cpolicy.blockers_for(CommitteeStage.SAC, snapshot)
    case = CommitteeCase(
        application_id=app.id, stage=CommitteeStage.SAC.value, rule_version_id=app.rule_version_id, state=INITIAL_STATE[CommitteeStage.SAC].value,
        trigger={"stage_key": "sac", "from_stage_key": "decision", "source": "appeal", "appeal_id": str(appeal.id), "against_decision_id": str(appeal.against_decision_id),
                 "rule_version": {"id": str(app.rule_version_id), "rule_set": config["rule_set"]["key"], "number": config["version"]["number"], "classification": config["rule_set"]["classification"]},
                 "shortcomings": []},
        policy_id=pol.id if pol else None, policy_snapshot=snapshot, blockers=bl or None,
    )
    db.add(case)
    db.flush()
    core.log_event(db, case, actor, "stage_activated", request=request, object_type="committee_case", object_id=case.id, to_state=case.state, institution_visible=True,
                   detail={"source": "appeal", "appeal_id": str(appeal.id), "policy": cpolicy.ref(snapshot), "blockers": [b["code"] for b in bl]})
    if bl:
        rules_svc.record_gap(db, app.category_id, app.academic_year, GapReason.INCOMPLETE, {"missing": "committee_policy:SAC", "blockers": [b["code"] for b in bl], "rule_version_id": str(app.rule_version_id)})
    return case


def submit(db: Session, user: User, application_id: uuid.UUID, grounds: str, idempotency_key: str, request: Request | None, settings: Settings) -> tuple[Appeal, bool]:
    """-> (appeal, replay). Refused (and the refusal audited) unless the frozen configuration makes the institution eligible now."""
    app = app_svc.get_owned(db, user, application_id, request)
    prior = db.scalar(select(Appeal).where(Appeal.idempotency_key == idempotency_key))
    if prior is not None:
        if prior.application_id != app.id:
            raise conflict("idempotency_key_reused", "That key was used for a different application.")
        return prior, True
    case = db.scalar(select(DecisionCase).where(DecisionCase.application_id == app.id))
    if case is not None:
        case = dsvc.lock_case(db, case.id)
    e = eligibility(db, app, case, settings)
    if e["state"] != AppealEligibility.ELIGIBLE.value:
        audit.record(db, "appeal.refused", request=request, actor=user, target_type="application", target_id=app.id, detail={"state": e["state"], "message": e["message"]})
        db.commit()
        code = "already_appealed" if e["state"] == AppealEligibility.ALREADY_APPEALED.value else "appeal_not_eligible"
        raise ServiceError(409, code, e["message"], [Issue(e["state"], e["message"], "eligibility")])
    if len((grounds or "").strip()) < MIN_GROUNDS:
        raise invalid("State the grounds of the appeal.", [Issue("grounds", f"Please give at least {MIN_GROUNDS} characters.", "grounds")])
    iss = dsvc.live_issuance(db, case.id)
    appeal = Appeal(
        application_id=app.id, institution_id=app.institution_id, decision_case_id=case.id, against_decision_id=iss.decision_id, grounds=grounds.strip(), eligibility=e, submitted_by_id=user.id,
        idempotency_key=idempotency_key, status=AppealStatus.UNDER_SAC_REVIEW.value,
    )
    try:
        db.add(appeal)
        db.flush()
    except IntegrityError:
        db.rollback()
        raise conflict("already_appealed", "An appeal was already made. The handbook allows an appeal once.") from None
    sac = _activate_sac(db, app, appeal, user, request, settings)
    db.execute(update(Appeal).where(Appeal.id == appeal.id).values(sac_case_id=sac.id))
    db.refresh(appeal)
    db.add(ApplicationEvent(application_id=app.id, actor_id=user.id, action="appeal_submitted", detail={"decision_version": e.get("outcome"), "decided": False}))
    dsvc.log(db, case, user, "appeal_submitted", request=request, object_type="appeal", object_id=appeal.id,
             detail={"against_decision_id": str(appeal.against_decision_id), "eligibility": {k: e[k] for k in ("state", "deadline_at", "clause_ref")}, "sac_case_id": str(sac.id)})
    db.flush()
    dtexts.appeal_submitted(db, appeal.id, sac.id, app)
    return appeal, False


def _own_open_appeal(db: Session, user: User, appeal_id: uuid.UUID, request: Request | None) -> tuple[Appeal, Application]:
    ap = db.get(Appeal, appeal_id, populate_existing=True)
    app = db.get(Application, ap.application_id) if ap else None
    if ap is None or app is None or user.institution_id is None or app.institution_id != user.institution_id:
        if ap is not None:
            audit.record(db, "appeal.access_denied", request=request, actor=user, target_type="appeal", target_id=ap.id)
            db.commit()
        raise not_found("Appeal")
    return ap, app


def add_file(db: Session, user: User, appeal_id: uuid.UUID, upload: UploadFile, description: str, request: Request | None, settings: Settings, storage: LocalStorage) -> AppealDocument:
    ap, app = _own_open_appeal(db, user, appeal_id, request)
    if ap.status != AppealStatus.UNDER_SAC_REVIEW.value:
        raise conflict("appeal_evidence_closed", "Supporting evidence can no longer be added: the SAC stage of this appeal is complete.")
    stored = private_files.store(upload, settings, storage)
    try:
        doc = AppealDocument(appeal_id=ap.id, kind="FILE", description=(description or "").strip()[:500], storage_key=stored.key, original_filename=stored.name, media_type=stored.media_type,
                             size_bytes=stored.size, sha256=stored.sha256, uploaded_by_id=user.id)
        db.add(doc)
        db.flush()
        audit.record(db, "appeal.document_added", request=request, actor=user, target_type="appeal", target_id=ap.id, detail={"document_id": str(doc.id), "kind": "FILE", "sha256": stored.sha256})
        db.commit()
    except BaseException:
        db.rollback()
        storage.delete(stored.key)
        raise
    return doc


def add_link(db: Session, user: User, appeal_id: uuid.UUID, url: str, description: str, request: Request | None) -> AppealDocument:
    ap, app = _own_open_appeal(db, user, appeal_id, request)
    if ap.status != AppealStatus.UNDER_SAC_REVIEW.value:
        raise conflict("appeal_evidence_closed", "Supporting evidence can no longer be added: the SAC stage of this appeal is complete.")
    parsed = urlparse((url or "").strip())
    if parsed.scheme not in ("http", "https") or not parsed.netloc or len(url) > 1000:
        raise invalid("Enter a full web address starting with http:// or https://.", [Issue("url", "Not a valid address.", "url")])
    if not (description or "").strip():
        raise invalid("Say what the linked material is.", [Issue("description", "A description is needed.", "description")])
    doc = AppealDocument(appeal_id=ap.id, kind="LINK", description=description.strip()[:500], url=url.strip(), uploaded_by_id=user.id)
    db.add(doc)
    db.flush()
    audit.record(db, "appeal.document_added", request=request, actor=user, target_type="appeal", target_id=ap.id, detail={"document_id": str(doc.id), "kind": "LINK", "host": parsed.netloc})
    return doc


# ---- the SAC stage finishing; the Council's decision ---------------------------------------------------------


def on_sac_completed(db: Session, sac: CommitteeCase, actor: User, request: Request | None) -> Appeal:
    """The SAC stage is complete: its recommendation now waits for the Council. This is not the Council's decision."""
    ap = db.scalar(select(Appeal).where(Appeal.sac_case_id == sac.id))
    if ap is None:
        raise not_found("Appeal")
    res = db.execute(update(Appeal).where(Appeal.id == ap.id, Appeal.status == AppealStatus.UNDER_SAC_REVIEW.value).values(
        status=AppealStatus.AWAITING_COUNCIL_DECISION.value, sac_completed_at=utcnow(), row_version=ap.row_version + 1))
    if res.rowcount != 1:
        return ap  # already moved on: idempotent
    db.refresh(ap)
    case = db.get(DecisionCase, ap.decision_case_id)
    dsvc.log(db, case, actor, "sac_completed", request=request, object_type="appeal", object_id=ap.id, detail={"sac_case_id": str(sac.id), "decided": False})
    db.flush()
    dtexts.sac_ready_for_council(db, ap.id, case.id, db.get(Application, ap.application_id))
    return ap


def council_outcomes(case: DecisionCase) -> dict[str, dict]:
    return {o["key"]: o for o in dsvc.policy(case).get("appeal", {}).get("council_outcomes", [])}


def record_council_decision(
    db: Session, user: User, appeal_id: uuid.UUID, outcome: str, reasons: str, speaking_order: str | None, reference: str | None, correction_reason: str | None, idempotency_key: str, request: Request | None,
) -> tuple[CouncilDecision, bool]:
    """The Council's decision on an appeal, recorded by an authorised person after the SAC stage. Immutable; a correction supersedes with a reason."""
    ap = db.get(Appeal, appeal_id, populate_existing=True)
    if ap is None:
        raise not_found("Appeal")
    prior = db.scalar(select(CouncilDecision).where(CouncilDecision.idempotency_key == idempotency_key))
    if prior is not None:
        return prior, True
    case = dsvc.lock_case(db, ap.decision_case_id)
    live = db.scalar(select(CouncilDecision).where(CouncilDecision.kind == CouncilKind.APPEAL_DECISION.value, CouncilDecision.appeal_id == ap.id, CouncilDecision.is_live.is_(True)))
    if live is None and ap.status != AppealStatus.AWAITING_COUNCIL_DECISION.value:
        raise conflict("sac_not_complete", "The Council's decision is recorded after the SAC stage is complete (the appeal is “%s”)." % ap.status)
    if live is not None and not (correction_reason and len(correction_reason.strip()) >= 10):
        raise conflict("already_decided", "The Council's decision on this appeal is already recorded. A correction needs a reason and supersedes it; the original stays.")
    outs = council_outcomes(case)
    o = outs.get(outcome or "")
    if o is None:
        raise invalid("Choose one of the outcomes the configuration offers.", [Issue("outcome", "Not a configured outcome.", "outcome")])
    if len((reasons or "").strip()) < 10:
        raise invalid("Record the Council's reasons.", [Issue("reasons", "Reasons are needed.", "reasons")])
    so = (speaking_order or "").strip() or None
    if o.get("requires_speaking_order") and (so is None or len(so) < 10):
        raise invalid("A Speaking Order stating the reasons is required with this outcome.", [Issue("speaking_order", "A Speaking Order is needed.", "speaking_order")])
    if live is not None:
        db.execute(update(CouncilDecision).where(CouncilDecision.id == live.id).values(is_live=False))
        db.flush()
    row = CouncilDecision(
        kind=CouncilKind.APPEAL_DECISION.value, appeal_id=ap.id, outcome=outcome, reasons=reasons.strip(), speaking_order=so, reference=(reference or "").strip()[:300] or None, recorded_by_id=user.id,
        supersedes_id=live.id if live else None, correction_reason=correction_reason.strip() if live else None, policy_ref=cpolicy.ref(case.policy_snapshot), idempotency_key=idempotency_key,
        content_hash=hashlib.sha256(_canonical({"appeal": str(ap.id), "outcome": outcome, "reasons": reasons.strip(), "speaking_order": so}).encode()).hexdigest(),
    )
    db.add(row)
    db.flush()
    if live is not None:
        db.execute(update(CouncilDecision).where(CouncilDecision.id == live.id).values(superseded_by_id=row.id))
    db.execute(update(Appeal).where(Appeal.id == ap.id).values(status=AppealStatus.COUNCIL_DECIDED.value, decided_at=ap.decided_at or utcnow(), row_version=ap.row_version + 1))
    db.refresh(ap)
    dsvc.touch(db, case)
    app = db.get(Application, ap.application_id)
    db.add(ApplicationEvent(application_id=app.id, actor_id=user.id, action="appeal_decided", detail={"outcome": outcome, "recorded_by_a_person": True}))
    dsvc.log(db, case, user, "appeal_decided", request=request, object_type="council_decision", object_id=row.id, reason=reasons, detail={"appeal_id": str(ap.id), "outcome": outcome, "correction": live is not None})
    db.flush()
    dtexts.appeal_decided(db, ap.id, app, outcome)
    sync = portal_sync.record(db, app.id, "council_decision", row.id, "APPEAL_DECIDED")
    if sync.status == "SYNC_PENDING":
        dtexts.sync_pending(db, case.id, app, sync.id)
    return row, False


def documents(db: Session, appeal_id: uuid.UUID) -> list[AppealDocument]:
    return list(db.scalars(select(AppealDocument).where(AppealDocument.appeal_id == appeal_id).order_by(AppealDocument.uploaded_at)))


_ = (DECISION_POLICY_STAGE, get_settings)
