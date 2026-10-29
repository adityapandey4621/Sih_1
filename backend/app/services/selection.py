"""The selection boundary: who may be put on a committee case, and how that is decided.

AICTE's handbook says committee members are chosen by an automated, portal-based process that AICTE provides (Ch. I cl. 1.8(a), 1.9(a)), and a reported
December 2025 corrigendum changes who constitutes the EVC (unverified). So this system does **not** choose members. It defines a provider boundary:

  provider.submit(...)   ask an authorised mechanism for a selection  -> COMPLETED (members) | REQUESTED (answer comes later) | FAILED
  provider.poll(...)     ask again for a pending request             -> a result, or None while it is still pending

Two providers exist:
  * DemoProvider           DEMO-classified rule sets only, in a development deployment. A coordinator records FICTIONAL members. The record is flagged
                           `is_demo` everywhere and says it is not the official process.
  * ExternalPortalProvider the real mechanism. **Not connected**: it reports itself unavailable and never pretends a call succeeded. A real adapter
                           replaces it by implementing the two methods above (see docs/COMMITTEE.md).

Every request, result, failure and assignment is persisted with its mode, source, time, actor and the configuration it was made under. A result is applied
only if it can be verified (active users of the right role, at least the configured minimum): otherwise the request is recorded INCOMPLETE and no
assignment exists. Nothing is invented to fill a gap.
"""

import secrets
import uuid
from dataclasses import dataclass, field

from fastapi import Request
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app import audit, committee_access
from app.config import Settings
from app.domain import CAction, CommitteeStage, CState, SelectionMode, SelectionStatus
from app.models import User, utcnow
from app.models_committee import CommitteeAssignment, CommitteeCase, SelectionRequest
from app.rbac import Role
from app.services import committee_core as core
from app.services import committee_policy as cpolicy
from app.services import committee_texts as ctexts
from app.services import policy as response_policy
from app.services.errors import ServiceError, conflict, invalid

DEMO_SELECTION_NOTICE = "Demonstration selection: a coordinator recorded fictional members. This is NOT the official AICTE committee selection process."
NOT_CONNECTED_NOTICE = (
    "Committee members must come from the authorised AICTE selection process. No interface to that process is connected to this system, so no selection can be requested or recorded here."
)
MEMBER_ROLE = {CommitteeStage.EVC: Role.EVC_MEMBER, CommitteeStage.SHC: Role.SHC_MEMBER, CommitteeStage.SAC: Role.SAC_MEMBER}
_STATE_MOVES = (CommitteeStage.EVC, CommitteeStage.SAC)  # stages where selecting members moves the case's state (SHC only attaches members)


@dataclass
class Availability:
    available: bool
    mode: str
    label: str
    notice: str
    demo: bool = False


@dataclass
class ProviderResult:
    status: SelectionStatus
    member_ids: list[uuid.UUID] = field(default_factory=list)
    external_ref: str | None = None
    error_code: str | None = None
    error_detail: str | None = None
    source: str | None = None  # who or what delivered it (an external system's name)


class ProviderUnavailable(Exception):
    pass


class SelectionProvider:
    mode: str = ""
    label: str = ""
    is_demo: bool = False

    def availability(self, config: dict, settings: Settings) -> Availability:  # pragma: no cover - interface
        raise NotImplementedError

    def submit(self, db: Session, case: CommitteeCase, requested_ids: list[uuid.UUID], actor: User | None) -> ProviderResult:  # pragma: no cover - interface
        raise NotImplementedError

    def poll(self, db: Session, req: SelectionRequest) -> ProviderResult | None:  # pragma: no cover - interface
        raise NotImplementedError


class DemoProvider(SelectionProvider):
    mode = SelectionMode.DEMO_MANUAL.value
    label = "Demonstration selection"
    is_demo = True

    def availability(self, config: dict, settings: Settings) -> Availability:
        return Availability(True, self.mode, self.label, DEMO_SELECTION_NOTICE, demo=True)

    def submit(self, db: Session, case: CommitteeCase, requested_ids: list[uuid.UUID], actor: User | None) -> ProviderResult:
        return ProviderResult(SelectionStatus.COMPLETED, list(requested_ids), source=actor.email if actor else "demo coordinator")

    def poll(self, db: Session, req: SelectionRequest) -> ProviderResult | None:
        return None  # a demonstration request is answered immediately; there is nothing to wait for


class ExternalPortalProvider(SelectionProvider):
    """The real selection mechanism. Not connected: no call is made and none is claimed."""

    mode = SelectionMode.EXTERNAL_PORTAL.value
    label = "Official selection (not connected)"

    def availability(self, config: dict, settings: Settings) -> Availability:
        return Availability(False, self.mode, self.label, NOT_CONNECTED_NOTICE)

    def submit(self, db: Session, case: CommitteeCase, requested_ids: list[uuid.UUID], actor: User | None) -> ProviderResult:
        raise ProviderUnavailable(NOT_CONNECTED_NOTICE)

    def poll(self, db: Session, req: SelectionRequest) -> ProviderResult | None:
        raise ProviderUnavailable(NOT_CONNECTED_NOTICE)


_DEMO, _EXTERNAL = DemoProvider(), ExternalPortalProvider()
PROVIDER_OVERRIDE: SelectionProvider | None = None  # tests replace the external provider with a scripted double; production code never sets it


def provider_for(config: dict, settings: Settings) -> SelectionProvider:
    if response_policy.is_demo_case(config) and settings.demo_rules_enabled:
        return _DEMO
    return PROVIDER_OVERRIDE or _EXTERNAL


def provider_by_mode(mode: str) -> SelectionProvider:
    return _DEMO if mode == SelectionMode.DEMO_MANUAL.value else (PROVIDER_OVERRIDE or _EXTERNAL)


def availability(db: Session, case: CommitteeCase, settings: Settings) -> Availability:
    return provider_for(core.case_config(db, case), settings).availability(core.case_config(db, case), settings)


def _min_members(case: CommitteeCase) -> int:
    return int((core.policy(case).get("content", {}).get("composition", {}) or {}).get("min_members") or 1)


# ---- requesting ---------------------------------------------------------------------------------------

_EVC_OPEN = (CState.SELECTION_PENDING,)
_SHC_OPEN = (CState.PREPARATION, CState.AWAITING_COMPLIANCE, CState.COMPLIANCE_RECEIVED, CState.UNDER_REVIEW)


def _new_ref() -> str:
    return "SEL-" + secrets.token_hex(4).upper()


def _verify_members(db: Session, stage: CommitteeStage, ids: list[uuid.UUID]) -> tuple[list[User], list[str]]:
    ok, bad = [], []
    for uid in dict.fromkeys(ids):
        u = db.get(User, uid)
        if u is None or not u.is_active or u.role != MEMBER_ROLE[stage].value:
            bad.append(str(uid))
        else:
            ok.append(u)
    return ok, bad


def request_selection(
    db: Session, actor: User, case_id: uuid.UUID, stage: CommitteeStage, user_ids: list[uuid.UUID], idempotency_key: str, request: Request | None, settings: Settings,
) -> tuple[CommitteeCase, SelectionRequest, bool]:
    """-> (case, request, replay). A repeated key returns the same request."""
    case, _ = committee_access.get_case(db, actor, case_id, request, stage=stage, need="coordinator")
    prior = db.scalar(select(SelectionRequest).where(SelectionRequest.idempotency_key == idempotency_key))
    if prior is not None:
        if prior.case_id != case.id:
            raise conflict("idempotency_key_reused", "That key was used for a different case.")
        return core.lock_case(db, case.id), prior, True
    case = core.lock_case(db, case.id)
    open_states = _EVC_OPEN if stage in _STATE_MOVES else _SHC_OPEN
    core.require_state(case, *open_states)
    if db.scalar(select(SelectionRequest.id).where(SelectionRequest.case_id == case.id, SelectionRequest.status == SelectionStatus.REQUESTED.value)) is not None:
        raise conflict("selection_in_progress", "A selection request for this case is already waiting for a result.")
    config = core.case_config(db, case)
    provider = provider_for(config, settings)
    avail = provider.availability(config, settings)
    if not avail.available:
        audit.record(db, "committee.selection_unavailable", request=request, actor=actor, target_type="committee_case", target_id=case.id, detail={"stage": stage.value, "mode": avail.mode})
        db.commit()
        raise ServiceError(409, "selection_unavailable", avail.notice)
    app = core.application(db, case)
    if provider.is_demo:
        ok, bad = _verify_members(db, stage, user_ids)
        if bad or not ok:
            raise invalid(f"Choose active {MEMBER_ROLE[stage].value.replace('_', ' ')}s from the list.")
        if len(ok) < _min_members(case):
            raise invalid(f"At least {_min_members(case)} member(s) are needed under this (demonstration) configuration.")
    elif user_ids:
        raise invalid("With the official selection process, members are not chosen here; request the selection and wait for its result.")
    req = SelectionRequest(
        case_id=case.id, request_ref=_new_ref(), idempotency_key=idempotency_key, provider=provider.mode, is_demo=provider.is_demo, status=SelectionStatus.REQUESTED.value, requested_by_id=actor.id,
        config_ref={"rule_version_id": str(case.rule_version_id), "policy": cpolicy.ref(case.policy_snapshot), "mode": provider.mode},
    )
    db.add(req)
    db.flush()
    from_state = case.state
    if stage in _STATE_MOVES:
        core.transition(db, case, CAction.REQUEST_SELECTION)
    core.log_event(db, case, actor, "selection_requested", request=request, object_type="selection_request", object_id=req.id, from_state=from_state, to_state=case.state,
                   detail={"request_ref": req.request_ref, "provider": provider.mode, "demo": provider.is_demo})
    db.flush()
    ctexts.selection_requested(db, case, app, req.request_ref, provider.is_demo)
    try:
        result = provider.submit(db, case, [u.id for u in (ok if provider.is_demo else [])], actor)
    except ProviderUnavailable as exc:
        result = ProviderResult(SelectionStatus.FAILED, error_code="provider_unavailable", error_detail=str(exc))
    except Exception as exc:  # noqa: BLE001 - a provider fault is a recorded outcome, not a crash that leaves the case half-changed
        result = ProviderResult(SelectionStatus.FAILED, error_code="provider_error", error_detail=type(exc).__name__)
    if result.external_ref:
        db.execute(update(SelectionRequest).where(SelectionRequest.id == req.id).values(external_ref=result.external_ref))
    if result.status != SelectionStatus.REQUESTED:
        apply_result(db, case, req, result, actor, request)
    db.refresh(req)
    return case, req, False


# ---- applying a result ---------------------------------------------------------------------------------


def apply_result(db: Session, case: CommitteeCase, req: SelectionRequest, result: ProviderResult, actor: User | None, request: Request | None) -> SelectionRequest:
    """Record a provider's answer. Usable only if every member can be verified and the configured minimum is met; otherwise nothing is assigned."""
    stage = CommitteeStage(case.stage)
    app = core.application(db, case)
    status, error_code, error_detail = result.status, result.error_code, result.error_detail
    members: list[User] = []
    if status == SelectionStatus.COMPLETED:
        members, bad = _verify_members(db, stage, result.member_ids)
        if bad:
            status, error_code, error_detail = SelectionStatus.INCOMPLETE, "member_not_verified", f"{len(bad)} selected member(s) could not be verified as active {MEMBER_ROLE[stage].value.replace('_', ' ')}s."
        elif len(members) < _min_members(case):
            status, error_code, error_detail = SelectionStatus.INCOMPLETE, "too_few_members", f"The result names {len(members)} member(s); the configuration needs at least {_min_members(case)}."
    now = utcnow()
    source = result.source or (actor.email if actor else "external source")
    res = db.execute(update(SelectionRequest).where(SelectionRequest.id == req.id, SelectionRequest.status == SelectionStatus.REQUESTED.value).values(
        status=status.value, result_at=now, result_source=source, error_code=error_code, error_detail=error_detail,
        result={"members": [{"user_id": str(u.id), "name": u.full_name} for u in members]} if status == SelectionStatus.COMPLETED else None,
        **({"external_ref": result.external_ref} if result.external_ref else {})))
    if res.rowcount != 1:
        raise conflict("selection_already_resolved", "This selection request already has a result.")
    db.refresh(req)
    from_state = case.state
    if status == SelectionStatus.COMPLETED:
        for u in members:
            if not db.scalar(select(CommitteeAssignment.id).where(CommitteeAssignment.case_id == case.id, CommitteeAssignment.user_id == u.id, CommitteeAssignment.revoked_at.is_(None))):
                db.add(CommitteeAssignment(case_id=case.id, user_id=u.id, selection_request_id=req.id, source=req.provider, is_demo=req.is_demo))
        db.flush()
        if stage in _STATE_MOVES:
            core.transition(db, case, CAction.RECORD_SELECTION)
            if stage == CommitteeStage.SAC:
                from app.services import sac

                sac.ensure_draft(db, case, actor)  # the review begins when members are recorded
        else:
            core.touch(db, case)
        core.log_event(db, case, actor, "selection_recorded", request=request, object_type="selection_request", object_id=req.id, from_state=from_state, to_state=case.state,
                       detail={"request_ref": req.request_ref, "provider": req.provider, "demo": req.is_demo, "members": [str(u.id) for u in members], "source": source})
        ctexts.selection_recorded(db, case, app, req.request_ref, [u.id for u in members], req.is_demo)
    else:
        if stage in _STATE_MOVES and case.state == CState.SELECTION_REQUESTED.value:
            core.transition(db, case, CAction.SELECTION_FAILED)
        else:
            core.touch(db, case)
        core.log_event(db, case, actor, "selection_failed", request=request, object_type="selection_request", object_id=req.id, from_state=from_state, to_state=case.state, reason=error_detail,
                       detail={"request_ref": req.request_ref, "status": status.value, "error_code": error_code, "provider": req.provider})
        ctexts.selection_failed(db, case, app, req.request_ref, error_detail or status.value)
    db.flush()
    return req


def refresh(db: Session, actor: User, case_id: uuid.UUID, stage: CommitteeStage, request: Request | None) -> tuple[CommitteeCase, SelectionRequest | None]:
    """Ask the provider again about the open request. A provider that cannot answer leaves the request pending, visibly."""
    case, _ = committee_access.get_case(db, actor, case_id, request, stage=stage, need="coordinator")
    case = core.lock_case(db, case.id)
    req = db.scalar(select(SelectionRequest).where(SelectionRequest.case_id == case.id, SelectionRequest.status == SelectionStatus.REQUESTED.value))
    if req is None:
        raise conflict("no_open_selection", "There is no selection request waiting for a result.")
    try:
        result = provider_by_mode(req.provider).poll(db, req)
    except ProviderUnavailable as exc:
        raise ServiceError(409, "selection_unavailable", str(exc)) from exc
    if result is not None:
        apply_result(db, case, req, result, actor, request)
        db.refresh(req)
    return case, req


def cancel_request(db: Session, actor: User, case_id: uuid.UUID, stage: CommitteeStage, reason: str, request: Request | None) -> CommitteeCase:
    case, _ = committee_access.get_case(db, actor, case_id, request, stage=stage, need="coordinator")
    case = core.lock_case(db, case.id)
    if not reason or len(reason.strip()) < 5:
        raise invalid("Record why the request is being cancelled.")
    req = db.scalar(select(SelectionRequest).where(SelectionRequest.case_id == case.id, SelectionRequest.status == SelectionStatus.REQUESTED.value))
    if req is None:
        raise conflict("no_open_selection", "There is no selection request waiting for a result.")
    now = utcnow()
    res = db.execute(update(SelectionRequest).where(SelectionRequest.id == req.id, SelectionRequest.status == SelectionStatus.REQUESTED.value).values(
        status=SelectionStatus.CANCELLED.value, result_at=now, result_source=actor.email, error_code="cancelled", error_detail=reason.strip()))
    if res.rowcount != 1:
        raise conflict("selection_already_resolved", "This selection request already has a result.")
    from_state = case.state
    if stage in _STATE_MOVES:
        core.transition(db, case, CAction.SELECTION_FAILED)
    else:
        core.touch(db, case)
    core.log_event(db, case, actor, "selection_cancelled", request=request, object_type="selection_request", object_id=req.id, from_state=from_state, to_state=case.state, reason=reason)
    return case


def reselect(db: Session, actor: User, case_id: uuid.UUID, reason: str, row_version: int, request: Request | None) -> CommitteeCase:
    """EVC only: before the visit starts, withdraw the assignments (and any live schedule) so a fresh selection can be requested. Nothing is deleted."""
    case, _ = committee_access.get_case(db, actor, case_id, request, stage=CommitteeStage.EVC, need="coordinator")
    case = core.lock_case(db, case.id)
    core.require_version(case, row_version)
    if not reason or len(reason.strip()) < 5:
        raise invalid("Record why the selection is being redone.")
    now = utcnow()
    from app.services import scheduling

    scheduling.cancel_active(db, case, actor, f"Selection redone: {reason.strip()}", request, silent=False)
    db.execute(update(CommitteeAssignment).where(CommitteeAssignment.case_id == case.id, CommitteeAssignment.revoked_at.is_(None)).values(revoked_at=now, revoked_by_id=actor.id, revoked_reason=reason.strip()[:500]))
    from_state = case.state
    core.transition(db, case, CAction.RESELECT)
    core.log_event(db, case, actor, "reselected", request=request, object_type="committee_case", object_id=case.id, from_state=from_state, to_state=case.state, reason=reason)
    return case


def active_assignments(db: Session, case_id: uuid.UUID) -> list[CommitteeAssignment]:
    return list(db.scalars(select(CommitteeAssignment).where(CommitteeAssignment.case_id == case_id, CommitteeAssignment.revoked_at.is_(None)).order_by(CommitteeAssignment.created_at)))


def reconcile(db: Session, settings: Settings) -> int:
    """Poll every pending request of a connected provider and apply any result that has arrived. Safe to run repeatedly."""
    applied = 0
    for req in list(db.scalars(select(SelectionRequest).where(SelectionRequest.status == SelectionStatus.REQUESTED.value))):
        try:
            result = provider_by_mode(req.provider).poll(db, req)
        except ProviderUnavailable:
            continue  # nothing connected: the request stays pending and visible
        if result is None:
            continue
        case = core.lock_case(db, req.case_id)
        apply_result(db, case, req, result, None, None)
        applied += 1
    db.commit()
    return applied
