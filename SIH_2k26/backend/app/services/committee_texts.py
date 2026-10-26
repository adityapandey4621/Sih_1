"""The notification for each committee event: who receives it and what it says.

Plain wording, no internal notes, and no claim that anything was sent outside the application (there is no email or SMS provider).
Every function is idempotent through the notification dedupe key (services/notifications.py), so a retried request or a re-run
reconciliation never produces a duplicate.
"""

import uuid
from datetime import datetime

from sqlalchemy.orm import Session

from app.domain import CommitteeEvent, CommitteeStage
from app.models_applications import Application
from app.models_committee import CommitteeCase, CommitteeSchedule
from app.rbac import Role
from app.services import notifications as nt

_COORD = {CommitteeStage.EVC: Role.EVC_COORDINATOR, CommitteeStage.SHC: Role.SHC_COORDINATOR}
_NAME = {CommitteeStage.EVC: "expert visit (EVC)", CommitteeStage.SHC: "SHC compliance / hearing"}
_PATH = {CommitteeStage.EVC: "evc", CommitteeStage.SHC: "shc"}


def _when(d: datetime | None) -> str:
    return d.strftime("%d %b %Y %H:%M UTC") if d else ""


def _stage(case: CommitteeCase) -> CommitteeStage:
    return CommitteeStage(case.stage)


def _link(case: CommitteeCase) -> str:
    return f"/app/{_PATH[_stage(case)]}/{case.id}"


def _send(db: Session, event: CommitteeEvent, recipients, kind: str, scope: str, title: str, body: str, link: str | None, case: CommitteeCase, app: Application) -> int:
    return nt.notify(db, event, recipients, kind=kind, scope_key=scope, title=title, body=body, link=link, application_id=app.id, committee_case_id=case.id)  # type: ignore[arg-type]


def coordinators(db: Session, case: CommitteeCase):
    return nt.users_with_role(db, _COORD[_stage(case)])


def stage_activated(db: Session, case: CommitteeCase, app: Application) -> int:
    st = _stage(case)
    n = _send(db, CommitteeEvent.STAGE_ACTIVATED, coordinators(db, case), "coordinator", str(case.id), f"{st.value} required: {app.reference}",
              f"The approved route requires the {_NAME[st]} stage for {app.reference}. It is waiting for the authorised process to begin.", _link(case), case, app)
    if st == CommitteeStage.EVC:
        n += _send(db, CommitteeEvent.STAGE_ACTIVATED, nt.institution_users(db, app.institution_id), "institution", str(case.id), f"An expert visit is required: {app.reference}",
                   "An expert visit is part of the process for your application. You will be told here when it is scheduled. This is not a decision.", f"/app/applications/{app.id}", case, app)
    return n


def selection_requested(db: Session, case: CommitteeCase, app: Application, request_ref: str, demo: bool) -> int:
    note = " (DEMONSTRATION: not the official selection)" if demo else ""
    return _send(db, CommitteeEvent.SELECTION_REQUESTED, coordinators(db, case), "coordinator", request_ref, f"Selection requested{note}: {app.reference}",
                 f"A selection of committee members was requested for {app.reference} ({request_ref}).", _link(case), case, app)


def selection_recorded(db: Session, case: CommitteeCase, app: Application, request_ref: str, member_ids: list[uuid.UUID], demo: bool) -> int:
    from app.models import User

    note = " This is a DEMONSTRATION selection, not the official AICTE process." if demo else ""
    n = _send(db, CommitteeEvent.SELECTION_RECORDED, coordinators(db, case), "coordinator", request_ref, f"Selection recorded: {app.reference}",
              f"{len(member_ids)} member(s) were recorded for {app.reference}.{note}", _link(case), case, app)
    members = [u for u in (db.get(User, i) for i in member_ids) if u]
    n += _send(db, CommitteeEvent.SELECTION_RECORDED, members, "assignee", f"{request_ref}:member", f"Case assigned to you: {app.reference}",
               f"You were assigned to the {_NAME[_stage(case)]} for {app.reference}.{note}", _link(case), case, app)
    return n


def selection_failed(db: Session, case: CommitteeCase, app: Application, request_ref: str, why: str) -> int:
    return _send(db, CommitteeEvent.SELECTION_FAILED, coordinators(db, case), "coordinator", request_ref, f"Selection did not complete: {app.reference}",
                 f"The selection request {request_ref} did not produce a usable result ({why}). No members were assigned.", _link(case), case, app)


def schedule_event(db: Session, case: CommitteeCase, app: Application, sch: CommitteeSchedule, event: CommitteeEvent, headline: str) -> int:
    """To the institution, the assigned members and the coordinators. `headline` e.g. "Visit proposed"."""
    what = "Visit" if sch.kind == "VISIT" else "Hearing"
    body = f"{what} for {app.reference}: {_when(sch.start_at)} ({sch.timezone}), mode {sch.mode.lower()}."
    scope = f"{sch.id}:{sch.status}"
    n = _send(db, event, coordinators(db, case), "coordinator", scope, f"{headline}: {app.reference}", body, _link(case), case, app)
    n += _send(db, event, nt.committee_members(db, case.id), "assignee", scope, f"{headline}: {app.reference}", body, _link(case), case, app)
    n += _send(db, event, nt.institution_users(db, app.institution_id), "institution", scope, f"{what} {headline.split()[-1].lower()}: {app.reference}", body + " Check the application page for details.", f"/app/applications/{app.id}", case, app)
    return n


def report_awaited(db: Session, case: CommitteeCase, app: Application) -> int:
    return _send(db, CommitteeEvent.VISIT_STARTED, coordinators(db, case), "coordinator", str(case.id), f"Visit under way, report awaited: {app.reference}",
                 f"The visit for {app.reference} has started. The report has not been submitted yet.", _link(case), case, app)


def report_submitted(db: Session, case: CommitteeCase, app: Application, report_id: uuid.UUID, version: int) -> int:
    st = _stage(case)
    what = "EVC report" if st == CommitteeStage.EVC else "SHC recommendation"
    return _send(db, CommitteeEvent.REPORT_SUBMITTED if st == CommitteeStage.EVC else CommitteeEvent.RECOMMENDATION_SUBMITTED, coordinators(db, case), "coordinator", str(report_id),
                 f"{what} submitted (version {version}): {app.reference}", f"A {what.lower()} was submitted for {app.reference}. It is a record for the next stage, not a decision.", _link(case), case, app)


def stage_completed(db: Session, case: CommitteeCase, app: Application) -> int:
    st = _stage(case)
    n = _send(db, CommitteeEvent.STAGE_COMPLETED, coordinators(db, case), "coordinator", str(case.id), f"{st.value} stage completed: {app.reference}",
              f"The {_NAME[st]} stage was recorded as complete. This is not an approval; the route decides what follows.", _link(case), case, app)
    if st == CommitteeStage.EVC:
        n += _send(db, CommitteeEvent.STAGE_COMPLETED, nt.institution_users(db, app.institution_id), "institution", str(case.id), f"Expert visit report available: {app.reference}",
                   "The expert visit stage is complete and its report is available on your application page. This is not a decision.", f"/app/applications/{app.id}", case, app)
    return n


def stage_blocked(db: Session, case: CommitteeCase, app: Application, code: str, message: str) -> int:
    return _send(db, CommitteeEvent.STAGE_BLOCKED, coordinators(db, case), "coordinator", f"{case.id}:{code}", f"{case.stage} blocked by configuration: {app.reference}", message, _link(case), case, app)


def compliance_requested(db: Session, case: CommitteeCase, app: Application, round_id: uuid.UUID, number: int, due: datetime | None) -> int:
    tail = f" Respond by {_when(due)}." if due else " No response deadline has been established."
    return _send(db, CommitteeEvent.COMPLIANCE_REQUESTED, nt.institution_users(db, app.institution_id), "institution", str(round_id), f"Compliance requested: {app.reference}",
                 f"The Standing Hearing Committee process asks your institution to address specific points (round {number}).{tail}", f"/app/compliance/{round_id}", case, app)


def compliance_submitted(db: Session, case: CommitteeCase, app: Application, round_id: uuid.UUID) -> int:
    n = _send(db, CommitteeEvent.COMPLIANCE_SUBMITTED, coordinators(db, case), "coordinator", str(round_id), f"Compliance received: {app.reference}",
              "The institution submitted its compliance response. It has been received, not accepted; the review follows.", _link(case), case, app)
    n += _send(db, CommitteeEvent.COMPLIANCE_SUBMITTED, nt.committee_members(db, case.id), "assignee", str(round_id), f"Compliance received: {app.reference}",
               "The institution submitted its compliance response for your review.", _link(case), case, app)
    return n


def determination_needed(db: Session, case: CommitteeCase, app: Application, reason: str) -> int:
    return _send(db, CommitteeEvent.NEXT_STAGE_AWAITS_DETERMINATION, coordinators(db, case), "coordinator", f"{case.id}:determination", f"Next stage needs a determination: {app.reference}", reason, _link(case), case, app)


def decision_handoff_ready(db: Session, case: CommitteeCase, app: Application) -> int:
    return _send(db, CommitteeEvent.DECISION_HANDOFF_READY, coordinators(db, case), "coordinator", f"{app.id}:decision", f"Ready for the decision stage: {app.reference}",
                 "Every stage the route required is complete. The case is ready for the later, authorised decision process. Nothing has been decided.", _link(case), case, app)
