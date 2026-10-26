"""Document versions' processing state, jobs, extracted content and preliminary findings (Phase 3).

`ApplicationDocument` (Phase 2) is the immutable record of an uploaded *version*; everything that
changes while it is processed lives here, so processing never mutates a submitted application's
documents.
"""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models import User, new_id, utcnow

if TYPE_CHECKING:  # resolved lazily by name: models_applications imports this module's tables at registration time
    from app.models_applications import ApplicationDocument


class DocumentSlot(Base):
    """The logical document for one checklist requirement of one application; versions hang off it."""

    __tablename__ = "document_slots"
    __table_args__ = (UniqueConstraint("application_id", "item_key"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    application_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("applications.id"), index=True)
    item_key: Mapped[str] = mapped_column(String(80))
    current_version_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("application_documents.id", use_alter=True, name="fk_slot_current_version"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DocumentProcessing(Base):
    """Mutable processing state of one document version. Never contains a 'verified' value."""

    __tablename__ = "document_processing"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("application_documents.id"), unique=True)
    technical_status: Mapped[str] = mapped_column(String(12), default="PENDING")
    technical_code: Mapped[str | None] = mapped_column(String(40))
    technical_detail: Mapped[dict | None] = mapped_column(JSON)
    scan_status: Mapped[str] = mapped_column(String(24), default="PENDING")
    scan_detail: Mapped[dict | None] = mapped_column(JSON)
    extraction_status: Mapped[str] = mapped_column(String(12), default="QUEUED")
    text_method: Mapped[str | None] = mapped_column(String(12))  # PDF_TEXT | OCR | MIXED | NONE
    page_count: Mapped[int | None] = mapped_column(Integer)
    pages_ocr: Mapped[int] = mapped_column(Integer, default=0)
    pages_unreadable: Mapped[int] = mapped_column(Integer, default=0)
    review_recommended: Mapped[bool] = mapped_column(Boolean, default=False)
    last_error_code: Mapped[str | None] = mapped_column(String(40))
    last_error_message: Mapped[str | None] = mapped_column(String(500))  # sanitised; never document content or paths
    processor_version: Mapped[str | None] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    document: Mapped["ApplicationDocument"] = relationship(back_populates="processing")


class ProcessingJob(Base):
    __tablename__ = "processing_jobs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued','running','retry_wait','succeeded','failed_permanent','failed_exhausted','cancelled')",
            name="ck_job_status",
        ),
        # exactly one PROCESS_DOCUMENT job per document version (retries re-queue the same row: no duplicate results)
        Index("uq_process_job_per_document", "document_id", unique=True, postgresql_where=text("job_type = 'PROCESS_DOCUMENT'"), sqlite_where=text("job_type = 'PROCESS_DOCUMENT'")),
        # cross-checks coalesce: at most one waiting per application
        Index("uq_waiting_cross_check", "application_id", unique=True, postgresql_where=text("job_type = 'CROSS_CHECK' AND status IN ('queued','retry_wait')"), sqlite_where=text("job_type = 'CROSS_CHECK' AND status IN ('queued','retry_wait')")),
        Index("ix_jobs_claim", "status", "run_after"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    job_type: Mapped[str] = mapped_column(String(20))
    application_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("applications.id"), index=True)
    document_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("application_documents.id"), index=True)
    status: Mapped[str] = mapped_column(String(20), default="queued")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    manual_retries: Mapped[int] = mapped_column(Integer, default=0)
    run_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    locked_by: Mapped[str | None] = mapped_column(String(80))
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(String(40))
    last_error_message: Mapped[str | None] = mapped_column(String(500))
    retry_eligible: Mapped[bool] = mapped_column(Boolean, default=False)  # may a person request another attempt?
    processor_version: Mapped[str | None] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ExtractedPage(Base):
    __tablename__ = "extracted_pages"
    __table_args__ = (UniqueConstraint("document_id", "page_number"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("application_documents.id"), index=True)
    page_number: Mapped[int] = mapped_column(Integer)  # 1-based
    method: Mapped[str] = mapped_column(String(12))
    status: Mapped[str] = mapped_column(String(20))
    text: Mapped[str] = mapped_column(Text, default="")  # document content: reviewer/owner access only; never logged
    char_count: Mapped[int] = mapped_column(Integer, default=0)
    confidence: Mapped[float | None] = mapped_column(Float)  # mean OCR word confidence 0-100; null for digital text
    note: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ExtractedField(Base):
    __tablename__ = "extracted_fields"
    __table_args__ = (UniqueConstraint("document_id", "field_key"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("application_documents.id"), index=True)
    job_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("processing_jobs.id"))
    field_key: Mapped[str] = mapped_column(String(80))
    label: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(12))
    status: Mapped[str] = mapped_column(String(12))
    value: Mapped[str | None] = mapped_column(String(500))
    normalised_value: Mapped[str | None] = mapped_column(String(500))
    page_number: Mapped[int | None] = mapped_column(Integer)
    method: Mapped[str | None] = mapped_column(String(12))
    confidence: Mapped[float | None] = mapped_column(Float)
    evidence: Mapped[str | None] = mapped_column(String(400))  # the source line, for the reviewer
    note: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PreliminaryFinding(Base):
    """A potential evidence issue for authorised review. Never an official deficiency notice."""

    __tablename__ = "preliminary_findings"
    __table_args__ = (
        CheckConstraint("status IN ('PRELIMINARY_ISSUE','SUPERSEDED')", name="ck_finding_status"),
        Index("uq_active_finding", "application_id", "fingerprint", unique=True, postgresql_where=text("superseded_at IS NULL"), sqlite_where=text("superseded_at IS NULL")),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    application_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("applications.id"), index=True)
    issue_type: Mapped[str] = mapped_column(String(40))
    check_key: Mapped[str] = mapped_column(String(60))  # the deterministic check that produced it
    status: Mapped[str] = mapped_column(String(20), default="PRELIMINARY_ISSUE")
    priority: Mapped[str] = mapped_column(String(10))  # info | low | medium | high  (review priority, not legal severity)
    item_key: Mapped[str | None] = mapped_column(String(80))
    document_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("application_documents.id"))
    pages: Mapped[list] = mapped_column(JSON, default=list)
    values: Mapped[list] = mapped_column(JSON, default=list)  # [{source, document_id, page, value}] compared values
    explanation: Mapped[str] = mapped_column(Text)
    rule_ref: Mapped[str | None] = mapped_column(String(300))  # checklist item / clause reference
    review_recommended: Mapped[bool] = mapped_column(Boolean, default=True)
    institute_visible: Mapped[bool] = mapped_column(Boolean, default=True)
    fingerprint: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DocumentEvent(Base):
    """Processing history of a document version (shown to the owner and to reviewers)."""

    __tablename__ = "document_events"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("application_documents.id"), index=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))  # null = the system worker
    action: Mapped[str] = mapped_column(String(40))
    detail: Mapped[dict | None] = mapped_column(JSON)

    actor: Mapped[User | None] = relationship()

