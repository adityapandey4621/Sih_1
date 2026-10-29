"""Compliance obligations that follow an issued decision, and document-validity tracking (Phase 7).

An obligation records what an authorised body asked an institution to do after a decision. Evidence submitted for it is a SUBMISSION; only an
authorised verifier's recorded review can make it VERIFIED. Nothing here revokes an approval or imposes a penalty: a NOT_MET outcome is a
record for the competent authority, and any consequence is that authority's, outside this system.
"""

import uuid
from datetime import date, datetime

from sqlalchemy import JSON, BigInteger, Boolean, CheckConstraint, Date, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models import User, new_id, utcnow

_OBL = "'AWAITING_EVIDENCE','EVIDENCE_SUBMITTED','CORRECTION_REQUESTED','VERIFIED','NOT_MET','WITHDRAWN'"


class Obligation(Base):
    __tablename__ = "obligations"
    __table_args__ = (
        CheckConstraint(f"status IN ({_OBL})", name="ck_obligation_status"),
        CheckConstraint("due_basis IN ('POLICY','SPECIFIED','NONE')", name="ck_obligation_due_basis"),
        CheckConstraint("(due_basis = 'NONE') = (due_at IS NULL)", name="ck_obligation_due_consistent"),
        CheckConstraint("due_basis <> 'SPECIFIED' OR due_authority IS NOT NULL", name="ck_obligation_specified_cited"),
        CheckConstraint("(decision_id IS NULL) <> (council_decision_id IS NULL)", name="ck_obligation_one_source"),
        Index("ix_obligations_inst_status", "institution_id", "status"),
        Index("ix_obligations_verifier", "verifier_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    reference: Mapped[str] = mapped_column(String(24), unique=True)
    application_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("applications.id"), index=True)
    institution_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("institutions.id"))
    decision_case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("decision_cases.id"), index=True)
    # exactly one source: the issued Executive Committee decision version it follows from, OR the Council's decision on an appeal (an approval granted on appeal)
    decision_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("ec_decisions.id"))
    council_decision_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("council_decisions.id"))
    rule_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("rule_versions.id"))
    title: Mapped[str] = mapped_column(String(200))
    condition_text: Mapped[str] = mapped_column(Text)  # the condition, as recorded by the authorised person from the decision
    condition_ref: Mapped[str | None] = mapped_column(String(300))  # where in the decision / letter it appears
    required_evidence: Mapped[str] = mapped_column(Text)
    verifier_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    status: Mapped[str] = mapped_column(String(22), default="AWAITING_EVIDENCE")
    row_version: Mapped[int] = mapped_column(Integer, default=1)
    due_basis: Mapped[str] = mapped_column(String(10))
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    due_authority: Mapped[str | None] = mapped_column(String(300))  # SPECIFIED: the citation for the date
    due_policy: Mapped[dict | None] = mapped_column(JSON)  # POLICY: how it was computed (or why it was not)
    defined_by_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    defined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    withdrawn_reason: Mapped[str | None] = mapped_column(Text)
    idempotency_key: Mapped[str | None] = mapped_column(String(80), unique=True)

    verifier: Mapped[User] = relationship(foreign_keys=[verifier_id])
    defined_by: Mapped[User] = relationship(foreign_keys=[defined_by_id])
    submissions: Mapped[list["ObligationSubmission"]] = relationship(order_by="ObligationSubmission.sequence", back_populates="obligation")


class ObligationSubmission(Base):
    """Evidence an institution put forward. Append-only. Says nothing about whether the condition is satisfied."""

    __tablename__ = "obligation_submissions"
    __table_args__ = (UniqueConstraint("obligation_id", "sequence"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    obligation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("obligations.id"), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    note: Mapped[str] = mapped_column(Text, default="")
    submitted_by_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    idempotency_key: Mapped[str | None] = mapped_column(String(80), unique=True)

    obligation: Mapped[Obligation] = relationship(back_populates="submissions")
    documents: Mapped[list["ObligationDocument"]] = relationship(order_by="ObligationDocument.label")
    submitted_by: Mapped[User] = relationship(foreign_keys=[submitted_by_id])


class ObligationDocument(Base):
    """One file of a submission, versioned per (obligation, label): a corrected resubmission is a new version and the earlier one stays."""

    __tablename__ = "obligation_documents"
    __table_args__ = (UniqueConstraint("obligation_id", "label", "version_number"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    submission_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("obligation_submissions.id"), index=True)
    obligation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("obligations.id"), index=True)
    label: Mapped[str] = mapped_column(String(120))
    version_number: Mapped[int] = mapped_column(Integer)
    storage_key: Mapped[str] = mapped_column(String(64), unique=True)
    original_filename: Mapped[str] = mapped_column(String(255))
    media_type: Mapped[str] = mapped_column(String(100))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(String(64))
    scan_status: Mapped[str] = mapped_column(String(24), default="NOT_SCANNED")
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ObligationReview(Base):
    """A verifier's recorded outcome on one submission. Append-only. `internal_note` is never shown to the institution."""

    __tablename__ = "obligation_reviews"
    __table_args__ = (CheckConstraint("outcome IN ('VERIFIED','CORRECTION_REQUESTED','NOT_MET')", name="ck_review_outcome"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    obligation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("obligations.id"), index=True)
    submission_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("obligation_submissions.id"), unique=True)  # one review per submission
    outcome: Mapped[str] = mapped_column(String(22))
    reason: Mapped[str] = mapped_column(Text)  # visible to the institution
    internal_note: Mapped[str | None] = mapped_column(Text)
    reviewed_by_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    reviewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    idempotency_key: Mapped[str | None] = mapped_column(String(80), unique=True)

    reviewed_by: Mapped[User] = relationship(foreign_keys=[reviewed_by_id])


class ObligationEvent(Base):
    """Per-obligation history. Append-only."""

    __tablename__ = "obligation_events"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    obligation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("obligations.id"), index=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(40))
    from_status: Mapped[str | None] = mapped_column(String(22))
    to_status: Mapped[str | None] = mapped_column(String(22))
    detail: Mapped[dict | None] = mapped_column(JSON)
    institution_visible: Mapped[bool] = mapped_column(Boolean, default=True)

    actor: Mapped[User | None] = relationship(foreign_keys=[actor_id])


# =====================================================================================================
# Document validity
# =====================================================================================================


class ValidityItem(Base):
    """A document whose validity period matters (for example a lease), tracked across renewals."""

    __tablename__ = "validity_items"
    __table_args__ = (
        UniqueConstraint("application_id", "item_key", "field_key"),
        CheckConstraint("status IN ('ACTIVE','RESOLVED')", name="ck_vitem_status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    application_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("applications.id"), index=True)
    institution_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("institutions.id"), index=True)
    rule_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("rule_versions.id"))
    item_key: Mapped[str] = mapped_column(String(80))  # the checklist item the document belongs to
    field_key: Mapped[str] = mapped_column(String(80))  # the configured extraction field that holds the date
    label: Mapped[str] = mapped_column(String(300))
    status: Mapped[str] = mapped_column(String(10), default="ACTIVE")
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    resolution_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    versions: Mapped[list["ValidityVersion"]] = relationship(order_by="ValidityVersion.version_number", back_populates="item")


class ValidityVersion(Base):
    """One candidate or claimed expiry date for an item, with its source. The source columns never change; only the review columns do, once."""

    __tablename__ = "validity_versions"
    __table_args__ = (
        UniqueConstraint("item_id", "version_number"),
        CheckConstraint("source IN ('EXTRACTED','DECLARED')", name="ck_vver_source"),
        CheckConstraint("status IN ('UNVERIFIED','NEEDS_REVIEW','VERIFIED','REJECTED','SUPERSEDED')", name="ck_vver_status"),
        CheckConstraint("status NOT IN ('VERIFIED') OR (verified_date IS NOT NULL AND verified_by_id IS NOT NULL AND verified_at IS NOT NULL)", name="ck_vver_verified_consistent"),
        # one extracted candidate per source document version
        Index("uq_vver_extracted_doc", "item_id", "source_document_id", unique=True, postgresql_where=text("source = 'EXTRACTED'"), sqlite_where=text("source = 'EXTRACTED'")),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    item_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("validity_items.id"), index=True)
    version_number: Mapped[int] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(String(10))
    status: Mapped[str] = mapped_column(String(12))
    value_date: Mapped[date | None] = mapped_column(Date)  # what the document was read (or the institution said) to show; NOT yet a confirmed date
    raw_value: Mapped[str | None] = mapped_column(String(500))
    # provenance of an extracted date
    source_document_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("application_documents.id"))
    source_document_version: Mapped[int | None] = mapped_column(Integer)
    source_page: Mapped[int | None] = mapped_column(Integer)
    source_method: Mapped[str | None] = mapped_column(String(12))
    source_confidence: Mapped[float | None] = mapped_column(Float)
    source_evidence: Mapped[str | None] = mapped_column(String(400))
    review_reason_flag: Mapped[str | None] = mapped_column(String(200))  # why a person must look: uncertain read, conflicting date, ...
    conflict: Mapped[dict | None] = mapped_column(JSON)
    # a renewal upload
    storage_key: Mapped[str | None] = mapped_column(String(64), unique=True)
    original_filename: Mapped[str | None] = mapped_column(String(255))
    media_type: Mapped[str | None] = mapped_column(String(100))
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    sha256: Mapped[str | None] = mapped_column(String(64))
    scan_status: Mapped[str | None] = mapped_column(String(24))
    uploaded_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    # the human review
    verified_date: Mapped[date | None] = mapped_column(Date)
    verified_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    review_reason: Mapped[str | None] = mapped_column(Text)  # visible to the institution on a rejection
    internal_note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    idempotency_key: Mapped[str | None] = mapped_column(String(80), unique=True)

    item: Mapped[ValidityItem] = relationship(back_populates="versions")
