"""Shared plumbing for the EVC and SHC services: history, locking and the one way a committee case changes state.

A case moves only through `transition()`: one compare-and-set UPDATE checked against `COMMITTEE_TRANSITIONS`. A stale view, an undefined move
or a lost race is refused and leaves nothing changed. Every action writes a case event and an audit record (actor, time, object, from/to state, reason).
"""

import uuid

from fastapi import Request
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app import audit
from app.domain import COMMITTEE_TRANSITIONS, CAction, CommitteeStage, CState
from app.models import User, utcnow
from app.models_applications import Application
from app.models_committee import CommitteeCase, CommitteeEventRow
from app.services.applications import config_of
from app.services.errors import ServiceError, conflict, not_found


def log_event(
    db: Session, case: CommitteeCase | None, actor: User | None, action: str, *, request: Request | None = None, object_type: str | None = None, object_id: uuid.UUID | str | None = None,
    from_state: str | None = None, to_state: str | None = None, reason: str | None = None, detail: dict | None = None, institution_visible: bool = False,
    stage: str | None = None, application_id: uuid.UUID | None = None,
) -> None:
    """One case-history event plus the matching audit record. `detail` holds identifiers and states, never document content."""
    app_id = application_id or (case.application_id if case else None)
    st = stage or (case.stage if case else "ROUTE")
    db.add(CommitteeEventRow(
        stage=st, case_id=case.id if case else None, application_id=app_id, actor_id=actor.id if actor else None, actor_label=actor.full_name if actor else "System", action=action,
        object_type=object_type, object_id=str(object_id) if object_id else None, from_state=from_state, to_state=to_state, reason=reason, detail=detail, institution_visible=institution_visible,
    ))
    audit.record(
        db, f"committee.{action}", request=request, actor=actor, target_type=object_type or "committee_case", target_id=object_id or (case.id if case else app_id),
        detail={"stage": st, "case_id": str(case.id) if case else None, "application_id": str(app_id), "from": from_state, "to": to_state, "reason": (reason or "")[:500] or None, **(detail or {})},
    )


def lock_case(db: Session, case_id: uuid.UUID) -> CommitteeCase:
    q = select(CommitteeCase).where(CommitteeCase.id == case_id).execution_options(populate_existing=True)
    if db.bind.dialect.name == "postgresql":
        q = q.with_for_update()
    case = db.scalars(q).one_or_none()
    if case is None:
        raise not_found("Committee case")
    return case


def transition(db: Session, case: CommitteeCase, action: CAction, *, expected_version: int | None = None, **extra) -> CState:
    try:
        current = CState(case.state)
    except ValueError:
        raise ServiceError(409, "invalid_transition", "The case is in an unrecognised state.") from None
    target = COMMITTEE_TRANSITIONS.get((CommitteeStage(case.stage), current, action))
    if target is None:
        raise conflict("invalid_transition", f"That action is not allowed while the case is “{current.value}”.")
    if expected_version is not None and case.row_version != expected_version:
        raise conflict("stale_version", "This case was changed by someone else since you opened it. Reload it to see the latest state.")
    now = utcnow()
    res = db.execute(update(CommitteeCase).where(CommitteeCase.id == case.id, CommitteeCase.state == current.value, CommitteeCase.row_version == case.row_version).values(
        state=target.value, row_version=case.row_version + 1, updated_at=now, state_changed_at=now, **extra))
    if res.rowcount != 1:
        raise conflict("stale_version", "This case was changed by someone else at the same moment. Reload it and try again.")
    db.refresh(case)
    return target


def touch(db: Session, case: CommitteeCase) -> None:
    """Advance the case's row version without a state change (an assignment or schedule changed), so every open view is told it is stale."""
    res = db.execute(update(CommitteeCase).where(CommitteeCase.id == case.id, CommitteeCase.row_version == case.row_version).values(row_version=case.row_version + 1, updated_at=utcnow()))
    if res.rowcount != 1:
        raise conflict("stale_version", "This case was changed by someone else at the same moment. Reload it and try again.")
    db.refresh(case)


def application(db: Session, case: CommitteeCase) -> Application:
    return db.get(Application, case.application_id)


def case_config(db: Session, case: CommitteeCase) -> dict:
    return config_of(application(db, case))


def require_version(case: CommitteeCase, row_version: int) -> None:
    if case.row_version != row_version:
        raise conflict("stale_version", "This case was changed by someone else since you opened it. Reload it to see the latest state.")


def require_state(case: CommitteeCase, *states: CState) -> None:
    if case.state not in {s.value for s in states}:
        raise conflict("invalid_transition", f"That action is not allowed while the case is “{case.state}”.")


def policy(case: CommitteeCase) -> dict:
    return case.policy_snapshot or {}


def require_no_blockers(case: CommitteeCase, *codes: str) -> None:
    """Refuse an action while a configuration problem that matters to it is unresolved. `codes` empty means any blocker."""
    bad = [b for b in (case.blockers or []) if not codes or b["code"] in codes]
    if bad:
        from app.services.errors import Issue

        raise conflict("configuration_blocked", bad[0]["message"], [Issue(b["code"], b["message"]) for b in bad])
