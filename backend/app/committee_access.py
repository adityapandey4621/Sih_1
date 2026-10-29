"""Who may see or act on which committee case.

A permission says what a role can do in principle (app/rbac.py); this module says *where*:

  coordinator  holds {evc|shc}:coordinate and the case is of that stage: every case of the stage
  member       holds {evc|shc}:work and has an active assignment to this case

Holding a broad "EVC member" role is never enough: without an assignment the case does not exist for that person. Out-of-scope access answers
"not found" (existence is not revealed) and is audited.
"""

import uuid
from dataclasses import dataclass

from fastapi import Request
from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from app import audit
from app.domain import CommitteeStage
from app.models import User
from app.models_committee import CommitteeAssignment, CommitteeCase
from app.rbac import Permission, permissions_for
from app.services.errors import not_found

_PERMS = {
    CommitteeStage.EVC: (Permission.EVC_QUEUE, Permission.EVC_COORDINATE, Permission.EVC_WORK),
    CommitteeStage.SHC: (Permission.SHC_QUEUE, Permission.SHC_COORDINATE, Permission.SHC_WORK),
    CommitteeStage.SAC: (Permission.SAC_QUEUE, Permission.SAC_COORDINATE, Permission.SAC_WORK),
}


@dataclass(frozen=True)
class CScope:
    kinds: frozenset[str]  # subset of {"coordinator", "member"}

    def __contains__(self, kind: str) -> bool:
        return kind in self.kinds

    def __bool__(self) -> bool:
        return bool(self.kinds)


def queue_permission(stage: CommitteeStage) -> Permission:
    return _PERMS[stage][0]


def is_assigned(db: Session, user_id: uuid.UUID, case_id: uuid.UUID) -> bool:
    return db.scalar(select(exists().where(CommitteeAssignment.case_id == case_id, CommitteeAssignment.user_id == user_id, CommitteeAssignment.revoked_at.is_(None)))) or False


def scope_of(db: Session, user: User, case: CommitteeCase) -> CScope:
    stage = CommitteeStage(case.stage)
    _, coordinate, work = _PERMS[stage]
    perms = permissions_for(user.role)
    kinds: set[str] = set()
    if coordinate in perms:
        kinds.add("coordinator")
    if work in perms and is_assigned(db, user.id, case.id):
        kinds.add("member")
    return CScope(frozenset(kinds))


def get_case(db: Session, user: User, case_id: uuid.UUID, request: Request | None, *, stage: CommitteeStage, need: str | None = None) -> tuple[CommitteeCase, CScope]:
    """Load a case the user may see; otherwise "not found" + an audit record. `need` demands a specific kind of scope."""
    case = db.get(CommitteeCase, case_id, populate_existing=True)
    if case is not None and case.stage != stage.value:
        case = None  # an EVC id used on an SHC route (or the reverse) is simply not found
    scope = scope_of(db, user, case) if case is not None else CScope(frozenset())
    if case is None or not scope or (need is not None and need not in scope):
        if case is not None:
            audit.record(db, "committee.access_denied", request=request, actor=user, target_type="committee_case", target_id=case_id, detail={"stage": stage.value, "need": need or "view"})
            db.commit()
        raise not_found(f"{stage.value} case")
    return case, scope


def can_read_documents(db: Session, user: User, application_id: uuid.UUID) -> bool:
    """Read access to a submitted application's evidence for committee work: a coordinator of a stage the application is in, or an assigned member."""
    if Permission.EVIDENCE_READ not in permissions_for(user.role):
        return False
    for case in db.scalars(select(CommitteeCase).where(CommitteeCase.application_id == application_id)):
        if scope_of(db, user, case):
            return True
    if Permission.DECISION_QUEUE in permissions_for(user.role):  # Phase 6: the Executive Committee, Regulation Bureau and Council read the record behind a decision
        from app.models_decisions import DecisionCase

        return db.scalar(select(exists().where(DecisionCase.application_id == application_id))) or False
    return False
