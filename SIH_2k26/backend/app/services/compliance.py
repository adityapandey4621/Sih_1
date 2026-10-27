"""Compliance obligations that follow an issued decision.

  * An obligation is RECORDED by a person holding `compliance:define`, from an issued decision. The software derives no obligation from a decision or a report.
  * The institution SUBMITS evidence. A submission never changes an obligation to verified: only an assigned verifier holding `compliance:verify` can,
    by recording a review. A resubmission after a requested correction is a new submission with new document versions; nothing earlier is overwritten.
  * A NOT_MET outcome is a record. This module revokes nothing, suspends nothing and imposes nothing; any consequence belongs to the competent authority.
  * The due date is one of: computed from a usable policy of the frozen rule version, specified by the authorised person with a citation, or absent.
"""

import secrets
import uuid
from datetime import datetime

from fastapi import Request, UploadFile
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app import audit
from app.config import Settings
from app.domain import (
    COMPLIANCE_ALERT_TRIGGER,
    COMPLIANCE_DEFAULT_TRIGGER,
    OBLIGATION_FINAL,
    OBLIGATION_TRANSITIONS,
    ClockKind,
    DueBasis,
    NotificationEvent,
    ObligationAction,
    ObligationStatus,
    ReviewOutcome,
)
from app.models import User, utcnow
from app.models_applications import Application
from app.models_compliance import Obligation, ObligationDocument, ObligationEvent, ObligationReview, ObligationSubmission
from app.models_decisions import Appeal, CouncilDecision, DecisionCase, EcDecision, OfficialIssuance
from app.rbac import Permission, permissions_for
from app.security import as_utc
from app.services import clock_policy, clock_sources, clocks, notifications, private_files, scheduler
from app.services.applications import config_of
from app.services.errors import Issue, conflict, invalid, not_found
from app.storage import LocalStorage

_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
MAX_FILES = 5
INSTITUTION_FACING = ("obligation_defined", "evidence_submitted", "correction_requested", "obligation_verified", "obligation_not_met", "obligation_withdrawn")


# ---- access ---------------------------------------------------------------------------------------------


def get_obligation(db: Session, user: User, obligation_id: uuid.UUID, request: Request | None, *, write: bool = False) -> Obligation:
    """Institutions see their own institution's obligations; the Regulation Bureau sees all; a compliance officer sees those assigned to them.
    Anyone else, and any other institution, gets "not found" and an audit record."""
    o = db.get(Obligation, obligation_id, populate_existing=True)
    perms = permissions_for(user.role)
    ok = False
    if o is not None:
        if Permission.COMPLIANCE_READ_OWN in perms:
            ok = user.institution_id is not None and o.institution_id == user.institution_id
        elif Permission.COMPLIANCE_QUEUE in perms:
            ok = Permission.COMPLIANCE_DEFINE in perms or o.verifier_id == user.id
    if not ok:
        if o is not None:
            audit.record(db, "compliance.access_denied", request=request, actor=user, target_type="obligation", target_id=o.id)
            db.commit()
        raise not_found("Obligation")
    return o


def _reference(db: Session) -> str:
    while True:
        ref = "OBL-" + "".join(secrets.choice(_ALPHABET) for _ in range(4)) + "-" + "".join(secrets.choice(_ALPHABET) for _ in range(4))
        if db.scalar(select(Obligation.id).where(Obligation.reference == ref)) is None:
            return ref


def log(db: Session, o: Obligation, actor: User | None, action: str, *, from_status: str | None = None, to_status: str | None = None, detail: dict | None = None, institution_visible: bool = True) -> None:
    db.add(ObligationEvent(obligation_id=o.id, actor_id=actor.id if actor else None, action=action, from_status=from_status, to_status=to_status, detail=detail, institution_visible=institution_visible))


def transition(db: Session, o: Obligation, action: ObligationAction, expected_version: int | None = None, **extra) -> ObligationStatus:
    current = ObligationStatus(o.status)
    target = OBLIGATION_TRANSITIONS.get((current, action))
    if target is None:
        raise conflict("invalid_transition", f"That action is not allowed while the obligation is “{current.value.replace('_', ' ').lower()}”.")
    if expected_version is not None and o.row_version != expected_version:
        raise conflict("stale_version", "This obligation was changed by someone else since you opened it. Reload it to see the latest state.")
    now = utcnow()
    res = db.execute(update(Obligation).where(Obligation.id == o.id, Obligation.status == current.value, Obligation.row_version == o.row_version).values(
        status=target.value, row_version=o.row_version + 1, updated_at=now, closed_at=now if target.value in OBLIGATION_FINAL else None, **extra))
    if res.rowcount != 1:
        raise conflict("stale_version", "This obligation was changed by someone else at the same moment. Reload it and try again.")
    db.refresh(o)
    return target


def _notify(db: Session, event: NotificationEvent, o: Obligation, users: list[User], kind: str, title: str, body: str, tag: str) -> None:
    notifications.notify(db, event, users, kind=kind, scope_key=f"obligation:{o.id}:{tag}", title=title, body=body, link="/app/post-approval" if kind == "institution" else f"/app/post-approval/obligations/{o.id}",
                         application_id=o.application_id, obligation_id=o.id)


# ---- recording an obligation ----------------------------------------------------------------------------


def issued_case_for(db: Session, application_id: uuid.UUID, source: str = "EC_DECISION") -> tuple[DecisionCase, Application, EcDecision | None, CouncilDecision | None]:
    """The decision an obligation may follow from. EC_DECISION: the issued Executive Committee decision. COUNCIL_APPEAL_DECISION: the Council's recorded decision on this
    application's appeal (an approval granted on appeal is the Council's, not the Executive Committee's). Nothing is inferred: the person says which."""
    case = db.scalar(select(DecisionCase).where(DecisionCase.application_id == application_id))
    app = db.get(Application, application_id)
    iss = db.scalar(select(OfficialIssuance).where(OfficialIssuance.case_id == case.id, OfficialIssuance.status == "ISSUED")) if case else None
    if case is None or app is None or iss is None or case.state not in ("ISSUED", "CLOSED"):
        raise conflict("no_issued_decision", "Obligations can be recorded only against a decision whose official outcome has been issued to the institution.")
    if source == "COUNCIL_APPEAL_DECISION":
        appeal = db.scalar(select(Appeal).where(Appeal.application_id == application_id))
        council = db.scalar(select(CouncilDecision).where(CouncilDecision.appeal_id == appeal.id, CouncilDecision.kind == "APPEAL_DECISION", CouncilDecision.is_live.is_(True))) if appeal else None
        if council is None:
            raise conflict("no_council_decision", "The Council has not recorded a decision on an appeal for this application.")
        return case, app, None, council
    return case, app, db.get(EcDecision, iss.decision_id), None


def _due(db: Session, settings: Settings, app: Application, due: dict, now: datetime) -> tuple[str, datetime | None, str | None, dict | None, clock_policy.ClockResolution | None]:
    basis = due.get("basis", DueBasis.NONE.value)
    if basis == DueBasis.NONE.value:
        return basis, None, None, None, None
    config = config_of(app)
    if basis == DueBasis.POLICY.value:
        res = next((r for r in clock_policy.resolutions_for(config, settings, COMPLIANCE_DEFAULT_TRIGGER) if r.policy_key == due.get("policy_key")), None)
        if res is None:
            raise invalid("That deadline policy does not exist in this application's configuration.", [Issue("due.policy_key", "No such policy for compliance obligations.", "due.policy_key")])
        if not res.usable:
            raise conflict("policy_not_usable", f"The configured policy cannot establish a due date: {res.message} Record the date the decision specifies, or record the obligation without one.")
        return basis, res.due_from(now), None, res.snapshot(app.rule_version_id), res
    if basis == DueBasis.SPECIFIED.value:
        at, authority = due.get("due_at"), (due.get("authority") or "").strip()
        if at is None or len(authority) < 5:
            raise invalid("A date taken from the decision needs the date and where the decision states it.", [Issue("due.authority", "Say where the decision specifies this date.", "due.authority")])
        at = as_utc(at)
        if at <= now:
            raise invalid("The due date must be in the future.", [Issue("due.due_at", "Choose a future date.", "due.due_at")])
        return basis, at, authority[:300], None, None
    raise invalid("Unknown due-date basis.")


def define(db: Session, user: User, application_id: uuid.UUID, data: dict, key: str, request: Request | None, settings: Settings) -> tuple[Obligation, bool]:
    prior = db.scalar(select(Obligation).where(Obligation.idempotency_key == key))
    if prior is not None:
        if prior.application_id != application_id or prior.defined_by_id != user.id:
            raise conflict("idempotency_key_reused", "That request key was already used for something else.")
        return prior, True
    case, app, decision, council = issued_case_for(db, application_id, data.get("source") or "EC_DECISION")
    verifier = db.get(User, data["verifier_id"])
    if verifier is None or not verifier.is_active or Permission.COMPLIANCE_VERIFY not in permissions_for(verifier.role):
        raise invalid("The chosen verifier cannot verify compliance evidence.", [Issue("verifier_id", "Choose an active compliance officer.", "verifier_id")])
    now = utcnow()
    basis, due_at, authority, due_policy, res = _due(db, settings, app, data.get("due") or {}, now)
    o = Obligation(
        reference=_reference(db), application_id=app.id, institution_id=app.institution_id, decision_case_id=case.id, decision_id=decision.id if decision else None, council_decision_id=council.id if council else None, rule_version_id=app.rule_version_id, title=data["title"],
        condition_text=data["condition_text"], condition_ref=data.get("condition_ref"), required_evidence=data["required_evidence"], verifier_id=verifier.id, due_basis=basis, due_at=due_at, due_authority=authority,
        due_policy=due_policy, defined_by_id=user.id, defined_at=now, idempotency_key=key,
    )
    db.add(o)
    db.flush()
    log(db, o, user, "obligation_defined", to_status=o.status, detail={"source": "EC_DECISION" if decision else "COUNCIL_APPEAL_DECISION", "decision_id": str((decision or council).id), "decision_version": decision.version_number if decision else None, "due_basis": basis, "verifier_id": str(verifier.id)})
    if due_at is not None:
        _make_clock(db, o, app, settings, res, now)
    audit.record(db, "compliance.obligation_defined", request=request, actor=user, target_type="obligation", target_id=o.id, detail={"application_id": str(app.id), "due_basis": basis})
    _notify(db, NotificationEvent.OBLIGATION_DEFINED, o, notifications.institution_users(db, o.institution_id), "institution", f"New compliance obligation: {o.title}",
            f"A compliance obligation ({o.reference}) was recorded for application {app.reference}. Open Compliance to see the condition and the evidence required.", "defined")
    _notify(db, NotificationEvent.OBLIGATION_DEFINED, o, [verifier], "verifier", f"Obligation assigned to you: {o.title}", f"{o.reference} for application {app.reference} is assigned to you for verification.", "assigned")
    return o, False


def _make_clock(db: Session, o: Obligation, app: Application, settings: Settings, res: clock_policy.ClockResolution | None, now: datetime) -> None:
    config = config_of(app)
    if res is not None:
        snap, dtype, pkey = res.snapshot(app.rule_version_id), res.deadline_type if res.deadline_type in ("STATUTORY", "INTERNAL_SLA") else "INTERNAL_SLA", res.policy_key
        label = f"Compliance: {o.title}"
    else:
        extras, ref = clock_policy.alert_policy(config, settings, COMPLIANCE_ALERT_TRIGGER)
        snap = {"policy_key": "obligation-due", "label": "Due date specified in the decision", "deadline_type": "SPECIFIED", "trigger_event": "compliance_due_specified", "state": "SPECIFIED",
                "message": "A date stated by an authorised person, with the citation shown.", "authority": o.due_authority, "alert_policy": ref, "rule_version_id": str(app.rule_version_id), **extras.as_dict()}
        dtype, pkey, label = "SPECIFIED", "obligation-due", f"Compliance: {o.title}"
    clock, _ = clocks.create_clock(
        db, kind=ClockKind.COMPLIANCE.value, deadline_type=dtype, subject_type="obligation", subject_id=o.id, policy_key=pkey, label=label, app=app, trigger_event=snap["trigger_event"], trigger_at=now,
        due_at=o.due_at, snapshot=snap, responsible_role="compliance_officer",
    )
    scheduler.plan_alerts(db, clock, settings)


# ---- the institution submits evidence ---------------------------------------------------------------------


def submit_evidence(
    db: Session, user: User, obligation_id: uuid.UUID, note: str, uploads: list[tuple[str, UploadFile]], key: str, expected_version: int, request: Request | None, settings: Settings, storage: LocalStorage,
) -> tuple[Obligation, bool]:
    o = get_obligation(db, user, obligation_id, request)
    if Permission.COMPLIANCE_SUBMIT not in permissions_for(user.role):
        raise not_found("Obligation")
    prior = db.scalar(select(ObligationSubmission).where(ObligationSubmission.idempotency_key == key))
    if prior is not None:
        if prior.obligation_id != o.id:
            raise conflict("idempotency_key_reused", "That request key was already used for something else.")
        return o, True
    if o.status not in (ObligationStatus.AWAITING_EVIDENCE.value, ObligationStatus.CORRECTION_REQUESTED.value):
        why = "evidence is already with the verifier" if o.status == ObligationStatus.EVIDENCE_SUBMITTED.value else "it is closed"
        raise conflict("invalid_transition", f"Evidence cannot be submitted now: {why}.")
    if not uploads or len(uploads) > MAX_FILES:
        raise invalid(f"Attach between 1 and {MAX_FILES} files.", [Issue("files", f"Attach between 1 and {MAX_FILES} files.", "files")])
    if len({label.strip().lower() for label, _ in uploads}) != len(uploads):
        raise invalid("Each file needs its own label.", [Issue("labels", "Give each file a different label.", "labels")])
    stored: list[tuple[str, private_files.StoredFile]] = []
    try:
        for label, up in uploads:
            stored.append((label.strip()[:120] or "Evidence", private_files.store(up, settings, storage)))
        seq = (db.scalar(select(func.max(ObligationSubmission.sequence)).where(ObligationSubmission.obligation_id == o.id)) or 0) + 1
        before = o.status
        transition(db, o, ObligationAction.SUBMIT, expected_version)
        sub = ObligationSubmission(obligation_id=o.id, sequence=seq, note=note.strip(), submitted_by_id=user.id, idempotency_key=key)
        db.add(sub)
        db.flush()
        for label, f in stored:
            ver = (db.scalar(select(func.max(ObligationDocument.version_number)).where(ObligationDocument.obligation_id == o.id, ObligationDocument.label == label)) or 0) + 1
            db.add(ObligationDocument(submission_id=sub.id, obligation_id=o.id, label=label, version_number=ver, storage_key=f.key, original_filename=f.name, media_type=f.media_type, size_bytes=f.size, sha256=f.sha256, scan_status=f.scan_status))
        log(db, o, user, "evidence_submitted", from_status=before, to_status=o.status, detail={"submission": seq, "files": len(stored)})
        audit.record(db, "compliance.evidence_submitted", request=request, actor=user, target_type="obligation", target_id=o.id, detail={"submission": seq, "files": len(stored)})
        _notify(db, NotificationEvent.EVIDENCE_SUBMITTED, o, [o.verifier], "verifier", f"Evidence to verify: {o.title}", f"The institution submitted evidence for {o.reference} (submission {seq}). It is NOT verified until you record a review.", f"submitted:{seq}")
        return o, False
    except BaseException:
        for _, f in stored:
            storage.delete(f.key)
        raise


# ---- the verifier records an outcome ----------------------------------------------------------------------


def review(db: Session, user: User, obligation_id: uuid.UUID, outcome: ReviewOutcome, submission_id: uuid.UUID, reason: str, internal_note: str | None, key: str, expected_version: int, request: Request | None) -> tuple[Obligation, bool]:
    o = get_obligation(db, user, obligation_id, request)
    if Permission.COMPLIANCE_VERIFY not in permissions_for(user.role) or o.verifier_id != user.id:
        audit.record(db, "compliance.access_denied", request=request, actor=user, target_type="obligation", target_id=o.id, detail={"attempt": "review"})
        db.commit()
        raise not_found("Obligation")
    prior = db.scalar(select(ObligationReview).where(ObligationReview.idempotency_key == key))
    if prior is not None:
        if prior.obligation_id != o.id:
            raise conflict("idempotency_key_reused", "That request key was already used for something else.")
        return o, True
    action = {ReviewOutcome.VERIFIED: ObligationAction.VERIFY, ReviewOutcome.CORRECTION_REQUESTED: ObligationAction.REQUEST_CORRECTION, ReviewOutcome.NOT_MET: ObligationAction.RECORD_NOT_MET}[outcome]
    latest = db.scalar(select(ObligationSubmission).where(ObligationSubmission.obligation_id == o.id).order_by(ObligationSubmission.sequence.desc()))
    if latest is None or latest.id != submission_id:
        raise conflict("stale_submission", "A newer submission exists (or this one is not the latest). Reload and review the latest evidence.")
    if db.scalar(select(ObligationReview.id).where(ObligationReview.submission_id == latest.id)) is not None:
        raise conflict("already_reviewed", "This submission already has a recorded review.")
    before = o.status
    transition(db, o, action, expected_version)
    db.add(ObligationReview(obligation_id=o.id, submission_id=latest.id, outcome=outcome.value, reason=reason.strip(), internal_note=(internal_note or "").strip() or None, reviewed_by_id=user.id, idempotency_key=key))
    log(db, o, user, f"review_{outcome.value.lower()}", from_status=before, to_status=o.status, detail={"submission": latest.sequence})
    audit.record(db, "compliance.reviewed", request=request, actor=user, target_type="obligation", target_id=o.id, detail={"outcome": outcome.value, "submission": latest.sequence})
    app = db.get(Application, o.application_id)
    inst = notifications.institution_users(db, o.institution_id)
    words = {
        ReviewOutcome.VERIFIED: (NotificationEvent.OBLIGATION_VERIFIED, "Compliance verified", "The verifier recorded that the evidence satisfies the condition."),
        ReviewOutcome.CORRECTION_REQUESTED: (NotificationEvent.CORRECTION_REQUESTED, "Correction requested", "The verifier asked for a correction. Open Compliance to see what is needed."),
        ReviewOutcome.NOT_MET: (NotificationEvent.OBLIGATION_NOT_MET, "Outcome recorded: not met", "The verifier recorded that the evidence does not satisfy the condition. Any consequence is for the competent authority; this system takes none."),
    }[outcome]
    _notify(db, words[0], o, inst, "institution", f"{words[1]}: {o.title}", f"{o.reference} for application {app.reference}. {words[2]}", f"review:{latest.sequence}")
    clock_sources.refresh(db)  # a verified / not-met obligation finishes its clock, which cancels its pending reminders
    return o, False


def withdraw(db: Session, user: User, obligation_id: uuid.UUID, reason: str, expected_version: int, request: Request | None) -> Obligation:
    o = get_obligation(db, user, obligation_id, request)
    if Permission.COMPLIANCE_DEFINE not in permissions_for(user.role):
        raise not_found("Obligation")
    before = o.status
    transition(db, o, ObligationAction.WITHDRAW, expected_version, withdrawn_reason=reason.strip())
    log(db, o, user, "obligation_withdrawn", from_status=before, to_status=o.status, detail={"reason": reason.strip()})
    audit.record(db, "compliance.obligation_withdrawn", request=request, actor=user, target_type="obligation", target_id=o.id)
    app = db.get(Application, o.application_id)
    _notify(db, NotificationEvent.OBLIGATION_DEFINED, o, notifications.institution_users(db, o.institution_id), "institution", f"Obligation withdrawn: {o.title}", f"{o.reference} for application {app.reference} was withdrawn. Nothing further is required for it.", "withdrawn")
    clock_sources.refresh(db)
    return o


def reassign(db: Session, user: User, obligation_id: uuid.UUID, verifier_id: uuid.UUID, reason: str, request: Request | None) -> Obligation:
    o = get_obligation(db, user, obligation_id, request)
    if Permission.COMPLIANCE_DEFINE not in permissions_for(user.role):
        raise not_found("Obligation")
    verifier = db.get(User, verifier_id)
    if o.status in OBLIGATION_FINAL:
        raise conflict("invalid_transition", "A closed obligation's verifier cannot be changed.")
    if verifier is None or not verifier.is_active or Permission.COMPLIANCE_VERIFY not in permissions_for(verifier.role):
        raise invalid("The chosen verifier cannot verify compliance evidence.", [Issue("verifier_id", "Choose an active compliance officer.", "verifier_id")])
    old = o.verifier_id
    o.verifier_id = verifier.id
    log(db, o, user, "verifier_reassigned", detail={"from": str(old), "to": str(verifier.id), "reason": reason.strip()}, institution_visible=False)
    audit.record(db, "compliance.verifier_reassigned", request=request, actor=user, target_type="obligation", target_id=o.id)
    app = db.get(Application, o.application_id)
    _notify(db, NotificationEvent.OBLIGATION_DEFINED, o, [verifier], "verifier", f"Obligation assigned to you: {o.title}", f"{o.reference} for application {app.reference} is assigned to you for verification.", f"assigned:{verifier.id}")
    return o


def document_for_download(db: Session, user: User, document_id: uuid.UUID, request: Request | None) -> ObligationDocument:
    doc = db.get(ObligationDocument, document_id)
    if doc is None:
        raise not_found("Document")
    get_obligation(db, user, doc.obligation_id, request)  # same scope as the obligation itself
    audit.record(db, "compliance.document_downloaded", request=request, actor=user, target_type="obligation_document", target_id=doc.id)
    db.commit()
    return doc
