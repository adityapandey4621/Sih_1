"""Institution applications (Phase 2): DRAFT and SUBMITTED only."""

import uuid
from datetime import datetime

from sqlalchemy import JSON, BigInteger, CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models import Institution, User, new_id, utcnow
from app.models_catalogue import ApplicationCategory, RuleVersion


class Application(Base):
    __tablename__ = "applications"
    __table_args__ = (
        CheckConstraint("status IN ('DRAFT','SUBMITTED')", name="ck_application_status"),
        CheckConstraint(
            "(status = 'SUBMITTED') = (submitted_at IS NOT NULL AND submitted_by_id IS NOT NULL AND submission_snapshot IS NOT NULL)",
            name="ck_application_submission_consistent",
        ),
        Index("ix_applications_institution_created", "institution_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    reference: Mapped[str] = mapped_column(String(24), unique=True)  # internal reference, e.g. APP-7K3Q-9XMD
    institution_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("institutions.id"), index=True)
    created_by_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    category_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("application_categories.id"))
    academic_year: Mapped[str] = mapped_column(String(9))
    rule_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("rule_versions.id"))
    status: Mapped[str] = mapped_column(String(12), default="DRAFT", index=True)
    form_data: Mapped[dict] = mapped_column(JSON, default=dict)
    row_version: Mapped[int] = mapped_column(Integer, default=1)  # optimistic concurrency
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    submitted_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    # Rule version + frozen configuration + checklist state at the moment of submission.
    submission_snapshot: Mapped[dict | None] = mapped_column(JSON)

    institution: Mapped[Institution] = relationship()
    created_by: Mapped[User] = relationship(foreign_keys=[created_by_id])
    submitted_by: Mapped[User | None] = relationship(foreign_keys=[submitted_by_id])
    category: Mapped[ApplicationCategory] = relationship()
    rule_version: Mapped[RuleVersion] = relationship()
    documents: Mapped[list["ApplicationDocument"]] = relationship(back_populates="application", order_by="ApplicationDocument.uploaded_at")
    events: Mapped[list["ApplicationEvent"]] = relationship(back_populates="application", order_by="ApplicationEvent.at")


class ApplicationDocument(Base):
    __tablename__ = "application_documents"
    __table_args__ = (
        # Uploading is not verification. Later phases add values; Phase 2 allows exactly one.
        CheckConstraint("verification_status = 'NOT_VERIFIED'", name="ck_document_not_verified_in_phase2"),
        # One active file per checklist slot; replaced files are kept (soft-removed) for the record.
        Index(
            "uq_active_document_per_item",
            "application_id",
            "item_key",
            unique=True,
            postgresql_where=text("removed_at IS NULL"),
            sqlite_where=text("removed_at IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    application_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("applications.id"), index=True)
    item_key: Mapped[str] = mapped_column(String(80))
    original_filename: Mapped[str] = mapped_column(String(255))  # display only; never used to build a path
    storage_key: Mapped[str] = mapped_column(String(64), unique=True)  # generated; never user-supplied
    media_type: Mapped[str] = mapped_column(String(100))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(String(64))
    verification_status: Mapped[str] = mapped_column(String(20), default="NOT_VERIFIED")
    uploaded_by_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    removed_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))

    application: Mapped[Application] = relationship(back_populates="documents")
    uploaded_by: Mapped[User] = relationship(foreign_keys=[uploaded_by_id])


class ApplicationEvent(Base):
    """Per-application history shown on the detail page (the global audit log records the same actions)."""

    __tablename__ = "application_events"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    application_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("applications.id"), index=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(40))
    detail: Mapped[dict | None] = mapped_column(JSON)
    note: Mapped[str | None] = mapped_column(Text)

    application: Mapped[Application] = relationship(back_populates="events")
    actor: Mapped[User | None] = relationship()
