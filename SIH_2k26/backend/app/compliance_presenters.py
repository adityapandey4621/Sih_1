"""Shape obligations, validity items and clocks for the API.

Officials see the whole record of what they are permitted to see. The institution sees its own obligations: the condition, the evidence required, its own
submissions, the verifier's stated reasons, the deadline and what stage it is at - never a verifier's internal note, never internal service levels.
Dates are always returned as recorded; "overdue" and "due soon" are computed at the moment of the request and never stored.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.domain import CLOCK_LIVE, INSTITUTION_VISIBLE_CLOCKS, ClockKind
from app.models import User
from app.models_applications import Application
from app.models_clocks import Clock, ClockEvent, ClockSegment, ScheduledWork
from app.models_compliance import Obligation, ObligationEvent, ObligationReview, ValidityItem, ValidityVersion
from app.models_decisions import CouncilDecision, EcDecision
from app.rbac import Permission, permissions_for
from app.services import clocks
from app.services.decision_texts import LABEL

NOT_A_PENALTY = "Recording an obligation, a submission or a verification outcome revokes nothing and imposes nothing. Any consequence is decided by the competent authority outside this system."
UPLOAD_IS_NOT_VERIFICATION = "Submitting evidence is not verification. An obligation is verified only when the assigned verifier records that outcome."
ROLE_CLOCK_KINDS: dict[str, tuple[str, ...]] = {
    "regulation_bureau": tuple(k.value for k in ClockKind), "scrutiny_coordinator": (ClockKind.OFFICER_STAGE.value, ClockKind.INSTITUTION_RESPONSE.value),
    "evc_coordinator": (ClockKind.EVC.value,), "sac_coordinator": (ClockKind.APPEAL.value,), "compliance_officer": (ClockKind.COMPLIANCE.value, ClockKind.DOCUMENT_VALIDITY.value),
}
STATUS_LABELS = {
    "AWAITING_EVIDENCE": "Awaiting your evidence", "EVIDENCE_SUBMITTED": "Evidence submitted — awaiting verification", "CORRECTION_REQUESTED": "Correction requested",
    "VERIFIED": "Verified", "NOT_MET": "Outcome recorded: not met", "WITHDRAWN": "Withdrawn",
}


def _utc(dt: datetime | None) -> datetime | None:
    return dt.replace(tzinfo=UTC) if dt is not None and dt.tzinfo is None else dt


def _app(a: Application) -> dict:
    return {"id": a.id, "reference": a.reference, "institution_name": a.institution.name, "category_name": a.category.name, "academic_year": a.academic_year}


# ---- clocks ----------------------------------------------------------------------------------------------------


def visible_kinds(user: User) -> tuple[str, ...]:
    perms = permissions_for(user.role)
    if Permission.COMPLIANCE_READ_OWN in perms:
        return INSTITUTION_VISIBLE_CLOCKS
    if Permission.CLOCK_READ in perms:
        return ROLE_CLOCK_KINDS.get(user.role, ())
    return ()


def clock_out(db: Session, c: Clock, settings: Settings, now: datetime, *, official: bool, detail: bool = False) -> dict:
    snap = c.policy_snapshot or {}
    due = clocks.effective_due(c)
    app = db.get(Application, c.application_id)
    out = {
        "id": c.id, "kind": c.kind, "deadline_type": c.deadline_type, "label": c.label, "status": c.status, "timing": clocks.timing(c, now, settings), "policy_key": c.policy_key,
        "trigger_event": c.trigger_event, "trigger_at": c.trigger_at, "due_at": c.due_at, "effective_due_at": due, "paused_seconds": c.paused_seconds, "not_established_reason": c.not_established_reason,
        "application": {"id": app.id, "reference": app.reference, "institution_name": app.institution.name}, "completed_at": c.completed_at, "completion_reason": c.completion_reason, "row_version": c.row_version,
        "subject_type": c.subject_type, "subject_id": c.subject_id,
        "policy": {"state": snap.get("state"), "message": snap.get("message"), "source": snap.get("source"), "demo": snap.get("demo"), "clause_ref": snap.get("clause_ref"), "duration_value": snap.get("duration_value"),
                   "duration_unit": snap.get("duration_unit"), "calendar_convention": snap.get("calendar_convention"), "authority": snap.get("authority"), "note": snap.get("note"), "verified_date": snap.get("verified_date")},
    }
    if official:
        out["responsible_role"] = c.responsible_role
        out["rule_version_id"] = c.rule_version_id
        out["alerts"] = snap.get("alerts")
        out["pause"] = {"allowed": bool((snap.get("pause") or {}).get("allowed")), "reasons": (snap.get("pause") or {}).get("reasons") or []}
        out["escalate_to"] = snap.get("escalate_to") or []
    if detail and official:
        out["segments"] = [{"id": s.id, "paused_at": s.paused_at, "resumed_at": s.resumed_at, "reason": s.reason, "reference": s.reference, "permitted_by": s.permitted_by} for s in db.scalars(select(ClockSegment).where(ClockSegment.clock_id == c.id).order_by(ClockSegment.paused_at))]
        out["events"] = [{"at": e.at, "action": e.action, "from_status": e.from_status, "to_status": e.to_status, "detail": e.detail} for e in db.scalars(select(ClockEvent).where(ClockEvent.clock_id == c.id).order_by(ClockEvent.at))]
        out["alert_work"] = [{"id": w.id, "alert_kind": w.alert_kind, "offset_hours": w.offset_hours, "run_at": w.run_at, "status": w.status, "attempts": w.attempts, "max_attempts": w.max_attempts, "outcome": w.outcome,
                              "last_error": w.last_error, "delivery": w.delivery, "recipients": len(w.recipients or []), "processed_at": w.processed_at, "policy": w.policy_ref}
                             for w in db.scalars(select(ScheduledWork).where(ScheduledWork.clock_id == c.id).order_by(ScheduledWork.run_at, ScheduledWork.offset_hours))]
    return out


def list_clocks(db: Session, user: User, settings: Settings, *, kind: str | None, status: str | None, due: str | None, q: str | None, page: int, page_size: int) -> dict:
    kinds = visible_kinds(user)
    now = datetime.now(UTC)
    base = select(Clock).join(Application, Application.id == Clock.application_id).where(Clock.kind.in_(kinds)) if kinds else select(Clock).where(Clock.id.is_(None))
    if Permission.COMPLIANCE_READ_OWN in permissions_for(user.role):
        base = base.where(Clock.institution_id == user.institution_id)
    if kind:
        base = base.where(Clock.kind == kind)
    if status:
        base = base.where(Clock.status == status)
    if q:
        base = base.where(Application.reference.ilike(f"%{q.strip()}%") | Clock.label.ilike(f"%{q.strip()}%"))
    live = Clock.status.in_(CLOCK_LIVE)
    # paused clocks have no fixed effective date; "overdue" and "soon" are judged on the original date plus approved pause time, in Python below for exactness
    rows = db.scalars(base.order_by(Clock.due_at.is_(None), Clock.due_at, Clock.created_at)).all()
    if due in ("overdue", "soon", "ontrack"):
        keep = {"overdue": ("OVERDUE",), "soon": ("DUE_SOON",), "ontrack": ("ON_TRACK",)}[due]
        rows = [c for c in rows if clocks.timing(c, now, settings) in keep]
    total = len(rows)
    page_rows = rows[(page - 1) * page_size : page * page_size]
    official = Permission.COMPLIANCE_READ_OWN not in permissions_for(user.role)
    return {"items": [clock_out(db, c, settings, now, official=official) for c in page_rows], "total": total, "page": page, "page_size": page_size, "counts": clock_counts(db, kinds, user, settings, now), "_live": bool(live is not None)}


def clock_counts(db: Session, kinds: tuple[str, ...], user: User, settings: Settings, now: datetime) -> dict:
    if not kinds:
        return {"overdue": 0, "soon": 0, "ontrack": 0, "paused": 0, "not_established": 0}
    q = select(Clock).where(Clock.kind.in_(kinds))
    if Permission.COMPLIANCE_READ_OWN in permissions_for(user.role):
        q = q.where(Clock.institution_id == user.institution_id)
    out = {"overdue": 0, "soon": 0, "ontrack": 0, "paused": 0, "not_established": 0}
    for c in db.scalars(q).all():
        t = clocks.timing(c, now, settings)
        key = {"OVERDUE": "overdue", "DUE_SOON": "soon", "ON_TRACK": "ontrack", "PAUSED": "paused", "NOT_ESTABLISHED": "not_established"}.get(t)
        if key:
            out[key] += 1
    return out


# ---- obligations -------------------------------------------------------------------------------------------------


def _doc_out(d) -> dict:
    return {"id": d.id, "label": d.label, "version_number": d.version_number, "filename": d.original_filename, "size_bytes": d.size_bytes, "sha256": d.sha256, "media_type": d.media_type,
            "scan_status": d.scan_status, "uploaded_at": d.uploaded_at, "download": f"/api/post-approval/documents/{d.id}/download"}


def obligation_clock(db: Session, o: Obligation) -> Clock | None:
    return db.scalar(select(Clock).where(Clock.subject_type == "obligation", Clock.subject_id == o.id).order_by(Clock.created_at))


def obligation_summary(db: Session, o: Obligation, settings: Settings, now: datetime, *, official: bool) -> dict:
    c = obligation_clock(db, o)
    app = db.get(Application, o.application_id)
    return {
        "id": o.id, "reference": o.reference, "title": o.title, "status": o.status, "status_label": STATUS_LABELS[o.status], "application": _app(app), "due_basis": o.due_basis, "due_at": o.due_at,
        "effective_due_at": clocks.effective_due(c) if c else o.due_at, "timing": clocks.timing(c, now, settings) if c else ("NO_DATE" if o.due_at is None else "UNKNOWN"), "verifier": o.verifier.full_name if official else None,
        "defined_at": o.defined_at, "closed_at": o.closed_at, "submissions": len(o.submissions), "row_version": o.row_version,
    }


def obligation_detail(db: Session, o: Obligation, settings: Settings, viewer: User, now: datetime) -> dict:
    perms = permissions_for(viewer.role)
    official = Permission.COMPLIANCE_READ_OWN not in perms
    app = db.get(Application, o.application_id)
    dec = db.get(EcDecision, o.decision_id) if o.decision_id else None
    council = db.get(CouncilDecision, o.council_decision_id) if o.council_decision_id else None
    reviews = {r.submission_id: r for r in db.scalars(select(ObligationReview).where(ObligationReview.obligation_id == o.id))}
    subs = []
    for s in o.submissions:
        r = reviews.get(s.id)
        rv = None
        if r is not None:
            rv = {"outcome": r.outcome, "reason": r.reason, "reviewed_at": r.reviewed_at, "reviewed_by": r.reviewed_by.full_name if official else "The assigned verifier"}
            if official:
                rv["internal_note"] = r.internal_note
        subs.append({"id": s.id, "sequence": s.sequence, "note": s.note, "submitted_at": s.submitted_at, "submitted_by": s.submitted_by.full_name, "documents": [_doc_out(d) for d in s.documents], "review": rv})
    events = db.scalars(select(ObligationEvent).where(ObligationEvent.obligation_id == o.id).order_by(ObligationEvent.at)).all()
    if not official:
        events = [e for e in events if e.institution_visible]
    c = obligation_clock(db, o)
    latest = o.submissions[-1] if o.submissions else None
    is_verifier = official and Permission.COMPLIANCE_VERIFY in perms and o.verifier_id == viewer.id
    out = {
        **obligation_summary(db, o, settings, now, official=official),
        "condition_text": o.condition_text, "condition_ref": o.condition_ref, "required_evidence": o.required_evidence,
        "decision": ({"id": dec.id, "source": "EC_DECISION", "version_number": dec.version_number, "outcome": dec.outcome, "outcome_label": LABEL.get(dec.outcome, dec.outcome), "decided_at": dec.decided_at}
                     if dec else {"id": council.id, "source": "COUNCIL_APPEAL_DECISION", "version_number": None, "outcome": council.outcome, "outcome_label": f"Council decision on appeal: {LABEL.get(council.outcome, council.outcome)}", "decided_at": council.recorded_at}),
        "due": {"basis": o.due_basis, "due_at": o.due_at, "authority": o.due_authority, "policy": (o.due_policy or {}) if official else {k: (o.due_policy or {}).get(k) for k in ("message", "demo", "clause_ref", "duration_value", "duration_unit")}},
        "clock": clock_out(db, c, settings, now, official=official) if c else None, "submissions_detail": subs,
        "events": [{"at": e.at, "action": e.action, "from_status": e.from_status, "to_status": e.to_status, "actor": (e.actor.full_name if e.actor else None) if official else None, "detail": e.detail if official else None} for e in events],
        "withdrawn_reason": o.withdrawn_reason, "notes": {"upload": UPLOAD_IS_NOT_VERIFICATION, "penalty": NOT_A_PENALTY},
        "can": {
            "submit": (not official) and Permission.COMPLIANCE_SUBMIT in perms and o.status in ("AWAITING_EVIDENCE", "CORRECTION_REQUESTED"),
            "review": is_verifier and o.status == "EVIDENCE_SUBMITTED", "withdraw": official and Permission.COMPLIANCE_DEFINE in perms and o.status in ("AWAITING_EVIDENCE", "CORRECTION_REQUESTED", "EVIDENCE_SUBMITTED"),
            "reassign": official and Permission.COMPLIANCE_DEFINE in perms and o.status in ("AWAITING_EVIDENCE", "CORRECTION_REQUESTED", "EVIDENCE_SUBMITTED"),
        },
        "latest_submission_id": latest.id if latest else None,
    }
    if official:
        out["application_rule_version_id"] = o.rule_version_id
        out["defined_by"] = o.defined_by.full_name
        out["verifier_id"] = o.verifier_id
    return out


def list_obligations(db: Session, user: User, settings: Settings, *, status: str | None, q: str | None, due: str | None, page: int, page_size: int) -> dict:
    perms = permissions_for(user.role)
    now = datetime.now(UTC)
    base = select(Obligation).join(Application, Application.id == Obligation.application_id)
    official = Permission.COMPLIANCE_READ_OWN not in perms
    if not official:
        base = base.where(Obligation.institution_id == user.institution_id)
    elif Permission.COMPLIANCE_DEFINE not in perms:
        base = base.where(Obligation.verifier_id == user.id)
    if status:
        base = base.where(Obligation.status == status)
    if q:
        like = f"%{q.strip()}%"
        base = base.where(or_(Application.reference.ilike(like), Obligation.reference.ilike(like), Obligation.title.ilike(like)))
    rows = db.scalars(base.order_by(Obligation.due_at.is_(None), Obligation.due_at, Obligation.defined_at.desc())).all()
    items = [obligation_summary(db, o, settings, now, official=official) for o in rows]
    if due:
        items = [i for i in items if i["timing"] == {"overdue": "OVERDUE", "soon": "DUE_SOON", "ontrack": "ON_TRACK"}.get(due)]
    total = len(items)
    return {"items": items[(page - 1) * page_size : page * page_size], "total": total, "page": page, "page_size": page_size,
            "counts": {s: sum(1 for o in rows if o.status == s) for s in ("AWAITING_EVIDENCE", "EVIDENCE_SUBMITTED", "CORRECTION_REQUESTED", "VERIFIED", "NOT_MET", "WITHDRAWN")}}


# ---- validity -------------------------------------------------------------------------------------------------------


def _version_out(db: Session, v: ValidityVersion, official: bool) -> dict:
    out = {
        "id": v.id, "version_number": v.version_number, "source": v.source, "status": v.status, "value_date": v.value_date, "raw_value": v.raw_value if official else None, "verified_date": v.verified_date,
        "verified_at": v.verified_at, "needs_review_reason": v.review_reason_flag, "review_reason": v.review_reason if v.status in ("REJECTED", "VERIFIED") else None, "created_at": v.created_at,
        "provenance": {"document_id": v.source_document_id, "document_version": v.source_document_version, "page": v.source_page, "method": v.source_method, "confidence": v.source_confidence, "evidence": v.source_evidence} if v.source == "EXTRACTED" else None,
        "file": {"filename": v.original_filename, "size_bytes": v.size_bytes, "sha256": v.sha256, "scan_status": v.scan_status, "download": f"/api/post-approval/validity/versions/{v.id}/download"} if v.storage_key else None,
        "conflict": v.conflict if official else None,
        "clock": None,
    }
    if v.source == "EXTRACTED" and v.source_document_id:
        out["source_download"] = f"/api/post-approval/validity/versions/{v.id}/download" if official else None
    if official:
        out["internal_note"] = v.internal_note
        out["verified_by"] = db.get(User, v.verified_by_id).full_name if v.verified_by_id else None
    return out


def validity_item_out(db: Session, it: ValidityItem, settings: Settings, now: datetime, *, official: bool) -> dict:
    app = db.get(Application, it.application_id)
    versions = list(it.versions)
    clock_by_version = {c.subject_id: c for c in db.scalars(select(Clock).where(Clock.subject_type == "validity_version", Clock.subject_id.in_([v.id for v in versions]))).all()} if versions else {}
    vs = []
    for v in versions:
        d = _version_out(db, v, official)
        c = clock_by_version.get(v.id)
        d["clock"] = clock_out(db, c, settings, now, official=official) if c else None
        vs.append(d)
    verified = next((v for v in reversed(versions) if v.status == "VERIFIED"), None)
    pending = [v for v in versions if v.status in ("UNVERIFIED", "NEEDS_REVIEW")]
    c = clock_by_version.get(verified.id) if verified else None
    return {
        "id": it.id, "label": it.label, "status": it.status, "application": _app(app), "versions": vs, "resolved_at": it.resolved_at, "resolution_reason": it.resolution_reason if official or it.status == "RESOLVED" else None,
        "current_verified_date": verified.verified_date if verified else None, "expiry_timing": clocks.timing(c, now, settings) if c else None, "effective_due_at": clocks.effective_due(c) if c else None,
        "awaiting_review": len(pending), "can": {"renew": (not official) and it.status == "ACTIVE" and not any(v.source == "DECLARED" for v in pending), "review": official and Permission.COMPLIANCE_VERIFY in permissions_for_user(db, official)},
        "note": "Only a date a person has confirmed against the document produces an expiry alert. A renewal does not end the earlier date's alerts until it is verified.",
    }


def permissions_for_user(db: Session, _official: bool) -> frozenset:  # pragma: no cover - overridden per request by the router
    return frozenset()


def list_validity(db: Session, user: User, settings: Settings, *, status: str | None, q: str | None, page: int, page_size: int) -> dict:
    perms = permissions_for(user.role)
    now = datetime.now(UTC)
    official = Permission.COMPLIANCE_READ_OWN not in perms
    base = select(ValidityItem).join(Application, Application.id == ValidityItem.application_id)
    if not official:
        base = base.where(ValidityItem.institution_id == user.institution_id)
    if q:
        base = base.where(Application.reference.ilike(f"%{q.strip()}%") | ValidityItem.label.ilike(f"%{q.strip()}%"))
    items = db.scalars(base.order_by(ValidityItem.created_at.desc())).all()
    out = []
    for it in items:
        d = validity_item_out(db, it, settings, now, official=official)
        d["can"]["review"] = official and Permission.COMPLIANCE_VERIFY in perms
        out.append(d)
    if status == "needs_review":
        out = [d for d in out if d["awaiting_review"] and d["status"] == "ACTIVE"]
    elif status == "expiring":
        out = [d for d in out if d["expiry_timing"] in ("DUE_SOON", "OVERDUE")]
    elif status == "resolved":
        out = [d for d in out if d["status"] == "RESOLVED"]
    total = len(out)
    return {"items": out[(page - 1) * page_size : page * page_size], "total": total, "page": page, "page_size": page_size}


def institution_overview(db: Session, user: User, settings: Settings) -> dict:
    now = datetime.now(UTC)
    obligations = list_obligations(db, user, settings, status=None, q=None, due=None, page=1, page_size=100)
    validity = list_validity(db, user, settings, status=None, q=None, page=1, page_size=100)
    cl = list_clocks(db, user, settings, kind=None, status=None, due=None, q=None, page=1, page_size=100)
    cl.pop("_live", None)
    return {"obligations": obligations, "validity": validity, "clocks": cl, "notes": {"upload": UPLOAD_IS_NOT_VERIFICATION, "penalty": NOT_A_PENALTY}, "generated_at": now}


_ = (and_, func, uuid)
