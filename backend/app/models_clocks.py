"""Persistent deadline clocks and the durable work queue that raises alerts for them (Phase 7).

A clock records WHAT is being timed, from WHICH trigger, under WHICH policy version, and what happened to it. It never contains a duration of
its own invention: `due_at` is either computed from a usable policy of the application's frozen rule version, specified (with a citation) by an
authorised person, or absent. Once created, a clock's trigger, start, due date and policy are fixed; the only things that move are its status,
its accumulated approved pause time, and its completion record.
"""

import uuid
from datetime import datetime

from sqlalchemy import JSON, CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models import new_id, utcnow

_KINDS = "'OFFICER_STAGE','INSTITUTION_RESPONSE','EVC','APPEAL','COMPLIANCE','DOCUMENT_VALIDITY'"
_TYPES = "'STATUTORY','INTERNAL_SLA','SPECIFIED','VALIDITY'"
_STATUSES = "'RUNNING','PAUSED','COMPLETED','CANCELLED','NOT_ESTABLISHED'"
_WORK = "'PENDING','RUNNING','DONE','SKIPPED_STALE','RETRY_WAIT','FAILED_EXHAUSTED','CANCELLED'"


class Clock(Base):
    __tablename__ = "clocks"
    __table_args__ = (
        CheckConstraint(f"kind IN ({_KINDS})", name="ck_clock_kind"),
        CheckConstraint(f"deadline_type IN ({_TYPES})", name="ck_clock_type"),
        CheckConstraint(f"status IN ({_STATUSES})", name="ck_clock_status"),
        # a date exists only when the clock is running / paused / finished; a NOT_ESTABLISHED clock never carries one
        CheckConstraint("status <> 'NOT_ESTABLISHED' OR due_at IS NULL", name="ck_clock_no_invented_date"),
        UniqueConstraint("subject_type", "subject_id", "policy_key", name="uq_clock_subject_policy"),
        Index("ix_clocks_status_due", "status", "due_at"),
        Index("ix_clocks_institution", "institution_id", "kind"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    kind: Mapped[str] = mapped_column(String(24))
    deadline_type: Mapped[str] = mapped_column(String(14))
    subject_type: Mapped[str] = mapped_column(String(24))  # notice | decision_case | obligation | committee_case | scrutiny_case | validity_version
    subject_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    policy_key: Mapped[str] = mapped_column(String(80))  # distinct clocks on one subject never share a key: a statutory and an internal clock stay separate rows
    label: Mapped[str] = mapped_column(String(300))
    application_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("applications.id"), index=True)
    institution_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("institutions.id"))
    rule_version_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("rule_versions.id"))  # the application's frozen version; never rebound
    trigger_event: Mapped[str] = mapped_column(String(200))
    trigger_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    start_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # the ORIGINAL due date; approved pause time is kept apart in paused_seconds
    paused_seconds: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16))
    not_established_reason: Mapped[str | None] = mapped_column(Text)
    policy_snapshot: Mapped[dict] = mapped_column(JSON)  # the policy as it was when the clock was created: duration, source, verification, alert schedule, pause rule
    responsible_role: Mapped[str | None] = mapped_column(String(32))
    row_version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completion_reason: Mapped[str | None] = mapped_column(String(80))
    completion_detail: Mapped[dict | None] = mapped_column(JSON)


class ClockSegment(Base):
    """One approved pause. Created only when the clock's own policy permits pausing; `resumed_at` is set when it ends."""

    __tablename__ = "clock_segments"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    clock_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("clocks.id"), index=True)
    paused_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    resumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reason: Mapped[str] = mapped_column(Text)
    reference: Mapped[str | None] = mapped_column(String(300))
    paused_by_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    resumed_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    permitted_by: Mapped[dict] = mapped_column(JSON)  # the policy clause that allowed it


class ClockEvent(Base):
    """Completion history and every state change of a clock. Append-only."""

    __tablename__ = "clock_events"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    clock_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("clocks.id"), index=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(40))
    from_status: Mapped[str | None] = mapped_column(String(16))
    to_status: Mapped[str | None] = mapped_column(String(16))
    detail: Mapped[dict | None] = mapped_column(JSON)


class ScheduledWork(Base):
    """A pending alert, stored durably. Claimed with a compare-and-set lease; a crashed worker's lease expires and the row runs again."""

    __tablename__ = "scheduled_work"
    __table_args__ = (
        CheckConstraint(f"status IN ({_WORK})", name="ck_work_status"),
        CheckConstraint("alert_kind IN ('PRE_DUE','DUE','OVERDUE')", name="ck_work_alert_kind"),
        Index("ix_work_claim", "status", "run_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    dedupe_key: Mapped[str] = mapped_column(String(160), unique=True)  # clock + alert + the due date it was planned for: planning it again is a no-op
    clock_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("clocks.id"), index=True)
    alert_kind: Mapped[str] = mapped_column(String(10))
    offset_hours: Mapped[int] = mapped_column(Integer, default=0)
    planned_for_due: Mapped[datetime] = mapped_column(DateTime(timezone=True))  # the effective due date this alert was planned against
    run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(18), default="PENDING")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=5)
    locked_by: Mapped[str | None] = mapped_column(String(80))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(200))  # an error CODE, never content
    outcome: Mapped[str | None] = mapped_column(String(40))  # sent | skipped_<why>
    recipients: Mapped[list | None] = mapped_column(JSON)  # [{user_id, kind}] actually notified
    policy_ref: Mapped[dict | None] = mapped_column(JSON)
    delivery: Mapped[str | None] = mapped_column(String(20))  # STORED_IN_APP when stored; no email / SMS channel exists, so none is ever claimed
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
