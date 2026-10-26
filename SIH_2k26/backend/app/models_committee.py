"""EVC and SHC committee processes, the route engine's records and the hand-off to the decision stage (Phase 5).

One `committee_cases` row per application and stage ("EVC" or "SHC"). The two processes keep their own state tables, services
and permissions; they share only the plumbing that is genuinely the same (selection requests, assignments, schedules,
evidence, versioned reports, an append-only event log). Nothing here touches `applications` or the scrutiny tables.

A report or a recommendation is a record, never a decision: no column or state here means "approved" or "rejected".
"""

import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models import User, new_id, utcnow

_STATES = (
    "'SELECTION_PENDING','SELECTION_REQUESTED','SCHEDULE_PENDING','SCHEDULED','VISIT_IN_PROGRESS','REPORT_SUBMITTED',"
    "'PREPARATION','AWAITING_COMPLIANCE','COMPLIANCE_RECEIVED','UNDER_REVIEW','RECOMMENDATION_SUBMITTED','COMPLETED'"
)


def _where(cond: str) -> dict:
    return {"postgresql_where": text(cond), "sqlite_where": text(cond)}


class CommitteePolicy(Base):
    """Versioned, source-referenced configuration for a committee stage: visit modes, composition, checklist, report structure,
    hearing and recommendation options. A case freezes a copy at activation. There is no OFFICIAL policy until its sources are verified."""

    __tablename__ = "committee_policies"
    __table_args__ = (
        UniqueConstraint("key", "version_number"),
        CheckConstraint("stage IN ('EVC','SHC')", name="ck_cpolicy_stage"),
        CheckConstraint("classification IN ('DEMO','OFFICIAL')", name="ck_cpolicy_class"),
        CheckConstraint("status IN ('published','retired')", name="ck_cpolicy_status"),
        Index("uq_one_published_policy", "stage", "classification", unique=True, **_where("status = 'published'")),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    stage: Mapped[str] = mapped_column(String(3))
    key: Mapped[str] = mapped_column(String(80))
    version_number: Mapped[int] = mapped_column(Integer, default=1)
    classification: Mapped[str] = mapped_column(String(10))
    status: Mapped[str] = mapped_column(String(10), default="published")
    title: Mapped[str] = mapped_column(String(300))
    content: Mapped[dict] = mapped_column(JSON)
    sources: Mapped[list] = mapped_column(JSON)  # [{title, clause_ref, verification_status}]
    content_hash: Mapped[str] = mapped_column(String(64))
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CommitteeCase(Base):
    __tablename__ = "committee_cases"
    __table_args__ = (
        UniqueConstraint("application_id", "stage"),  # one EVC case and one SHC case per application: activation is idempotent
        CheckConstraint("stage IN ('EVC','SHC')", name="ck_ccase_stage"),
        CheckConstraint(f"state IN ({_STATES})", name="ck_ccase_state"),
        Index("ix_ccases_stage_state", "stage", "state"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    application_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("applications.id"), index=True)
    stage: Mapped[str] = mapped_column(String(3))
    rule_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("rule_versions.id"))  # the application's frozen version; never rebound
    scrutiny_case_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("scrutiny_cases.id"))
    state: Mapped[str] = mapped_column(String(26))
    row_version: Mapped[int] = mapped_column(Integer, default=1)
    trigger: Mapped[dict] = mapped_column(JSON)  # what activated it: from-stage, route basis, determination, prior outcome, identified shortcomings
    policy_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("committee_policies.id"))
    policy_snapshot: Mapped[dict | None] = mapped_column(JSON)  # frozen copy; a later policy never changes a running case
    blockers: Mapped[list | None] = mapped_column(JSON)  # configuration problems that stop the next steps (never guessed around)
    activated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    state_changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    completion: Mapped[dict | None] = mapped_column(JSON)

    assignments: Mapped[list["CommitteeAssignment"]] = relationship(order_by="CommitteeAssignment.created_at", back_populates="case")


class SelectionRequest(Base):
    """A request for an authorised selection of committee members, and what came back. A failure is a recorded outcome, never a silent gap."""

    __tablename__ = "committee_selection_requests"
    __table_args__ = (
        CheckConstraint("provider IN ('DEMO_MANUAL','EXTERNAL_PORTAL')", name="ck_selreq_provider"),
        CheckConstraint("status IN ('REQUESTED','COMPLETED','FAILED','INCOMPLETE','CANCELLED')", name="ck_selreq_status"),
        Index("uq_open_selection_request", "case_id", unique=True, **_where("status = 'REQUESTED'")),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("committee_cases.id"), index=True)
    request_ref: Mapped[str] = mapped_column(String(40), unique=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(80), unique=True)
    provider: Mapped[str] = mapped_column(String(20))
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(12), default="REQUESTED")
    requested_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    result_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    result_source: Mapped[str | None] = mapped_column(String(320))  # the actor, or the external source that delivered the result
    external_ref: Mapped[str | None] = mapped_column(String(120))
    result: Mapped[dict | None] = mapped_column(JSON)  # {members: [{user_id, name}]}
    error_code: Mapped[str | None] = mapped_column(String(60))
    error_detail: Mapped[str | None] = mapped_column(Text)
    config_ref: Mapped[dict | None] = mapped_column(JSON)  # rule version + committee policy this was requested under


class CommitteeAssignment(Base):
    """A member's authorisation to work one case. Always traceable to the selection request that produced it."""

    __tablename__ = "committee_assignments"
    __table_args__ = (Index("uq_active_committee_assignment", "case_id", "user_id", unique=True, **_where("revoked_at IS NULL")),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("committee_cases.id"), index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    selection_request_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("committee_selection_requests.id"))
    source: Mapped[str] = mapped_column(String(20))
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    revoked_reason: Mapped[str | None] = mapped_column(Text)

    case: Mapped[CommitteeCase] = relationship(back_populates="assignments")
    user: Mapped[User] = relationship(foreign_keys=[user_id])


class CommitteeSchedule(Base):
    """A visit (EVC) or hearing (SHC) schedule. Rescheduling or cancelling closes the row and adds another: history is never overwritten."""

    __tablename__ = "committee_schedules"
    __table_args__ = (
        UniqueConstraint("case_id", "kind", "sequence"),
        CheckConstraint("kind IN ('VISIT','HEARING')", name="ck_sched_kind"),
        CheckConstraint("status IN ('PROPOSED','CONFIRMED','SUPERSEDED','CANCELLED','COMPLETED')", name="ck_sched_status"),
        CheckConstraint("mode IN ('PHYSICAL','ONLINE','HYBRID')", name="ck_sched_mode"),
        Index("uq_active_schedule", "case_id", "kind", unique=True, **_where("status IN ('PROPOSED','CONFIRMED')")),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("committee_cases.id"), index=True)
    kind: Mapped[str] = mapped_column(String(8))
    sequence: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(10), default="PROPOSED")
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))  # an instant (UTC)
    timezone: Mapped[str] = mapped_column(String(64))  # IANA name the date and time were entered in
    mode: Mapped[str] = mapped_column(String(10))
    mode_reason: Mapped[str | None] = mapped_column(Text)  # e.g. the circumstance that justifies a physical visit
    location: Mapped[str | None] = mapped_column(Text)
    access_details: Mapped[str | None] = mapped_column(Text)  # how to join online; never a secret or credential
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("committee_schedules.id"))
    change_reason: Mapped[str | None] = mapped_column(Text)
    policy_ref: Mapped[dict | None] = mapped_column(JSON)
    idempotency_key: Mapped[str | None] = mapped_column(String(80), unique=True)
    row_version: Mapped[int] = mapped_column(Integer, default=1)
    created_by_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    confirmed_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    closed_reason: Mapped[str | None] = mapped_column(Text)


class EvcObservation(Base):
    """One member's observation against one checklist item. `note` goes into the report; `internal_note` never leaves the committee."""

    __tablename__ = "evc_observations"
    __table_args__ = (
        UniqueConstraint("case_id", "item_key"),
        CheckConstraint("status IN ('OBSERVED','NOT_OBSERVED','EVIDENCE_UNAVAILABLE','UNCERTAIN')", name="ck_obs_status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("committee_cases.id"), index=True)
    item_key: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(22))
    note: Mapped[str] = mapped_column(Text, default="")
    internal_note: Mapped[str | None] = mapped_column(Text)
    shortcoming: Mapped[bool] = mapped_column(Boolean, default=False)  # the member flags something for the route's next determination; not a finding of non-compliance
    document_refs: Mapped[list | None] = mapped_column(JSON)  # [{document_id, version_number, page}] the observation relies on
    recorded_by_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    row_version: Mapped[int] = mapped_column(Integer, default=1)

    recorded_by: Mapped[User] = relationship(foreign_keys=[recorded_by_id])


class CommitteeEvidence(Base):
    """Private evidence for a visit: a stored file (same private storage as application documents) or a reference to material held elsewhere."""

    __tablename__ = "committee_evidence"
    __table_args__ = (
        CheckConstraint("kind IN ('FILE','LINK')", name="ck_cevid_kind"),
        CheckConstraint("(kind = 'FILE') = (storage_key IS NOT NULL)", name="ck_cevid_file_consistent"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("committee_cases.id"), index=True)
    item_key: Mapped[str | None] = mapped_column(String(80))
    kind: Mapped[str] = mapped_column(String(4))
    description: Mapped[str] = mapped_column(Text, default="")
    storage_key: Mapped[str | None] = mapped_column(String(64))
    original_filename: Mapped[str | None] = mapped_column(String(255))
    media_type: Mapped[str | None] = mapped_column(String(100))
    size_bytes: Mapped[int | None] = mapped_column(Integer)
    sha256: Mapped[str | None] = mapped_column(String(64))
    url: Mapped[str | None] = mapped_column(String(1000))  # recorded, never fetched or rendered as a link the system vouches for
    uploaded_by_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    removed_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))


class CommitteeReport(Base):
    """A versioned report (EVC report) or recommendation (SHC). A submitted version is immutable; a correction is a later version
    opened by an authorised coordinator. Neither is an approval or a decision."""

    __tablename__ = "committee_reports"
    __table_args__ = (
        UniqueConstraint("case_id", "kind", "version_number"),
        CheckConstraint("kind IN ('EVC_REPORT','SHC_RECOMMENDATION')", name="ck_creport_kind"),
        CheckConstraint("status IN ('DRAFT','SUBMITTED')", name="ck_creport_status"),
        CheckConstraint(
            "(status = 'SUBMITTED') = (submitted_at IS NOT NULL AND submitted_by_id IS NOT NULL AND content_hash IS NOT NULL AND source_snapshot IS NOT NULL)",
            name="ck_creport_submit_consistent",
        ),
        Index("uq_one_draft_report", "case_id", "kind", unique=True, **_where("status = 'DRAFT'")),
        Index("uq_report_idempotency", "case_id", "kind", "idempotency_key", unique=True, **_where("idempotency_key IS NOT NULL")),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("committee_cases.id"), index=True)
    kind: Mapped[str] = mapped_column(String(20))
    version_number: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(10), default="DRAFT")
    content: Mapped[dict] = mapped_column(JSON, default=dict)  # the author's text (sections); the evidence snapshot is added at submission
    row_version: Mapped[int] = mapped_column(Integer, default=1)
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("committee_reports.id"))
    amendment_reason: Mapped[str | None] = mapped_column(Text)
    amendment_authorised_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    created_by_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    submitted_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    idempotency_key: Mapped[str | None] = mapped_column(String(80))
    content_hash: Mapped[str | None] = mapped_column(String(64))
    source_snapshot: Mapped[dict | None] = mapped_column(JSON(none_as_null=True))  # documents + versions, evidence, observations, policy, schedule as submitted

    created_by: Mapped[User] = relationship(foreign_keys=[created_by_id])
    submitted_by: Mapped[User | None] = relationship(foreign_keys=[submitted_by_id])


class CommitteeEventRow(Base):
    """Append-only history of a committee case (and of the route engine). `institution_visible` events alone appear on the institution's timeline."""

    __tablename__ = "committee_events"
    __table_args__ = (Index("ix_cevents_case_at", "case_id", "at"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    stage: Mapped[str] = mapped_column(String(8))  # EVC | SHC | ROUTE
    case_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    application_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("applications.id"), index=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    actor_label: Mapped[str] = mapped_column(String(320))
    action: Mapped[str] = mapped_column(String(50))
    object_type: Mapped[str | None] = mapped_column(String(30))
    object_id: Mapped[str | None] = mapped_column(String(64))
    from_state: Mapped[str | None] = mapped_column(String(32))
    to_state: Mapped[str | None] = mapped_column(String(32))
    reason: Mapped[str | None] = mapped_column(Text)
    detail: Mapped[dict | None] = mapped_column(JSON)
    institution_visible: Mapped[bool] = mapped_column(Boolean, default=False)


# ---- SHC -----------------------------------------------------------------------------------------


class ShcRound(Base):
    """One request to the institution to address identified shortcomings. A further round is a further row, never an edit."""

    __tablename__ = "shc_compliance_rounds"
    __table_args__ = (
        UniqueConstraint("case_id", "number"),
        CheckConstraint("status IN ('OPEN','RESPONSE_RECEIVED','CLOSED')", name="ck_shcround_status"),
        Index("uq_one_open_shc_round", "case_id", unique=True, **_where("status = 'OPEN'")),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("committee_cases.id"), index=True)
    number: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(18), default="OPEN")
    instructions: Mapped[str] = mapped_column(Text, default="")  # institution-facing
    requested_by_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    policy_resolution: Mapped[dict | None] = mapped_column(JSON)  # how (whether) a response period was established
    response_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # only ever from a verified, configured policy
    response_received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ShcRequirement(Base):
    """One thing the institution is asked to address. Prepared by the coordinator; frozen once its round is requested."""

    __tablename__ = "shc_requirements"
    __table_args__ = (
        CheckConstraint("source_ref_status IN ('ESTABLISHED','NEEDS_VERIFICATION')", name="ck_shcreq_source_status"),
        CheckConstraint("(source_ref_status = 'ESTABLISHED') = (source_ref IS NOT NULL)", name="ck_shcreq_source_consistent"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("committee_cases.id"), index=True)
    round_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("shc_compliance_rounds.id"), index=True)  # null while being prepared
    position: Mapped[int] = mapped_column(Integer, default=0)
    title: Mapped[str] = mapped_column(String(300))
    description: Mapped[str] = mapped_column(Text)  # institution-facing
    requested_evidence: Mapped[str] = mapped_column(Text, default="")
    item_key: Mapped[str | None] = mapped_column(String(80))  # a checklist document requirement a compliance file is filed under
    origin: Mapped[dict | None] = mapped_column(JSON)  # {type: scrutiny_finding | evc_observation | manual, ...}
    source_ref: Mapped[dict | None] = mapped_column(JSON(none_as_null=True))
    source_ref_status: Mapped[str] = mapped_column(String(20), default="NEEDS_VERIFICATION")
    created_by_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    row_version: Mapped[int] = mapped_column(Integer, default=1)
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # only while still being prepared


class ShcResponse(Base):
    """The institution's response to one compliance round. Received, never 'accepted'."""

    __tablename__ = "shc_responses"
    __table_args__ = (CheckConstraint("status IN ('DRAFT','SUBMITTED')", name="ck_shcresp_status"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    round_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("shc_compliance_rounds.id"), unique=True)
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("committee_cases.id"), index=True)
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

    items: Mapped[list["ShcResponseItem"]] = relationship(back_populates="response", cascade="all, delete-orphan")


class ShcResponseItem(Base):
    __tablename__ = "shc_response_items"
    __table_args__ = (UniqueConstraint("response_id", "requirement_id"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    response_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("shc_responses.id"), index=True)
    requirement_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("shc_requirements.id"))
    explanation: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    response: Mapped[ShcResponse] = relationship(back_populates="items")
    documents: Mapped[list["ShcResponseDocument"]] = relationship(order_by="ShcResponseDocument.linked_at", cascade="all, delete-orphan")


class ShcResponseDocument(Base):
    """Links an exact document version, uploaded as compliance evidence, to the requirement it answers."""

    __tablename__ = "shc_response_documents"
    __table_args__ = (UniqueConstraint("response_item_id", "document_id"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    response_item_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("shc_response_items.id"), index=True)
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("application_documents.id"))
    linked_by_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    linked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ShcFinding(Base):
    """A reviewer's assessment of the evidence for a requirement. Separate from any recommendation and from any decision."""

    __tablename__ = "shc_findings"
    __table_args__ = (
        UniqueConstraint("case_id", "sequence"),
        CheckConstraint("assessment IN ('EVIDENCE_SUPPORTS','EVIDENCE_DOES_NOT_SUPPORT','INCONCLUSIVE')", name="ck_shcfind_assessment"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("committee_cases.id"), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    requirement_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("shc_requirements.id"))
    assessment: Mapped[str] = mapped_column(String(26))
    text: Mapped[str] = mapped_column(Text)  # the reasons
    created_by_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    row_version: Mapped[int] = mapped_column(Integer, default=1)

    created_by: Mapped[User] = relationship(foreign_keys=[created_by_id])


class ShcProceeding(Base):
    """What happened at a hearing or review sitting. Append-only."""

    __tablename__ = "shc_proceedings"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("committee_cases.id"), index=True)
    schedule_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("committee_schedules.id"))
    held_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    participants: Mapped[str] = mapped_column(Text, default="")
    text: Mapped[str] = mapped_column(Text)
    recorded_by_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    recorded_by: Mapped[User] = relationship(foreign_keys=[recorded_by_id])


# ---- route engine ----------------------------------------------------------------------------------


class RouteTransition(Base):
    """What the frozen route says comes after a completed EVC or SHC stage (after scrutiny the record is Phase 4's `stage_handoffs`)."""

    __tablename__ = "route_transitions"
    __table_args__ = (
        UniqueConstraint("application_id", "from_stage_key"),
        CheckConstraint("status IN ('QUEUED','AWAITING_DETERMINATION','NO_FURTHER_STAGE','EXCEPTION')", name="ck_rtrans_status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    application_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("applications.id"), index=True)
    from_stage_key: Mapped[str] = mapped_column(String(80))
    from_case_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("committee_cases.id"))
    to_stage_key: Mapped[str | None] = mapped_column(String(80))
    to_stage_label: Mapped[str | None] = mapped_column(String(300))
    status: Mapped[str] = mapped_column(String(24))
    route_basis: Mapped[dict | None] = mapped_column(JSON)
    reason: Mapped[str | None] = mapped_column(Text)
    determined_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    determined_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    determination_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DecisionHandoff(Base):
    """The application has completed every stage its route required and is ready for the LATER, authorised decision process.
    This is a hand-off record only: no decision, approval or rejection exists in this phase."""

    __tablename__ = "decision_handoffs"
    __table_args__ = (CheckConstraint("status IN ('READY_FOR_DECISION_STAGE')", name="ck_dhandoff_status"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    application_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("applications.id"), unique=True)
    status: Mapped[str] = mapped_column(String(30), default="READY_FOR_DECISION_STAGE")
    basis: Mapped[dict] = mapped_column(JSON)  # rule version, the route as followed, every completed stage and its records
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
