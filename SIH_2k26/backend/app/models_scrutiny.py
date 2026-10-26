"""Authorised scrutiny, formal deficiency notices, institution responses and hand-off (Phase 4).

None of these tables touches `applications` or a submitted application's documents: scrutiny state is
its own record, so the Phase 2/3 immutability guarantees on submitted applications keep holding.
Which fields are institution-facing and which are internal is explicit in the column names:
`description` / `review_notes` are internal; `institution_text` and notice items are what the
institution sees.
"""

import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models import User, new_id, utcnow

_CASE_STATES = "'AWAITING_ASSIGNMENT','READY_FOR_SCRUTINY','UNDER_SCRUTINY','AWAITING_NOTICE_DECISION','AWAITING_RESPONSE','AWAITING_RESCRUTINY','UNDER_RESCRUTINY','SCRUTINY_COMPLETED','CONFIGURATION_EXCEPTION'"


def _where(cond: str) -> dict:
    return {"postgresql_where": text(cond), "sqlite_where": text(cond)}


class ScrutinyCase(Base):
    """One per submitted application. Holds the scrutiny lifecycle; never the application's own status."""

    __tablename__ = "scrutiny_cases"
    __table_args__ = (
        CheckConstraint(f"state IN ({_CASE_STATES})", name="ck_case_state"),
        Index("ix_cases_state", "state"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    application_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("applications.id"), unique=True)
    rule_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("rule_versions.id"))  # copied from the application; never rebound
    state: Mapped[str] = mapped_column(String(32), default="AWAITING_ASSIGNMENT")
    row_version: Mapped[int] = mapped_column(Integer, default=1)
    exception_code: Mapped[str | None] = mapped_column(String(60))
    exception_detail: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    state_changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    completion: Mapped[dict | None] = mapped_column(JSON)  # what was resolved / handled / carried forward when scrutiny completed

    rounds: Mapped[list["ScrutinyRound"]] = relationship(order_by="ScrutinyRound.number", back_populates="case")
    assignments: Mapped[list["ScrutinyAssignment"]] = relationship(order_by="ScrutinyAssignment.created_at", back_populates="case")


class ScrutinyRound(Base):
    """Initial scrutiny, then one re-scrutiny round per notice response. Earlier rounds are never overwritten."""

    __tablename__ = "scrutiny_rounds"
    __table_args__ = (
        UniqueConstraint("case_id", "number"),
        CheckConstraint("kind IN ('INITIAL','RE_SCRUTINY')", name="ck_round_kind"),
        CheckConstraint("status IN ('OPEN','NOTICE_ISSUED','COMPLETED')", name="ck_round_status"),
        Index("uq_open_round_per_case", "case_id", unique=True, **_where("status = 'OPEN'")),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("scrutiny_cases.id"), index=True)
    number: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(12))
    status: Mapped[str] = mapped_column(String(14), default="OPEN")
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # when a reviewer began work in the round
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    triggered_by_notice_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("deficiency_notices.id", use_alter=True, name="fk_round_notice"))
    review_notes: Mapped[str | None] = mapped_column(Text)  # INTERNAL: the reviewer's working notes for this round
    review_notes_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    review_notes_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    outcome: Mapped[str | None] = mapped_column(String(40))
    outcome_reason: Mapped[str | None] = mapped_column(Text)
    row_version: Mapped[int] = mapped_column(Integer, default=1)

    case: Mapped[ScrutinyCase] = relationship(back_populates="rounds")


class ScrutinyAssignment(Base):
    """Who may work the case, who or what made that assignment, and under which configuration."""

    __tablename__ = "scrutiny_assignments"
    __table_args__ = (
        CheckConstraint("source IN ('DEMO_MANUAL','OFFICIAL_SELECTION')", name="ck_assignment_source"),
        Index("uq_active_assignment", "case_id", "user_id", unique=True, **_where("revoked_at IS NULL")),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("scrutiny_cases.id"), index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    source: Mapped[str] = mapped_column(String(24))
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)  # a demonstration assignment is never the official selection
    made_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))  # null for a result delivered by an authorised interface
    made_by_label: Mapped[str] = mapped_column(String(320))
    selection_ref: Mapped[dict | None] = mapped_column(JSON)  # mode, rule version, external reference where one exists
    reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    revoked_reason: Mapped[str | None] = mapped_column(Text)

    case: Mapped[ScrutinyCase] = relationship(back_populates="assignments")
    user: Mapped[User] = relationship(foreign_keys=[user_id])


class ScrutinyFinding(Base):
    """A scrutiny reviewer's conclusion about one issue. Not produced by OCR; not a notice until an issuer issues one."""

    __tablename__ = "scrutiny_findings"
    __table_args__ = (
        UniqueConstraint("case_id", "sequence"),
        CheckConstraint("state IN ('OPEN','NOTICED','UNRESOLVED','RESOLVED','NOT_SUPPORTED')", name="ck_sfinding_state"),
        CheckConstraint("visibility IN ('INTERNAL_ONLY','ELIGIBLE_FOR_NOTICE')", name="ck_sfinding_visibility"),
        CheckConstraint("source_ref_status IN ('ESTABLISHED','NEEDS_VERIFICATION')", name="ck_sfinding_source_status"),
        # an established source is always an actual reference chosen from the case's frozen rule version
        CheckConstraint("(source_ref_status = 'ESTABLISHED') = (source_ref IS NOT NULL)", name="ck_sfinding_source_consistent"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("scrutiny_cases.id"), index=True)
    application_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("applications.id"), index=True)
    round_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("scrutiny_rounds.id"))  # the round in which it was recorded
    sequence: Mapped[int] = mapped_column(Integer)
    item_key: Mapped[str | None] = mapped_column(String(80))
    document_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("application_documents.id"))  # the exact version examined
    page_number: Mapped[int | None] = mapped_column(Integer)
    issue_type: Mapped[str] = mapped_column(String(40))
    description: Mapped[str] = mapped_column(Text)  # INTERNAL reviewer description
    institution_text: Mapped[str | None] = mapped_column(Text)  # the only wording eligible for a notice
    requested_correction: Mapped[str | None] = mapped_column(Text)  # what the institution is asked to supply (eligible for a notice)
    source_ref: Mapped[dict | None] = mapped_column(JSON(none_as_null=True))  # SQL NULL, not JSON null: the check constraint relies on it
    source_ref_status: Mapped[str] = mapped_column(String(20), default="NEEDS_VERIFICATION")
    preliminary_finding_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("preliminary_findings.id"))
    visibility: Mapped[str] = mapped_column(String(22), default="INTERNAL_ONLY")
    state: Mapped[str] = mapped_column(String(14), default="OPEN")
    state_reason: Mapped[str | None] = mapped_column(Text)
    created_by_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    row_version: Mapped[int] = mapped_column(Integer, default=1)

    created_by: Mapped[User] = relationship(foreign_keys=[created_by_id])


class FindingEvent(Base):
    """Append-only history of one finding (which round, who, what, why)."""

    __tablename__ = "scrutiny_finding_events"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    finding_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("scrutiny_findings.id"), index=True)
    round_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("scrutiny_rounds.id"))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(30))
    from_state: Mapped[str | None] = mapped_column(String(14))
    to_state: Mapped[str | None] = mapped_column(String(14))
    reason: Mapped[str | None] = mapped_column(Text)

    actor: Mapped[User | None] = relationship()


class DeficiencyNotice(Base):
    """A formal deficiency notice. DRAFT and IN_REVIEW are internal; only an authorised human action issues one."""

    __tablename__ = "deficiency_notices"
    __table_args__ = (
        UniqueConstraint("application_id", "sequence"),
        CheckConstraint("status IN ('DRAFT','IN_REVIEW','ISSUED','RESPONSE_RECEIVED','CLOSED','WITHDRAWN')", name="ck_notice_status"),
        # Issued means issued by someone, at some time, with a recorded confirmation and frozen content.
        CheckConstraint(
            "(status IN ('ISSUED','RESPONSE_RECEIVED','CLOSED')) = (issued_at IS NOT NULL AND issued_by_id IS NOT NULL AND content_hash IS NOT NULL AND content_snapshot IS NOT NULL)",
            name="ck_notice_issue_consistent",
        ),
        Index("uq_one_notice_in_preparation", "case_id", unique=True, **_where("status IN ('DRAFT','IN_REVIEW')")),
        Index("uq_one_notice_awaiting_closure", "case_id", unique=True, **_where("status IN ('ISSUED','RESPONSE_RECEIVED')")),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    reference: Mapped[str] = mapped_column(String(24), unique=True)  # e.g. DN-7K3Q-9XMD
    application_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("applications.id"), index=True)
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("scrutiny_cases.id"), index=True)
    round_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("scrutiny_rounds.id"))  # the round whose findings it carries
    sequence: Mapped[int] = mapped_column(Integer)  # 1, 2, ... per application; a follow-up notice never replaces an earlier one
    previous_notice_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("deficiency_notices.id"))
    status: Mapped[str] = mapped_column(String(18), default="DRAFT")
    row_version: Mapped[int] = mapped_column(Integer, default=1)

    summary: Mapped[str] = mapped_column(Text, default="")  # institution-facing explanation
    prepared_by_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    submitted_for_issuance_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    returned_reason: Mapped[str | None] = mapped_column(Text)  # INTERNAL: why an issuer sent it back
    withdrawn_reason: Mapped[str | None] = mapped_column(Text)

    issued_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    issuer_role: Mapped[str | None] = mapped_column(String(40))
    issued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    issue_reason: Mapped[str | None] = mapped_column(Text)  # INTERNAL: the issuer's recorded reason
    issue_confirmation: Mapped[dict | None] = mapped_column(JSON)  # {confirmed_content_hash, confirmed_at, acknowledged_unsourced}
    authority: Mapped[dict | None] = mapped_column(JSON)  # the capability that authorised issuance and its (unverified) mapping
    idempotency_key: Mapped[str | None] = mapped_column(String(80), unique=True)
    policy_resolution: Mapped[dict | None] = mapped_column(JSON)  # how the response period was (or was not) established
    response_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # only ever from a usable policy
    template: Mapped[dict | None] = mapped_column(JSON)  # {key, label, demo}
    content_snapshot: Mapped[dict | None] = mapped_column(JSON(none_as_null=True))  # exactly what the institution was shown
    content_hash: Mapped[str | None] = mapped_column(String(64))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    items: Mapped[list["NoticeItem"]] = relationship(order_by="NoticeItem.position", back_populates="notice", cascade="all, delete-orphan")
    prepared_by: Mapped[User] = relationship(foreign_keys=[prepared_by_id])
    issued_by: Mapped[User | None] = relationship(foreign_keys=[issued_by_id])


class NoticeItem(Base):
    """One requested correction. Institution-facing text only; immutable once the notice leaves DRAFT."""

    __tablename__ = "notice_items"
    __table_args__ = (UniqueConstraint("notice_id", "position"), CheckConstraint("source_ref_status IN ('ESTABLISHED','NEEDS_VERIFICATION')", name="ck_nitem_source_status"))

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    notice_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("deficiency_notices.id"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    finding_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("scrutiny_findings.id"), index=True)
    item_key: Mapped[str | None] = mapped_column(String(80))  # the checklist requirement a corrected file will be filed under
    document_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("application_documents.id"))  # the version the issue concerns
    document_version: Mapped[int | None] = mapped_column(Integer)
    page_number: Mapped[int | None] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(300))
    description: Mapped[str] = mapped_column(Text)
    requested_correction: Mapped[str] = mapped_column(Text)
    source_ref: Mapped[dict | None] = mapped_column(JSON)
    source_ref_status: Mapped[str] = mapped_column(String(20), default="NEEDS_VERIFICATION")

    notice: Mapped[DeficiencyNotice] = relationship(back_populates="items")


class NoticeResponse(Base):
    """The institution's single response to one issued notice (a further round needs a further notice)."""

    __tablename__ = "notice_responses"
    __table_args__ = (CheckConstraint("status IN ('DRAFT','SUBMITTED')", name="ck_response_status"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    notice_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("deficiency_notices.id"), unique=True)
    application_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("applications.id"), index=True)
    institution_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("institutions.id"), index=True)
    status: Mapped[str] = mapped_column(String(10), default="DRAFT")
    explanation: Mapped[str] = mapped_column(Text, default="")
    row_version: Mapped[int] = mapped_column(Integer, default=1)
    created_by_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    submitted_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    idempotency_key: Mapped[str | None] = mapped_column(String(80))

    items: Mapped[list["NoticeResponseItem"]] = relationship(back_populates="response", cascade="all, delete-orphan")
    created_by: Mapped[User] = relationship(foreign_keys=[created_by_id])
    submitted_by: Mapped[User | None] = relationship(foreign_keys=[submitted_by_id])


class NoticeResponseItem(Base):
    __tablename__ = "notice_response_items"
    __table_args__ = (UniqueConstraint("response_id", "notice_item_id"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    response_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("notice_responses.id"), index=True)
    notice_item_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("notice_items.id"))
    explanation: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    response: Mapped[NoticeResponse] = relationship(back_populates="items")
    documents: Mapped[list["ResponseDocument"]] = relationship(order_by="ResponseDocument.linked_at", cascade="all, delete-orphan")


class ResponseDocument(Base):
    """Links an exact document version, uploaded as corrected evidence, to the notice item it answers."""

    __tablename__ = "notice_response_documents"
    __table_args__ = (UniqueConstraint("response_item_id", "document_id"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    response_item_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("notice_response_items.id"), index=True)
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("application_documents.id"))
    linked_by_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    linked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class StageHandoff(Base):
    """Where a case goes after scrutiny. A record for the future module to pick up; no later module runs in Phase 4."""

    __tablename__ = "stage_handoffs"
    __table_args__ = (CheckConstraint("status IN ('QUEUED','AWAITING_DETERMINATION','NO_FURTHER_STAGE','EXCEPTION')", name="ck_handoff_status"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("scrutiny_cases.id"), unique=True)
    application_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("applications.id"), index=True)
    from_stage_key: Mapped[str | None] = mapped_column(String(80))
    to_stage_key: Mapped[str | None] = mapped_column(String(80))
    to_stage_label: Mapped[str | None] = mapped_column(String(300))
    status: Mapped[str] = mapped_column(String(24))
    route_basis: Mapped[dict | None] = mapped_column(JSON)  # rule version + route entries + clause references this was read from
    reason: Mapped[str | None] = mapped_column(Text)  # why it awaits determination / is an exception
    determined_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    determined_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    determination_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ScrutinyEvent(Base):
    """Append-only case history. `institution_visible` events (and only those) appear on the institution's timeline."""

    __tablename__ = "scrutiny_events"
    __table_args__ = (Index("ix_sevents_case_at", "case_id", "at"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("scrutiny_cases.id"))
    application_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("applications.id"), index=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    actor_label: Mapped[str] = mapped_column(String(320))  # a name kept even if the account later changes
    action: Mapped[str] = mapped_column(String(40))
    object_type: Mapped[str | None] = mapped_column(String(30))
    object_id: Mapped[str | None] = mapped_column(String(64))
    from_state: Mapped[str | None] = mapped_column(String(32))
    to_state: Mapped[str | None] = mapped_column(String(32))
    reason: Mapped[str | None] = mapped_column(Text)
    detail: Mapped[dict | None] = mapped_column(JSON)
    institution_visible: Mapped[bool] = mapped_column(Boolean, default=False)

    actor: Mapped[User | None] = relationship()


class Notification(Base):
    """Persistent in-app notification. `dedupe_key` makes event creation idempotent. No external channel exists."""

    __tablename__ = "notifications"
    __table_args__ = (Index("ix_notifications_user_unread", "recipient_user_id", "read_at"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    event_type: Mapped[str] = mapped_column(String(40))
    dedupe_key: Mapped[str] = mapped_column(String(200), unique=True)
    recipient_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    recipient_kind: Mapped[str] = mapped_column(String(24))  # why this user: the role or relationship that targeted them
    application_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("applications.id"))
    case_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("scrutiny_cases.id"))
    notice_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("deficiency_notices.id"))
    committee_case_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("committee_cases.id"))  # Phase 5: the EVC / SHC case it concerns
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(String(600))  # never contains internal reviewer notes
    link: Mapped[str | None] = mapped_column(String(200))  # in-app path
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivery_state: Mapped[str] = mapped_column(String(20), default="STORED_IN_APP")  # what is true: it is stored and visible in the app
    external_delivery: Mapped[str] = mapped_column(String(20), default="NOT_CONFIGURED")  # no email / SMS provider exists
