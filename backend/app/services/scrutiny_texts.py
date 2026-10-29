"""The notification for each scrutiny event: who receives it and what it says.

Wording is deliberately plain and contains no internal reviewer notes. Messages to the institution state only what
the notice itself discloses. Every function is idempotent (see services/notifications.py).
"""

import uuid
from datetime import datetime

from sqlalchemy.orm import Session

from app.domain import NotificationEvent
from app.models_applications import Application
from app.models_scrutiny import DeficiencyNotice, ScrutinyCase, StageHandoff
from app.rbac import Role
from app.services import notifications as nt


def _when(d: datetime | None) -> str:
    return d.strftime("%d %b %Y %H:%M UTC") if d else ""


def case_entered_queue(db: Session, case: ScrutinyCase, app: Application) -> int:
    return nt.notify(
        db, NotificationEvent.CASE_ENTERED_QUEUE, nt.users_with_role(db, Role.SCRUTINY_COORDINATOR), kind="coordinator", scope_key=str(case.id),
        title=f"New application in the scrutiny queue: {app.reference}", body=f"{app.reference} was submitted and is waiting for an authorised assignment.",
        link=f"/app/scrutiny/{case.id}", application_id=app.id, case_id=case.id,
    )


def case_assigned(db: Session, case: ScrutinyCase, app: Application, assignee_id: uuid.UUID, demo: bool, assignment_id: uuid.UUID) -> int:
    from app.models import User

    user = db.get(User, assignee_id)
    note = " This is a DEMONSTRATION assignment, not the official AICTE selection." if demo else ""
    return nt.notify(
        db, NotificationEvent.CASE_ASSIGNED, [user] if user else [], kind="assignee", scope_key=str(assignment_id),
        title=f"Case assigned to you: {app.reference}", body=f"You were assigned to scrutinise {app.reference}.{note}", link=f"/app/scrutiny/{case.id}",
        application_id=app.id, case_id=case.id,
    )


def notice_awaiting_issuance(db: Session, notice: DeficiencyNotice, app: Application) -> int:
    return nt.notify(
        db, NotificationEvent.NOTICE_AWAITING_ISSUANCE, nt.users_with_role(db, Role.NOTICE_ISSUER), kind="issuer",
        scope_key=f"{notice.id}:{notice.row_version}", title=f"Notice {notice.reference} awaits an issuing decision",
        body=f"A deficiency notice for {app.reference} was prepared and needs an authorised issuer to inspect it.", link=f"/app/scrutiny/{notice.case_id}",
        application_id=app.id, case_id=notice.case_id, notice_id=notice.id,
    )


def notice_issued_notification(db: Session, notice: DeficiencyNotice) -> int:
    app = db.get(Application, notice.application_id)
    due = f" Response due: {_when(notice.response_due_at)}." if notice.response_due_at else " No response deadline has been established."
    return nt.notify(
        db, NotificationEvent.NOTICE_ISSUED, nt.institution_users(db, app.institution_id), kind="institution", scope_key=str(notice.id),
        title=f"Deficiency notice {notice.reference} issued", body=f"A deficiency notice was issued for application {app.reference}. Open it to see what is requested.{due}",
        link=f"/app/notices/{notice.id}", application_id=app.id, case_id=notice.case_id, notice_id=notice.id,
    )


def response_submitted(db: Session, case: ScrutinyCase, notice: DeficiencyNotice, app: Application) -> int:
    return nt.notify(
        db, NotificationEvent.RESPONSE_SUBMITTED, nt.users_with_role(db, Role.SCRUTINY_COORDINATOR), kind="coordinator", scope_key=str(notice.id),
        title=f"Response received for {notice.reference}", body=f"The institution responded to {notice.reference} ({app.reference}). The case is waiting for re-scrutiny.",
        link=f"/app/scrutiny/{case.id}", application_id=app.id, case_id=case.id, notice_id=notice.id,
    )


def ready_for_rescrutiny(db: Session, case: ScrutinyCase, notice: DeficiencyNotice, app: Application) -> int:
    reviewers = nt.assigned_reviewers(db, case.id)
    kind = "assignee"
    if not reviewers:  # nobody is assigned: the coordinators must see it instead
        reviewers, kind = nt.users_with_role(db, Role.SCRUTINY_COORDINATOR), "coordinator"
    return nt.notify(
        db, NotificationEvent.READY_FOR_RESCRUTINY, reviewers, kind=kind, scope_key=str(notice.id), title=f"Ready for re-scrutiny: {app.reference}",
        body=f"The response to {notice.reference} is in. Re-scrutiny examines the corrected evidence; it is not an automatic pass.", link=f"/app/scrutiny/{case.id}",
        application_id=app.id, case_id=case.id, notice_id=notice.id,
    )


def scrutiny_completed(db: Session, case: ScrutinyCase, app: Application) -> int:
    return nt.notify(
        db, NotificationEvent.SCRUTINY_COMPLETED, nt.users_with_role(db, Role.SCRUTINY_COORDINATOR), kind="coordinator", scope_key=str(case.id),
        title=f"Scrutiny recorded as complete: {app.reference}", body="Scrutiny is complete. This is not an approval; the case moves to its next configured stage.",
        link=f"/app/scrutiny/{case.id}", application_id=app.id, case_id=case.id,
    )


def handed_off(db: Session, case: ScrutinyCase, app: Application, handoff: StageHandoff) -> int:
    from app.domain import committee_stage_for

    where = handoff.to_stage_label or "(none configured)"
    if handoff.status == "QUEUED" and committee_stage_for(handoff.to_stage_key):
        tail = " That stage has been activated."
    elif handoff.status == "AWAITING_DETERMINATION":
        tail = " A coordinator must determine whether it applies."
    else:
        tail = " That stage's workflow is not available yet."
    return nt.notify(
        db, NotificationEvent.HANDED_OFF, nt.users_with_role(db, Role.SCRUTINY_COORDINATOR), kind="coordinator", scope_key=f"{case.id}:{handoff.status}:{handoff.to_stage_key}",
        title=f"Hand-off recorded: {app.reference}", body=f"Next stage: {where} ({handoff.status.replace('_', ' ').lower()}).{tail}",
        link=f"/app/scrutiny/{case.id}", application_id=app.id, case_id=case.id,
    )
