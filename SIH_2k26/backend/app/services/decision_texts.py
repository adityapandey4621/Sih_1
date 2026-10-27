"""The notification for each decision / ratification / appeal event: who receives it and what it says.

Plain wording. The institution is told what was recorded and what it may do; it is never told AICTE's portal was updated (see portal_sync.py).
Every function is idempotent through the notification dedupe key (services/notifications.py); the only channel is the application itself.
"""

import uuid
from datetime import datetime

from sqlalchemy.orm import Session

from app.domain import DecisionEvent
from app.models_applications import Application
from app.rbac import Role
from app.services import notifications as nt

LABEL = {"LOA": "Letter of Approval (LoA)", "LOD": "Letter of Deficiency (LoD)", "LOR": "Letter of Rejection (LoR)"}


def _when(d: datetime | None) -> str:
    return d.strftime("%d %b %Y %H:%M UTC") if d else ""


def _send(db: Session, event: DecisionEvent, recipients, kind: str, scope: str, title: str, body: str, link: str | None, app: Application) -> int:
    return nt.notify(db, event, recipients, kind=kind, scope_key=scope, title=title, body=body, link=link, application_id=app.id)  # type: ignore[arg-type]


def _staff(db: Session, *roles: Role):
    out, seen = [], set()
    for r in roles:
        for u in nt.users_with_role(db, r):
            if u.id not in seen:
                seen.add(u.id)
                out.append(u)
    return out


def case_opened(db: Session, case_id: uuid.UUID, app: Application) -> int:
    return _send(db, DecisionEvent.CASE_OPENED, _staff(db, Role.REGULATION_BUREAU, Role.EXECUTIVE_COMMITTEE), "decision", str(case_id), f"Ready for the decision stage: {app.reference}",
                 "Every stage the approved route required is complete. The Regulation Bureau's observations are needed before the Executive Committee decides.", f"/app/decisions/{case_id}", app)


def observation_recorded(db: Session, case_id: uuid.UUID, app: Application, obs_id: uuid.UUID) -> int:
    return _send(db, DecisionEvent.OBSERVATION_RECORDED, _staff(db, Role.EXECUTIVE_COMMITTEE), "decision", str(obs_id), f"Bureau observations recorded: {app.reference}",
                 "The Regulation Bureau's observations were recorded. The Executive Committee may now record its decision.", f"/app/decisions/{case_id}", app)


def decision_recorded(db: Session, case_id: uuid.UUID, app: Application, decision_id: uuid.UUID, version: int, corrected: bool) -> int:
    ev = DecisionEvent.DECISION_CORRECTED if corrected else DecisionEvent.DECISION_RECORDED
    word = "corrected (version %d)" % version if corrected else "recorded"
    return _send(db, ev, _staff(db, Role.REGULATION_BUREAU, Role.COUNCIL_MEMBER), "decision", str(decision_id), f"Executive Committee decision {word}: {app.reference}",
                 "The decision is recorded. The Regulation Bureau issues the official outcome; the Council ratifies where the configuration requires it. Nothing has reached the institution yet.", f"/app/decisions/{case_id}", app)


def outcome_issued(db: Session, case_id: uuid.UUID, app: Application, issuance_id: uuid.UUID, outcome: str, reference: str | None, corrected: bool) -> int:
    tail = f" Reference: {reference}." if reference else ""
    title = ("Corrected official outcome: " if corrected else "Official outcome issued: ") + app.reference
    n = _send(db, DecisionEvent.OUTCOME_ISSUED, nt.institution_users(db, app.institution_id), "institution", str(issuance_id), title,
              f"The Executive Committee's decision on your application is recorded: {LABEL.get(outcome, outcome)}.{tail} Open the application for the reasons and for whether you may appeal.", f"/app/applications/{app.id}", app)
    n += _send(db, DecisionEvent.OUTCOME_ISSUED, _staff(db, Role.EXECUTIVE_COMMITTEE, Role.COUNCIL_MEMBER), "decision", str(issuance_id), title, "The official outcome was issued to the institution in this system.", f"/app/decisions/{case_id}", app)
    return n


def ratification_recorded(db: Session, case_id: uuid.UUID, app: Application, council_id: uuid.UUID, outcome: str) -> int:
    return _send(db, DecisionEvent.RATIFICATION_RECORDED, _staff(db, Role.REGULATION_BUREAU, Role.EXECUTIVE_COMMITTEE), "decision", str(council_id), f"Council ratification recorded ({outcome.replace('_', ' ').lower()}): {app.reference}",
                 "The Council's step on the Executive Committee decision was recorded.", f"/app/decisions/{case_id}", app) + \
        _send(db, DecisionEvent.RATIFICATION_RECORDED, nt.institution_users(db, app.institution_id), "institution", str(council_id), f"Council step recorded: {app.reference}",
              f"The Council's step on the decision was recorded ({outcome.replace('_', ' ').lower()}). Open the application for the current official status.", f"/app/applications/{app.id}", app)


def appeal_submitted(db: Session, appeal_id: uuid.UUID, sac_case_id: uuid.UUID, app: Application) -> int:
    n = _send(db, DecisionEvent.APPEAL_SUBMITTED, nt.users_with_role(db, Role.SAC_COORDINATOR), "coordinator", str(appeal_id), f"Appeal received: {app.reference}",
              "An institution appealed against the Executive Committee's decision. Members must be assigned through the authorised selection step.", f"/app/sac/{sac_case_id}", app)
    n += _send(db, DecisionEvent.APPEAL_SUBMITTED, nt.institution_users(db, app.institution_id), "institution", str(appeal_id), f"Your appeal was received: {app.reference}",
               "Your appeal was recorded and passed to the Standing Appellate Committee process. Submitting an appeal does not decide it.", f"/app/applications/{app.id}", app)
    return n


def sac_ready_for_council(db: Session, appeal_id: uuid.UUID, case_id: uuid.UUID, app: Application) -> int:
    return _send(db, DecisionEvent.SAC_RECOMMENDATION_READY, _staff(db, Role.COUNCIL_MEMBER, Role.REGULATION_BUREAU), "decision", str(appeal_id), f"SAC recommendation awaits the Council: {app.reference}",
                 "The SAC stage is complete. Its recommendation is for the Council's decision; it is not that decision.", f"/app/decisions/{case_id}", app)


def appeal_decided(db: Session, appeal_id: uuid.UUID, app: Application, outcome: str) -> int:
    return _send(db, DecisionEvent.APPEAL_DECIDED, nt.institution_users(db, app.institution_id), "institution", str(appeal_id), f"Council decision on your appeal: {app.reference}",
                 f"The Council's decision on your appeal is recorded: {LABEL.get(outcome, outcome)}. Under the handbook it is final. Open the application for the reasons.", f"/app/applications/{app.id}", app)


def case_closed(db: Session, case_id: uuid.UUID, app: Application) -> int:
    return _send(db, DecisionEvent.CASE_CLOSED, nt.institution_users(db, app.institution_id), "institution", str(case_id), f"Decision stage complete: {app.reference}",
                 "No further decision-stage step is open for your application. Later stages are not part of this system yet.", f"/app/applications/{app.id}", app)


def sync_pending(db: Session, case_id: uuid.UUID, app: Application, sync_id: uuid.UUID) -> int:
    return _send(db, DecisionEvent.SYNC_PENDING, _staff(db, Role.REGULATION_BUREAU), "decision", f"{sync_id}", f"AICTE portal not yet updated: {app.reference}",
                 "The external synchronisation was not confirmed. It will be retried; AICTE's portal has not been updated.", f"/app/decisions/{case_id}", app)
