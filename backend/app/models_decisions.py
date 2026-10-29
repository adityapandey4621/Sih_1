"""Executive Committee decisions, Council ratification, appeals and the external-sync record (Phase 6).

These tables record what authorised bodies decided. Nothing here derives a decision: no OCR result, checklist, report or recommendation is ever
turned into an outcome by code. A recorded decision is immutable; a correction is a new, authorised version and the earlier one stays.
"""

import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models import User, new_id, utcnow

_DSTATES = "'AWAITING_OBSERVATIONS','READY_FOR_DECISION','DECIDED','ISSUED','CLOSED'"


def _where(cond: str) -> dict:
    return {"postgresql_where": text(cond), "sqlite_where": text(cond)}


class DecisionCase(Base):
    """One per application whose route is complete. The Executive Committee's queue entry; its own lifecycle, separate from every earlier stage."""

    __tablename__ = "decision_cases"
    __table_args__ = (CheckConstraint(f"state IN ({_DSTATES})", name="ck_dcase_state"), Index("ix_dcases_state", "state"))

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    application_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("applications.id"), unique=True)
    handoff_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("decision_handoffs.id"), unique=True)
    rule_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("rule_versions.id"))  # the application's frozen version; never rebound
    state: Mapped[str] = mapped_column(String(24), default="AWAITING_OBSERVATIONS")
    row_version: Mapped[int] = mapped_column(Integer, default=1)
    policy_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("committee_policies.id"))
    policy_snapshot: Mapped[dict | None] = mapped_column(JSON)  # frozen decision / ratification / appeal configuration
    blockers: Mapped[list | None] = mapped_column(JSON)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    state_changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    first_issued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # the appeal window is anchored here and never moves
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closure: Mapped[dict | None] = mapped_column(JSON)


class BureauObservation(Base):
    """The Regulation Bureau's observations placed before the Executive Committee (cl. 1.10(a)). Append-only."""

    __tablename__ = "bureau_observations"
    __table_args__ = (UniqueConstraint("case_id", "sequence"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("decision_cases.id"), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    recorded_by_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    idempotency_key: Mapped[str | None] = mapped_column(String(80), unique=True)

    recorded_by: Mapped[User] = relationship(foreign_keys=[recorded_by_id])


class EcDecision(Base):
    """An Executive Committee decision, as recorded by an authorised person. Immutable. A correction is the next version, with a reason."""

    __tablename__ = "ec_decisions"
    __table_args__ = (
        UniqueConstraint("case_id", "version_number"),
        CheckConstraint("kind IN ('ORIGINAL','CORRECTION')", name="ck_ecdec_kind"),
        CheckConstraint("outcome IN ('LOA','LOD','LOR')", name="ck_ecdec_outcome"),
        CheckConstraint("(kind = 'CORRECTION') = (supersedes_id IS NOT NULL AND correction_reason IS NOT NULL)", name="ck_ecdec_correction_consistent"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("decision_cases.id"), index=True)
    version_number: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(10), default="ORIGINAL")
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("ec_decisions.id"))
    correction_reason: Mapped[str | None] = mapped_column(Text)
    outcome: Mapped[str] = mapped_column(String(3))
    reasons: Mapped[str] = mapped_column(Text)  # the reasons for the decision, as recorded
    speaking_order: Mapped[str | None] = mapped_column(Text)  # required with a Letter of Rejection (cl. 1.10(b))
    meeting_reference: Mapped[str | None] = mapped_column(String(200))
    supporting_refs: Mapped[dict] = mapped_column(JSON)  # the exact scrutiny / EVC / SHC / Bureau records the decision-maker confirmed considering
    rule_version: Mapped[dict] = mapped_column(JSON)
    policy_ref: Mapped[dict | None] = mapped_column(JSON)
    decided_by_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    content_hash: Mapped[str] = mapped_column(String(64))
    idempotency_key: Mapped[str | None] = mapped_column(String(80), unique=True)

    decided_by: Mapped[User] = relationship(foreign_keys=[decided_by_id])


class OfficialIssuance(Base):
    """The official outcome issued to the institution: a reference and/or the official file, tied to one decision version."""

    __tablename__ = "official_issuances"
    __table_args__ = (
        CheckConstraint("status IN ('ISSUED','SUPERSEDED')", name="ck_issuance_status"),
        CheckConstraint("document_reference IS NOT NULL OR storage_key IS NOT NULL", name="ck_issuance_has_reference"),
        Index("uq_one_live_issuance", "case_id", unique=True, **_where("status = 'ISSUED'")),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("decision_cases.id"), index=True)
    decision_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ec_decisions.id"))
    status: Mapped[str] = mapped_column(String(10), default="ISSUED")
    document_reference: Mapped[str | None] = mapped_column(String(300))
    storage_key: Mapped[str | None] = mapped_column(String(64))
    original_filename: Mapped[str | None] = mapped_column(String(255))
    media_type: Mapped[str | None] = mapped_column(String(100))
    size_bytes: Mapped[int | None] = mapped_column(Integer)
    sha256: Mapped[str | None] = mapped_column(String(64))
    issued_by_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    idempotency_key: Mapped[str | None] = mapped_column(String(80), unique=True)


class CouncilDecision(Base):
    """A Council step: ratification of an Executive Committee decision, or the Council's decision on an appeal. Immutable; a correction supersedes."""

    __tablename__ = "council_decisions"
    __table_args__ = (
        CheckConstraint("kind IN ('RATIFICATION','APPEAL_DECISION')", name="ck_council_kind"),
        CheckConstraint("(kind = 'RATIFICATION') = (decision_id IS NOT NULL)", name="ck_council_ratif_target"),
        CheckConstraint("(kind = 'APPEAL_DECISION') = (appeal_id IS NOT NULL)", name="ck_council_appeal_target"),
        Index("uq_live_ratification", "decision_id", unique=True, **_where("kind = 'RATIFICATION' AND is_live")),
        Index("uq_live_appeal_decision", "appeal_id", unique=True, **_where("kind = 'APPEAL_DECISION' AND is_live")),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    kind: Mapped[str] = mapped_column(String(16))
    decision_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("ec_decisions.id"))
    appeal_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("appeals.id", use_alter=True, name="fk_council_appeal"))
    outcome: Mapped[str] = mapped_column(String(24))
    reasons: Mapped[str] = mapped_column(Text)
    speaking_order: Mapped[str | None] = mapped_column(Text)
    reference: Mapped[str | None] = mapped_column(String(300))  # the Council meeting / minute reference
    recorded_by_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("council_decisions.id"))
    superseded_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("council_decisions.id"))
    is_live: Mapped[bool] = mapped_column(Boolean, default=True)  # exactly one live record per decision / appeal; a correction retires the earlier one first
    correction_reason: Mapped[str | None] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))
    idempotency_key: Mapped[str | None] = mapped_column(String(80), unique=True)
    policy_ref: Mapped[dict | None] = mapped_column(JSON)

    recorded_by: Mapped[User] = relationship(foreign_keys=[recorded_by_id])


class Appeal(Base):
    """An institution's appeal against an issued Executive Committee decision. At most one per application (the handbook allows an appeal once)."""

    __tablename__ = "appeals"
    __table_args__ = (CheckConstraint("status IN ('UNDER_SAC_REVIEW','AWAITING_COUNCIL_DECISION','COUNCIL_DECIDED')", name="ck_appeal_status"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    application_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("applications.id"), unique=True)
    institution_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("institutions.id"), index=True)
    decision_case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("decision_cases.id"))
    against_decision_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ec_decisions.id"))  # the issued Executive Committee decision version it is against
    sac_case_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("committee_cases.id"))
    status: Mapped[str] = mapped_column(String(26), default="UNDER_SAC_REVIEW")
    row_version: Mapped[int] = mapped_column(Integer, default=1)
    grounds: Mapped[str] = mapped_column(Text)
    eligibility: Mapped[dict] = mapped_column(JSON)  # what was evaluated: rule, clause, trigger, deadline, convention note, configuration
    submitted_by_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    idempotency_key: Mapped[str | None] = mapped_column(String(80), unique=True)
    sac_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    documents: Mapped[list["AppealDocument"]] = relationship(order_by="AppealDocument.uploaded_at", back_populates="appeal")
    submitted_by: Mapped[User] = relationship(foreign_keys=[submitted_by_id])


class AppealDocument(Base):
    """Supporting evidence for an appeal: a private file, or a reference to material held elsewhere. Kept; never edited."""

    __tablename__ = "appeal_documents"
    __table_args__ = (
        CheckConstraint("kind IN ('FILE','LINK')", name="ck_adoc_kind"),
        CheckConstraint("(kind = 'FILE') = (storage_key IS NOT NULL)", name="ck_adoc_file_consistent"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    appeal_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("appeals.id"), index=True)
    kind: Mapped[str] = mapped_column(String(4))
    description: Mapped[str] = mapped_column(Text, default="")
    storage_key: Mapped[str | None] = mapped_column(String(64))
    original_filename: Mapped[str | None] = mapped_column(String(255))
    media_type: Mapped[str | None] = mapped_column(String(100))
    size_bytes: Mapped[int | None] = mapped_column(Integer)
    sha256: Mapped[str | None] = mapped_column(String(64))
    url: Mapped[str | None] = mapped_column(String(1000))
    uploaded_by_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    appeal: Mapped[Appeal] = relationship(back_populates="documents")


class ExternalSync(Base):
    """What was (or was not) sent to an external system, and what that system confirmed. Never "synced" without a confirmation."""

    __tablename__ = "external_syncs"
    __table_args__ = (
        UniqueConstraint("subject_type", "subject_id", "kind"),
        CheckConstraint("status IN ('NOT_CONFIGURED','SYNC_PENDING','SYNCED','FAILED','RECONCILIATION_REQUIRED')", name="ck_sync_status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    application_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("applications.id"), index=True)
    subject_type: Mapped[str] = mapped_column(String(24))  # official_issuance | council_decision
    subject_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    kind: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(24))  # RECONCILIATION_REQUIRED is 23 characters: found by the PostgreSQL run (SQLite does not enforce lengths)
    payload_hash: Mapped[str] = mapped_column(String(64))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)
    external_ref: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # SYNC_PENDING: not before this moment (exponential back-off, bounded)
    adapter: Mapped[str | None] = mapped_column(String(20))  # which adapter made the last attempt (mock | http | ...)
    last_outcome: Mapped[str | None] = mapped_column(String(40))  # the adapter's error code of the last attempt, never a message body


class PostDecisionHandoff(Base):
    """No further decision-stage step is open: a record for the later post-approval stage (Phase 7+). It starts nothing."""

    __tablename__ = "post_decision_handoffs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    application_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("applications.id"), unique=True)
    decision_case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("decision_cases.id"), unique=True)
    basis: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
