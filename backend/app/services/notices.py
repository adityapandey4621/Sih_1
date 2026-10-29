"""Formal deficiency notices: draft -> in review -> issued, and nothing else.

Only an authorised person (`notice:issue`) issues a notice, only after inspecting its exact content (they must
echo that content's hash), and only in one transaction that records the notice, its frozen content, the
authority it was issued under, the response-period resolution, the findings it carries, the case state and the
audit trail. If anything fails, nothing is issued. Internal reviewer text never enters a notice: items are built
from the institution-facing fields of a finding, which the reviewer wrote for that purpose.
"""

import hashlib
import json
import secrets
import uuid
from dataclasses import dataclass, field

from fastapi import Request
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app import audit, scrutiny_access
from app.config import Settings
from app.domain import (
    DEMO_BANNER,
    NOTICE_PREPARING,
    NOTICE_TRANSITIONS,
    CaseAction,
    FindingState,
    GapReason,
    NoticeStatus,
    RoundKind,
    RoundStatus,
    SourceRefStatus,
    Visibility,
)
from app.models import User, utcnow
from app.models_applications import Application, ApplicationDocument, ApplicationEvent
from app.models_scrutiny import DeficiencyNotice, FindingEvent, NoticeItem, ScrutinyCase, ScrutinyFinding, ScrutinyRound
from app.rbac import ROLE_LABELS, Permission, Role
from app.services import policy, rules
from app.services import scrutiny as sc
from app.services import scrutiny_texts as texts
from app.services.applications import config_of
from app.services.errors import Issue, ServiceError, conflict, invalid, not_found

_ALPHABET = "ABCDEFGHJKMNPQRSTVWXYZ23456789"

# The only template that exists. It is a demonstration template: no approved AICTE template, signature, seal or legal
# wording has been provided, so none is invented. Non-demo cases therefore cannot issue a notice yet.
DEMO_TEMPLATE = {"key": "demo-notice-v1", "label": "DEMO ONLY — demonstration template (no official wording, signature or seal)", "demo": True}


@dataclass
class Blocker:
    code: str
    message: str


@dataclass
class Preview:
    content: dict
    content_hash: str
    policy: policy.PolicyResolution
    blockers: list[Blocker] = field(default_factory=list)  # prevent issuance
    unsourced: list[int] = field(default_factory=list)  # item positions without an established source (issuer must acknowledge)

    @property
    def can_issue(self) -> bool:
        return not self.blockers


# ---- helpers ------------------------------------------------------------------------------------


def _reference(db: Session) -> str:
    for _ in range(8):
        ref = "DN-" + "".join(secrets.choice(_ALPHABET) for _ in range(4)) + "-" + "".join(secrets.choice(_ALPHABET) for _ in range(4))
        if not db.scalar(select(DeficiencyNotice.id).where(DeficiencyNotice.reference == ref)):
            return ref
    raise ServiceError(500, "reference_unavailable", "A notice reference could not be generated. Please try again.")


def load_notice(db: Session, notice_id: uuid.UUID) -> DeficiencyNotice:
    n = db.scalars(select(DeficiencyNotice).where(DeficiencyNotice.id == notice_id).options(selectinload(DeficiencyNotice.items)).execution_options(populate_existing=True)).one_or_none()
    if n is None:
        raise not_found("Notice")
    return n


def notice_for_staff(db: Session, user: User, notice_id: uuid.UUID, request: Request | None, *, need: str) -> tuple[DeficiencyNotice, ScrutinyCase]:
    n = load_notice(db, notice_id)
    case, _ = scrutiny_access.get_case(db, user, n.case_id, request, need=need)
    return n, case


def _set_status(db: Session, n: DeficiencyNotice, to: NoticeStatus, *, expected_version: int | None = None, **extra) -> None:
    """The single place a notice changes status: compare-and-set against NOTICE_TRANSITIONS."""
    cur = NoticeStatus(n.status)
    if to not in NOTICE_TRANSITIONS[cur]:
        raise conflict("invalid_transition", f"A notice that is “{cur.value}” cannot become “{to.value}”.")
    cond = [DeficiencyNotice.id == n.id, DeficiencyNotice.status == cur.value, DeficiencyNotice.row_version == (expected_version if expected_version is not None else n.row_version)]
    res = db.execute(update(DeficiencyNotice).where(*cond).values(status=to.value, row_version=n.row_version + 1, updated_at=utcnow(), **extra))
    if res.rowcount != 1:
        raise conflict("stale_version", "This notice was changed by someone else at the same moment. Reload it before continuing.")
    db.refresh(n)


def _round_kind(db: Session, case: ScrutinyCase) -> str:
    rnd = sc.open_round(db, case.id)
    return rnd.kind if rnd else RoundKind.INITIAL.value


def _return_action(db: Session, case: ScrutinyCase) -> CaseAction:
    return CaseAction.RETURN_NOTICE_RESCRUTINY if _round_kind(db, case) == RoundKind.RE_SCRUTINY.value else CaseAction.RETURN_NOTICE


def _item_from_finding(db: Session, f: ScrutinyFinding, config: dict, position: int) -> NoticeItem:
    label = next((i["label"] for i in config["checklist"] if i["item_key"] == f.item_key), None)
    doc = db.get(ApplicationDocument, f.document_id) if f.document_id else None
    return NoticeItem(
        position=position, finding_id=f.id, item_key=f.item_key, document_id=f.document_id, document_version=doc.version_number if doc else None, page_number=f.page_number,
        title=(label or sc.ISSUE_TYPES.get(f.issue_type, f.issue_type))[:300], description=f.institution_text or "", requested_correction=f.requested_correction or "",
        source_ref=f.source_ref, source_ref_status=f.source_ref_status,
    )


# ---- drafting -----------------------------------------------------------------------------------


def create_draft(db: Session, user: User, case_id: uuid.UUID, finding_ids: list[uuid.UUID], summary: str, request: Request | None) -> DeficiencyNotice:
    case = sc._require_reviewer(db, user, case_id, request)
    sc._require_working(case)
    if not finding_ids:
        raise invalid("Choose at least one finding to include.")
    if len(set(finding_ids)) != len(finding_ids):
        raise invalid("A finding can be included once.")
    app = db.get(Application, case.application_id)
    config = config_of(app)
    if db.scalar(select(DeficiencyNotice.id).where(DeficiencyNotice.case_id == case.id, DeficiencyNotice.status.in_(NOTICE_PREPARING)).limit(1)) is not None:
        raise conflict("open_draft_exists", "A notice is already being prepared for this case. Finish or withdraw it first.")
    findings = [sc.get_finding(db, case, fid) for fid in finding_ids]
    issues = []
    for f in findings:
        if f.state not in (FindingState.OPEN.value, FindingState.UNRESOLVED.value):
            issues.append(Issue("finding_state", f"Finding {f.sequence} is “{f.state.lower()}” and cannot go into a notice.", str(f.id)))
        elif f.visibility != Visibility.ELIGIBLE_FOR_NOTICE.value:
            issues.append(Issue("internal_only", f"Finding {f.sequence} is internal-only. Make it eligible for a notice (with institution-facing wording) first.", str(f.id)))
        elif not (f.institution_text and f.requested_correction):
            issues.append(Issue("no_wording", f"Finding {f.sequence} has no institution-facing wording.", str(f.id)))
    if issues:
        raise invalid("These findings cannot be included in a notice.", issues)
    rnd = sc.open_round(db, case.id)
    sequence = (db.scalar(select(func.max(DeficiencyNotice.sequence)).where(DeficiencyNotice.application_id == app.id)) or 0) + 1
    previous = db.scalar(select(DeficiencyNotice.id).where(DeficiencyNotice.application_id == app.id, DeficiencyNotice.status.in_(("ISSUED", "RESPONSE_RECEIVED", "CLOSED"))).order_by(DeficiencyNotice.sequence.desc()).limit(1))
    n = DeficiencyNotice(
        reference=_reference(db), application_id=app.id, case_id=case.id, round_id=rnd.id, sequence=sequence, previous_notice_id=previous, status=NoticeStatus.DRAFT.value,
        summary=(summary or "").strip(), prepared_by_id=user.id,
    )
    for pos, f in enumerate(findings, start=1):
        n.items.append(_item_from_finding(db, f, config, pos))
    db.add(n)
    db.flush()
    sc.log_event(db, case, user, "notice_drafted", request=request, object_type="deficiency_notice", object_id=n.id, to_state=n.status,
                 detail={"reference": n.reference, "sequence": sequence, "items": len(n.items), "previous": str(previous) if previous else None})
    return n


def update_draft(db: Session, user: User, notice_id: uuid.UUID, summary: str | None, items: list[dict] | None, add_finding_ids: list[uuid.UUID], row_version: int, request: Request | None) -> DeficiencyNotice:
    n, case = notice_for_staff(db, user, notice_id, request, need="reviewer")
    case = sc.lock_case(db, case.id)
    n = load_notice(db, n.id)
    if n.status != NoticeStatus.DRAFT.value:
        raise conflict("notice_not_draft", "Only a draft notice can be edited.")
    if n.row_version != row_version:
        raise conflict("stale_version", "This draft was changed by someone else since you opened it. Reload it before saving.")
    app = db.get(Application, case.application_id)
    config = config_of(app)
    keep = {i["id"]: i for i in (items or [])} if items is not None else None
    existing = list(n.items)
    if keep is not None:
        unknown = set(keep) - {i.id for i in existing}
        if unknown:
            raise invalid("Some items do not belong to this notice.")
    issues = []
    for it in existing:
        if keep is not None and it.id not in keep:
            continue
        edit = keep.get(it.id) if keep else None
        title = (edit.get("title") if edit and edit.get("title") is not None else it.title).strip()
        desc = (edit.get("description") if edit and edit.get("description") is not None else it.description).strip()
        corr = (edit.get("requested_correction") if edit and edit.get("requested_correction") is not None else it.requested_correction).strip()
        if len(desc) < 5 or len(corr) < 5 or not title:
            issues.append(Issue("item_text", "Each item needs a title, a description and the correction requested.", str(it.id)))
        if edit:
            it.title, it.description, it.requested_correction = title[:300], desc, corr
    added = [sc.get_finding(db, case, fid) for fid in add_finding_ids]
    taken = {i.finding_id for i in existing if keep is None or i.id in keep}
    for f in added:
        if f.id in taken or f.state not in (FindingState.OPEN.value, FindingState.UNRESOLVED.value) or f.visibility != Visibility.ELIGIBLE_FOR_NOTICE.value or not (f.institution_text and f.requested_correction):
            issues.append(Issue("finding_not_eligible", f"Finding {f.sequence} cannot be added to this notice.", str(f.id)))
    if issues:
        raise invalid("The notice was not saved.", issues)
    res = db.execute(update(DeficiencyNotice).where(DeficiencyNotice.id == n.id, DeficiencyNotice.status == NoticeStatus.DRAFT.value, DeficiencyNotice.row_version == row_version).values(
        summary=(summary if summary is not None else n.summary).strip(), row_version=row_version + 1, updated_at=utcnow()))
    if res.rowcount != 1:
        raise conflict("stale_version", "This draft was changed by someone else at the same moment. Reload it before saving.")
    for it in existing:
        if keep is not None and it.id not in keep:
            n.items.remove(it)
    db.flush()
    for pos, it in enumerate(n.items, start=1):
        it.position = pos
    for f in added:
        n.items.append(_item_from_finding(db, f, config, len(n.items) + 1))
    if not n.items:
        raise invalid("A notice needs at least one item.")
    db.flush()
    sc.log_event(db, case, user, "notice_edited", request=request, object_type="deficiency_notice", object_id=n.id, detail={"reference": n.reference, "items": len(n.items)})
    db.refresh(n)
    return n


def _prepared_issues(n: DeficiencyNotice) -> list[Issue]:
    out = []
    if len((n.summary or "").strip()) < 5:
        out.append(Issue("summary", "Write a short explanation for the institution.", "summary"))
    if not n.items:
        out.append(Issue("items", "A notice needs at least one item.", "items"))
    for it in n.items:
        if len(it.description.strip()) < 5 or len(it.requested_correction.strip()) < 5:
            out.append(Issue("item_text", f"Item {it.position} needs a description and the correction requested.", str(it.id)))
    return out


def submit_for_issuance(db: Session, user: User, notice_id: uuid.UUID, row_version: int, request: Request | None) -> DeficiencyNotice:
    n, case = notice_for_staff(db, user, notice_id, request, need="reviewer")
    case = sc.lock_case(db, case.id)
    n = load_notice(db, n.id)
    sc._require_working(case)
    if n.status != NoticeStatus.DRAFT.value:
        raise conflict("notice_not_draft", "Only a draft can be submitted for issuance.")
    issues = _prepared_issues(n)
    if issues:
        raise invalid("The notice is not ready to be submitted for issuance.", issues)
    _set_status(db, n, NoticeStatus.IN_REVIEW, expected_version=row_version, submitted_for_issuance_at=utcnow())
    from_state = case.state
    sc.transition(db, case, CaseAction.PREPARE_NOTICE)
    sc.log_event(db, case, user, "notice_submitted_for_issuance", request=request, object_type="deficiency_notice", object_id=n.id, from_state=from_state, to_state=case.state,
                 detail={"reference": n.reference})
    db.flush()
    texts.notice_awaiting_issuance(db, n, db.get(Application, case.application_id))
    return n


def withdraw(db: Session, user: User, notice_id: uuid.UUID, reason: str, request: Request | None) -> DeficiencyNotice:
    n, case = notice_for_staff(db, user, notice_id, request, need="reviewer")
    case = sc.lock_case(db, case.id)
    n = load_notice(db, n.id)
    if n.status not in NOTICE_PREPARING:
        raise conflict("notice_not_withdrawable", "Only a notice that has not been issued can be withdrawn.")
    if not reason or len(reason.strip()) < 5:
        raise invalid("Record why the notice is being withdrawn.")
    was = n.status
    _set_status(db, n, NoticeStatus.WITHDRAWN, withdrawn_reason=reason.strip())
    from_state = case.state
    if was == NoticeStatus.IN_REVIEW.value:
        sc.transition(db, case, _return_action(db, case))
    sc.log_event(db, case, user, "notice_withdrawn", request=request, object_type="deficiency_notice", object_id=n.id, from_state=from_state, to_state=case.state, reason=reason, detail={"reference": n.reference})
    return n


def return_notice(db: Session, user: User, notice_id: uuid.UUID, reason: str, request: Request | None) -> DeficiencyNotice:
    """The issuer declines to issue for now and sends it back to the reviewer, with a reason."""
    n, case = notice_for_staff(db, user, notice_id, request, need="issuer")
    case = sc.lock_case(db, case.id)
    n = load_notice(db, n.id)
    if n.status != NoticeStatus.IN_REVIEW.value:
        raise conflict("notice_not_in_review", "Only a notice that is awaiting an issuing decision can be returned.")
    if not reason or len(reason.strip()) < 5:
        raise invalid("Record why the notice is being returned.")
    _set_status(db, n, NoticeStatus.DRAFT, returned_reason=reason.strip())
    from_state = case.state
    sc.transition(db, case, _return_action(db, case))
    sc.log_event(db, case, user, "notice_returned", request=request, object_type="deficiency_notice", object_id=n.id, from_state=from_state, to_state=case.state, reason=reason, detail={"reference": n.reference})
    return n


# ---- preview / content --------------------------------------------------------------------------


def _canonical(d: dict) -> str:
    return json.dumps(d, sort_keys=True, separators=(",", ":"), default=str)


def build_content(db: Session, n: DeficiencyNotice, app: Application, config: dict, pol: policy.PolicyResolution) -> dict:
    previous = db.get(DeficiencyNotice, n.previous_notice_id) if n.previous_notice_id else None
    demo = policy.is_demo_case(config)
    return {
        "schema": 1, "reference": n.reference, "sequence": n.sequence, "previous_notice_reference": previous.reference if previous else None,
        "application": {"reference": app.reference, "institution": app.institution.name, "category": app.category.name, "academic_year": app.academic_year},
        "rule_version": {"rule_set": config["rule_set"]["key"], "number": config["version"]["number"], "classification": config["rule_set"]["classification"]},
        "demo_notice": DEMO_BANNER if demo else None, "template": DEMO_TEMPLATE if demo else None,
        "summary": n.summary,
        "items": [
            {"position": i.position, "title": i.title, "description": i.description, "requested_correction": i.requested_correction, "item_key": i.item_key,
             "document_version": i.document_version, "page_number": i.page_number, "source_ref": i.source_ref, "source_ref_status": i.source_ref_status}
            for i in n.items
        ],
        "response_period": pol.stable(),
    }


def preview(db: Session, n: DeficiencyNotice, settings: Settings) -> Preview:
    app = db.scalars(select(Application).where(Application.id == n.application_id).options(selectinload(Application.institution), selectinload(Application.category))).one()
    config = config_of(app)
    pol = policy.resolve_response_policy(config, settings)
    content = build_content(db, n, app, config, pol)
    p = Preview(content, hashlib.sha256(_canonical(content).encode()).hexdigest(), pol)
    demo = policy.is_demo_case(config)
    if not demo:
        p.blockers.append(Blocker("notice_template_unavailable", "No approved notice template is configured for this case. A formal notice cannot be issued with invented wording; a person must configure an approved template first."))
    if not pol.settled and not (demo and settings.demo_rules_enabled):
        p.blockers.append(Blocker("response_policy_unresolved", f"The response period cannot be established: {pol.message} This has been routed to the rule configuration reviewers; no due date will be invented."))
    p.unsourced = [i.position for i in n.items if i.source_ref_status != SourceRefStatus.ESTABLISHED.value]
    return p


# ---- issuance -----------------------------------------------------------------------------------


def issue(
    db: Session, user: User, notice_id: uuid.UUID, content_hash: str, issue_reason: str, acknowledge_unsourced: bool, idempotency_key: str, row_version: int,
    request: Request | None, settings: Settings,
) -> tuple[DeficiencyNotice, bool]:
    """-> (notice, replay). `replay` is True when this exact request had already issued the notice."""
    n, case = notice_for_staff(db, user, notice_id, request, need="issuer")
    case = sc.lock_case(db, case.id)
    n = load_notice(db, n.id)

    if n.status in (NoticeStatus.ISSUED.value, NoticeStatus.RESPONSE_RECEIVED.value, NoticeStatus.CLOSED.value):
        if n.idempotency_key == idempotency_key:
            return n, True  # the client retried; the notice was issued once, by this request
        raise conflict("already_issued", f"Notice {n.reference} has already been issued. It was not issued a second time.")
    if n.status != NoticeStatus.IN_REVIEW.value:
        raise conflict("notice_not_in_review", "Only a notice that is awaiting an issuing decision can be issued.")
    if n.row_version != row_version:
        raise conflict("stale_version", "This notice changed since you opened it. Reload and inspect the current version before issuing.")
    if not issue_reason or len(issue_reason.strip()) < 5:
        raise invalid("Record the reason for issuing this notice.")

    pv = preview(db, n, settings)
    if pv.blockers:
        app = db.get(Application, n.application_id)
        if any(b.code == "response_policy_unresolved" for b in pv.blockers):
            rules.record_gap(db, app.category_id, app.academic_year, GapReason.INCOMPLETE, {"missing": "deficiency_notice_response_policy", "state": pv.policy.state.value, "rule_version_id": str(app.rule_version_id), "message": pv.policy.message})
        audit.record(db, "notice.issue_blocked", request=request, actor=user, target_type="deficiency_notice", target_id=n.id, detail={"blockers": [b.code for b in pv.blockers]})
        db.commit()  # the refusal and the configuration issue are recorded even though nothing was issued
        raise ServiceError(409, pv.blockers[0].code, pv.blockers[0].message, [Issue(b.code, b.message) for b in pv.blockers])
    if pv.content_hash != content_hash:
        raise conflict("content_changed", "The notice's content is not what you inspected. Reload it, read it again and confirm.")
    if pv.unsourced and not acknowledge_unsourced:
        raise invalid("Some items have no established source reference. Confirm that you are issuing them without one.", [Issue("unsourced", f"Item {p} has no established source reference.", str(p)) for p in pv.unsourced], code="unsourced_items_need_acknowledgement")

    now = utcnow()
    due = pv.policy.due_at(now)
    snapshot = {**pv.content, "issued_at": now.isoformat(), "response_due_at": due.isoformat() if due else None, "issued_by_role": ROLE_LABELS[Role(user.role)]}
    authority = {
        "capability": Permission.NOTICE_ISSUE.value, "role": user.role, "role_label": ROLE_LABELS[Role(user.role)],
        "official_designation": "NOT_ESTABLISHED: this is an application capability; it is not claimed to be an AICTE designation until formally mapped",
    }
    try:
        _set_status(
            db, n, NoticeStatus.ISSUED, expected_version=row_version, issued_by_id=user.id, issuer_role=user.role, issued_at=now, issue_reason=issue_reason.strip(),
            issue_confirmation={"confirmed_content_hash": content_hash, "confirmed_at": now.isoformat(), "acknowledged_unsourced": bool(pv.unsourced and acknowledge_unsourced)},
            authority=authority, idempotency_key=idempotency_key, policy_resolution=pv.policy.stable(), response_due_at=due, template=pv.content["template"],
            content_snapshot=snapshot, content_hash=pv.content_hash,
        )
    except IntegrityError as exc:  # the idempotency key (unique) was already used for a different notice
        raise ServiceError(409, "idempotency_key_reused", "That request identifier was already used for a different notice. Nothing was issued.") from exc
    # the findings it carries are now "noticed"; the round that produced them is over
    for it in n.items:
        res = db.execute(update(ScrutinyFinding).where(ScrutinyFinding.id == it.finding_id, ScrutinyFinding.state.in_((FindingState.OPEN.value, FindingState.UNRESOLVED.value))).values(
            state=FindingState.NOTICED.value, row_version=ScrutinyFinding.row_version + 1, updated_at=now))
        if res.rowcount != 1:
            raise conflict("finding_changed", "A finding in this notice was changed after the notice was prepared. Nothing was issued.")
        db.add(FindingEvent(finding_id=it.finding_id, round_id=n.round_id, actor_id=user.id, action="noticed", to_state=FindingState.NOTICED.value, reason=f"Included in notice {n.reference}"))
    res = db.execute(update(ScrutinyRound).where(ScrutinyRound.id == n.round_id, ScrutinyRound.status == RoundStatus.OPEN.value).values(
        status=RoundStatus.NOTICE_ISSUED.value, closed_at=now, outcome="NOTICE_ISSUED", outcome_reason=f"Notice {n.reference} issued", row_version=ScrutinyRound.row_version + 1))
    if res.rowcount != 1:
        raise conflict("stale_version", "The scrutiny round changed at the same moment. Nothing was issued.")
    from_state = case.state
    sc.transition(db, case, CaseAction.ISSUE_NOTICE)
    sc.log_event(
        db, case, user, "notice_issued", request=request, object_type="deficiency_notice", object_id=n.id, from_state=from_state, to_state=case.state, reason=issue_reason, institution_visible=True,
        detail={"reference": n.reference, "sequence": n.sequence, "content_hash": n.content_hash, "response_due_at": due.isoformat() if due else None, "policy": pv.policy.state.value,
                "template": pv.content["template"]["key"] if pv.content["template"] else None, "role": user.role},
    )
    db.add(ApplicationEvent(application_id=n.application_id, actor_id=user.id, action="deficiency_notice_issued", detail={"reference": n.reference, "sequence": n.sequence}))
    db.flush()
    db.refresh(n)
    texts.notice_issued_notification(db, n)
    return n, False
