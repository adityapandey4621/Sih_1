"""Who may see or act on which scrutiny case.

Permissions (app/rbac.py) say what a role can do in principle; this module says *where*: a reviewer only on
cases they are assigned to, an issuer only on cases with a notice that was prepared for them, a coordinator on queue
metadata. Out-of-scope access answers "not found" (the case's existence is not revealed) and is audited.

  reviewer     assigned (active assignment)           read evidence, record findings, prepare notices, complete rounds
  issuer       a notice was submitted for issuance    read evidence + notices, issue / return notices
  coordinator  every case                              queue metadata, assignment, route determination; NO document contents
"""

import uuid
from dataclasses import dataclass

from fastapi import Request
from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from app import audit
from app.models import User
from app.models_applications import Application
from app.models_scrutiny import DeficiencyNotice, ScrutinyAssignment, ScrutinyCase
from app.rbac import Permission, permissions_for
from app.services.errors import not_found


@dataclass(frozen=True)
class Scope:
    kinds: frozenset[str]  # subset of {"reviewer", "issuer", "coordinator"} that applies to this user on this case

    def __contains__(self, kind: str) -> bool:
        return kind in self.kinds

    @property
    def can_read_documents(self) -> bool:
        return bool(self.kinds & {"reviewer", "issuer"})


def is_assigned(db: Session, user_id: uuid.UUID, case_id: uuid.UUID) -> bool:
    return db.scalar(select(exists().where(ScrutinyAssignment.case_id == case_id, ScrutinyAssignment.user_id == user_id, ScrutinyAssignment.revoked_at.is_(None)))) or False


def has_notice_for_issuer(db: Session, case_id: uuid.UUID) -> bool:
    return db.scalar(select(exists().where(DeficiencyNotice.case_id == case_id, DeficiencyNotice.submitted_for_issuance_at.is_not(None)))) or False


def scope_of(db: Session, user: User, case_id: uuid.UUID) -> Scope:
    perms = permissions_for(user.role)
    kinds: set[str] = set()
    if Permission.SCRUTINY_WORK in perms and is_assigned(db, user.id, case_id):
        kinds.add("reviewer")
    if Permission.NOTICE_ISSUE in perms and has_notice_for_issuer(db, case_id):
        kinds.add("issuer")
    if Permission.SCRUTINY_ASSIGN in perms:
        kinds.add("coordinator")
    return Scope(frozenset(kinds))


def scope_for_application(db: Session, user: User, application_id: uuid.UUID) -> Scope:
    case_id = db.scalar(select(ScrutinyCase.id).where(ScrutinyCase.application_id == application_id))
    return scope_of(db, user, case_id) if case_id else Scope(frozenset())


def get_case(db: Session, user: User, case_id: uuid.UUID, request: Request | None, *, need: str | None = None, documents: bool = False) -> tuple[ScrutinyCase, Scope]:
    """Load a case the user may see; otherwise "not found" + an audit record. `need` demands a specific kind of scope."""
    case = db.get(ScrutinyCase, case_id, populate_existing=True)
    scope = scope_of(db, user, case_id) if case is not None else Scope(frozenset())
    ok = case is not None and bool(scope.kinds) and (need is None or need in scope) and (not documents or scope.can_read_documents)
    if not ok:
        if case is not None:
            audit.record(db, "scrutiny.access_denied", request=request, actor=user, target_type="scrutiny_case", target_id=case_id, detail={"need": need or "view", "documents": documents})
            db.commit()
        raise not_found("Scrutiny case")
    return case, scope


def can_read_application_documents(db: Session, user: User, app: Application) -> bool:
    """Reviewer-side read access to a submitted application's evidence (documents, extraction, previews, preliminary findings)."""
    if Permission.EVIDENCE_READ not in permissions_for(user.role):
        return False
    if scope_for_application(db, user, app.id).can_read_documents:
        return True
    from app import committee_access  # Phase 5: a committee coordinator of a stage the application is in, or an assigned member

    return committee_access.can_read_documents(db, user, app.id)
