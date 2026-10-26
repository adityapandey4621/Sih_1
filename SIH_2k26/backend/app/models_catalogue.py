"""Versioned rule catalogue (Phase 2). See docs/ARCHITECTURE.md for the lifecycle."""

import uuid
from datetime import date, datetime

from sqlalchemy import JSON, Boolean, Date, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models import User, new_id, utcnow


class RuleSource(Base):
    __tablename__ = "rule_sources"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    title: Mapped[str] = mapped_column(String(400))
    issuing_authority: Mapped[str] = mapped_column(String(200))
    url: Mapped[str | None] = mapped_column(String(1000))
    document_ref: Mapped[str | None] = mapped_column(String(300))  # e.g. stored source-document reference
    clause_ref: Mapped[str | None] = mapped_column(String(300))  # clause / table
    page_ref: Mapped[str | None] = mapped_column(String(100))
    publication_date: Mapped[date | None] = mapped_column(Date)
    effective_date: Mapped[date | None] = mapped_column(Date)
    retrieved_date: Mapped[date | None] = mapped_column(Date)
    verification_status: Mapped[str] = mapped_column(String(30))
    applicability_notes: Mapped[str | None] = mapped_column(Text)
    created_by_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class ApplicationCategory(Base):
    __tablename__ = "application_categories"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    key: Mapped[str] = mapped_column(String(80), unique=True)  # stable internal ID
    name: Mapped[str] = mapped_column(String(300))
    description: Mapped[str] = mapped_column(Text, default="")
    applicability_note: Mapped[str | None] = mapped_column(Text)
    source_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("rule_sources.id"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    source: Mapped[RuleSource | None] = relationship()


class RuleSet(Base):
    """A lineage of versions for one category."""

    __tablename__ = "rule_sets"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    key: Mapped[str] = mapped_column(String(120), unique=True)  # stable rule-set ID
    title: Mapped[str] = mapped_column(String(300))
    category_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("application_categories.id"), index=True)
    classification: Mapped[str] = mapped_column(String(10))  # OFFICIAL | DEMO
    created_by_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    category: Mapped[ApplicationCategory] = relationship()
    versions: Mapped[list["RuleVersion"]] = relationship(back_populates="rule_set", order_by="RuleVersion.version_number")


class RuleVersion(Base):
    __tablename__ = "rule_versions"
    __table_args__ = (UniqueConstraint("rule_set_id", "version_number"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    rule_set_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("rule_sets.id"), index=True)
    version_number: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="draft", index=True)
    academic_years: Mapped[list] = mapped_column(JSON, default=list)
    effective_from: Mapped[date | None] = mapped_column(Date)
    effective_to: Mapped[date | None] = mapped_column(Date)
    change_summary: Mapped[str] = mapped_column(Text, default="")
    row_version: Mapped[int] = mapped_column(Integer, default=1)  # optimistic concurrency for edits
    supersedes_version_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("rule_versions.id"))

    created_by_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    submitted_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    review_note: Mapped[str | None] = mapped_column(Text)  # latest approver note (e.g. changes requested)
    published_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retired_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retirement_reason: Mapped[str | None] = mapped_column(Text)

    # Frozen at publication: full content + sources, and its canonical hash.
    snapshot: Mapped[dict | None] = mapped_column(JSON)
    content_hash: Mapped[str | None] = mapped_column(String(64))

    rule_set: Mapped[RuleSet] = relationship(back_populates="versions")
    source_links: Mapped[list["RuleVersionSource"]] = relationship(cascade="all, delete-orphan", back_populates="version")
    checklist_items: Mapped[list["ChecklistItem"]] = relationship(
        cascade="all, delete-orphan", back_populates="version", order_by="ChecklistItem.display_order"
    )
    route_stages: Mapped[list["RouteStage"]] = relationship(
        cascade="all, delete-orphan", back_populates="version", order_by="RouteStage.display_order"
    )
    deadline_policies: Mapped[list["DeadlinePolicy"]] = relationship(cascade="all, delete-orphan", back_populates="version")
    events: Mapped[list["RuleVersionEvent"]] = relationship(order_by="RuleVersionEvent.at", cascade="all, delete-orphan")


class RuleVersionSource(Base):
    __tablename__ = "rule_version_sources"
    __table_args__ = (UniqueConstraint("rule_version_id", "source_id"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    rule_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("rule_versions.id"), index=True)
    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("rule_sources.id"))
    note: Mapped[str | None] = mapped_column(Text)

    version: Mapped[RuleVersion] = relationship(back_populates="source_links")
    source: Mapped[RuleSource] = relationship()


class ChecklistItem(Base):
    __tablename__ = "checklist_items"
    __table_args__ = (UniqueConstraint("rule_version_id", "item_key"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    rule_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("rule_versions.id"), index=True)
    item_key: Mapped[str] = mapped_column(String(80))  # stable across versions
    label: Mapped[str] = mapped_column(String(300))
    explanation: Mapped[str] = mapped_column(Text, default="")
    required: Mapped[bool] = mapped_column(Boolean, default=True)
    item_type: Mapped[str] = mapped_column(String(20))
    field_kind: Mapped[str | None] = mapped_column(String(20))  # for form_field
    options: Mapped[list | None] = mapped_column(JSON)  # for select
    validation: Mapped[dict | None] = mapped_column(JSON)  # {min_length,max_length,min,max}
    applies_when: Mapped[dict | None] = mapped_column(JSON)  # {"item_key": "...", "equals": ...} / {"in": [...]}
    source_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("rule_sources.id"))
    clause_ref: Mapped[str | None] = mapped_column(String(300))
    display_order: Mapped[int] = mapped_column(Integer, default=0)
    # Which values the document-processing layer should look for in this item's document (data, never code).
    extraction: Mapped[list | None] = mapped_column(JSON)

    version: Mapped[RuleVersion] = relationship(back_populates="checklist_items")
    source: Mapped[RuleSource | None] = relationship()


class RouteStage(Base):
    __tablename__ = "route_stages"
    __table_args__ = (UniqueConstraint("rule_version_id", "stage_key"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    rule_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("rule_versions.id"), index=True)
    stage_key: Mapped[str] = mapped_column(String(80))
    label: Mapped[str] = mapped_column(String(300))
    description: Mapped[str] = mapped_column(Text, default="")
    display_order: Mapped[int] = mapped_column(Integer, default=0)
    is_conditional: Mapped[bool] = mapped_column(Boolean, default=False)
    condition_description: Mapped[str | None] = mapped_column(Text)  # human-readable; required when conditional
    next_stage_keys: Mapped[list] = mapped_column(JSON, default=list)
    source_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("rule_sources.id"))
    clause_ref: Mapped[str | None] = mapped_column(String(300))

    version: Mapped[RuleVersion] = relationship(back_populates="route_stages")
    source: Mapped[RuleSource | None] = relationship()


class DeadlinePolicy(Base):
    """Stored for later phases. Phase 2 does not schedule or enforce anything."""

    __tablename__ = "deadline_policies"
    __table_args__ = (UniqueConstraint("rule_version_id", "policy_key"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    rule_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("rule_versions.id"), index=True)
    policy_key: Mapped[str] = mapped_column(String(80))
    label: Mapped[str] = mapped_column(String(300))
    deadline_type: Mapped[str] = mapped_column(String(20))
    trigger_event: Mapped[str] = mapped_column(String(200))
    duration_value: Mapped[int | None] = mapped_column(Integer)  # null = not established by a verified source
    duration_unit: Mapped[str | None] = mapped_column(String(20))
    calendar_convention: Mapped[str | None] = mapped_column(String(20))  # CALENDAR | WORKING | null = unspecified
    pause_resume_note: Mapped[str | None] = mapped_column(Text)
    escalation: Mapped[dict | None] = mapped_column(JSON)
    source_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("rule_sources.id"))
    clause_ref: Mapped[str | None] = mapped_column(String(300))

    version: Mapped[RuleVersion] = relationship(back_populates="deadline_policies")
    source: Mapped[RuleSource | None] = relationship()


class RuleVersionEvent(Base):
    """Per-version history shown to reviewers (the global audit log records the same actions)."""

    __tablename__ = "rule_version_events"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    rule_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("rule_versions.id"), index=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(40))
    from_status: Mapped[str | None] = mapped_column(String(20))
    to_status: Mapped[str | None] = mapped_column(String(20))
    note: Mapped[str | None] = mapped_column(Text)

    actor: Mapped[User | None] = relationship()


class ConfigurationGap(Base):
    """Raised when a category/year has no single approved configuration. Directs work to rule reviewers."""

    __tablename__ = "configuration_gaps"
    __table_args__ = (UniqueConstraint("category_id", "academic_year", "reason"), Index("ix_gaps_status", "status"))

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    category_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("application_categories.id"))
    academic_year: Mapped[str] = mapped_column(String(9))
    reason: Mapped[str] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(String(10), default="open")  # open | resolved
    report_count: Mapped[int] = mapped_column(Integer, default=1)
    first_reported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_reported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    detail: Mapped[dict | None] = mapped_column(JSON)
    resolved_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolution_note: Mapped[str | None] = mapped_column(Text)

    category: Mapped[ApplicationCategory] = relationship()
