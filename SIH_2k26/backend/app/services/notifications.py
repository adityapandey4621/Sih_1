"""Persistent in-app notifications.

* **Idempotent.** Each notification has a `dedupe_key` (event + subject + recipient); creating it again is a no-op,
  so a retried request or a re-run reconciliation never produces duplicates.
* **Isolated.** Each row is written inside a savepoint. If a notification cannot be stored, the change that
  caused it (a notice issued, a response received) is *not* undone: the failure is logged and audited, and
  `reconcile()` can re-create what is missing.
* **Truthful.** The only channel is the application itself. `delivery_state` says it is stored in the app and
  `external_delivery` says no email / SMS provider is configured. Nothing here claims an external message was sent.
"""

import logging
import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import audit
from app.domain import NOTICE_VISIBLE_TO_INSTITUTION, NotificationEvent
from app.models import User
from app.models_committee import CommitteeAssignment
from app.models_scrutiny import DeficiencyNotice, Notification, ScrutinyAssignment
from app.rbac import Role

log = logging.getLogger("app.notifications")


def users_with_role(db: Session, role: Role) -> list[User]:
    return list(db.scalars(select(User).where(User.role == role.value, User.is_active.is_(True)).order_by(User.email)))


def institution_users(db: Session, institution_id: uuid.UUID) -> list[User]:
    return list(db.scalars(select(User).where(User.institution_id == institution_id, User.role == Role.INSTITUTE_USER.value, User.is_active.is_(True)).order_by(User.email)))


def assigned_reviewers(db: Session, case_id: uuid.UUID) -> list[User]:
    return list(
        db.scalars(
            select(User).join(ScrutinyAssignment, ScrutinyAssignment.user_id == User.id)
            .where(ScrutinyAssignment.case_id == case_id, ScrutinyAssignment.revoked_at.is_(None), User.is_active.is_(True)).order_by(User.email)
        )
    )


def committee_members(db: Session, committee_case_id: uuid.UUID) -> list[User]:
    return list(
        db.scalars(
            select(User).join(CommitteeAssignment, CommitteeAssignment.user_id == User.id)
            .where(CommitteeAssignment.case_id == committee_case_id, CommitteeAssignment.revoked_at.is_(None), User.is_active.is_(True)).order_by(User.email)
        )
    )


def _insert(db: Session, row: Notification) -> None:
    """The single write point (tests replace it to simulate a storage failure)."""
    db.add(row)
    db.flush()


def notify(
    db: Session, event: NotificationEvent, recipients: list[User], *, kind: str, scope_key: str, title: str, body: str, link: str | None,
    application_id: uuid.UUID | None = None, case_id: uuid.UUID | None = None, notice_id: uuid.UUID | None = None, committee_case_id: uuid.UUID | None = None,
) -> int:
    """Create one notification per recipient. Returns how many were newly created."""
    created = 0
    for user in recipients:
        key = f"{event.value}:{scope_key}:{user.id}"
        if db.scalar(select(Notification.id).where(Notification.dedupe_key == key)) is not None:
            continue
        row = Notification(
            event_type=event.value, dedupe_key=key, recipient_user_id=user.id, recipient_kind=kind, application_id=application_id, case_id=case_id,
            notice_id=notice_id, committee_case_id=committee_case_id, title=title[:200], body=body[:600], link=link,
        )
        try:
            # SAVEPOINT on PostgreSQL (the production database): a failed notification rolls back to here and nothing else.
            # SQLite's driver cannot do savepoints reliably, so there it is a plain write; SQLite is used for tests only.
            if db.bind.dialect.name == "postgresql":
                with db.begin_nested():
                    _insert(db, row)
            else:
                _insert(db, row)
            created += 1
        except IntegrityError:
            continue  # created concurrently by the same event: exactly what idempotency is for
        except Exception as exc:  # noqa: BLE001 - a notification must never undo the change that triggered it
            log.error("notification not stored: event=%s error=%s", event.value, type(exc).__name__)
            audit.record(db, "notification.persist_failed", target_type="application", target_id=application_id, detail={"event": event.value, "error": type(exc).__name__})
    return created


# ---- reconciliation -----------------------------------------------------------------------------


def reconcile(db: Session) -> int:
    """Re-create institution notifications for issued notices whose notification is missing. Idempotent."""
    from app.services.scrutiny_texts import notice_issued_notification  # local: avoids an import cycle

    made = 0
    for notice in db.scalars(select(DeficiencyNotice).where(DeficiencyNotice.status.in_(NOTICE_VISIBLE_TO_INSTITUTION))):
        made += notice_issued_notification(db, notice)
    return made
