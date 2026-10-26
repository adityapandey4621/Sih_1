import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.schemas_catalogue import CategoryOut, ChecklistItemOut, RouteStageOut, SourceOut


class ApplicationCreateIn(BaseModel):
    category_id: uuid.UUID
    academic_year: str = Field(min_length=7, max_length=7)


class ApplicationUpdateIn(BaseModel):
    form_data: dict[str, Any] = Field(max_length=200)
    row_version: int


class ApplicationSubmitIn(BaseModel):
    row_version: int


class DocumentOut(BaseModel):
    id: uuid.UUID
    item_key: str
    original_filename: str
    media_type: str
    size_bytes: int
    sha256: str
    uploaded_at: datetime
    uploaded_by: str | None
    removed: bool
    # Always NOT_VERIFIED in Phase 2: an upload is a technical fact, not a regulatory finding.
    verification_status: str


class ChecklistStateOut(BaseModel):
    item_key: str
    label: str
    explanation: str
    required: bool
    item_type: str
    field_kind: str | None
    options: list[str] | None
    validation: dict | None
    applies_when: dict | None
    clause_ref: str | None
    source_id: str | None
    applicable: bool
    status: str  # supplied | missing | invalid | not_applicable
    error: str | None
    document_id: str | None


class ChecklistSummaryOut(BaseModel):
    required_total: int
    required_supplied: int
    optional_total: int
    optional_supplied: int


class HistoryOut(BaseModel):
    at: datetime
    actor: str | None
    action: str
    detail: dict | None


class RuleVersionRefOut(BaseModel):
    id: uuid.UUID
    rule_set_key: str
    rule_set_title: str
    number: int
    classification: str
    is_demo: bool
    demo_banner: str | None
    content_hash: str | None
    verification: str
    sources: list[dict]
    route: list[RouteStageOut]


class InstitutionOut(BaseModel):
    id: uuid.UUID
    name: str
    external_identifier: str | None


class ApplicationSummaryOut(BaseModel):
    id: uuid.UUID
    reference: str
    category: CategoryOut
    academic_year: str
    status: str
    created_at: datetime
    updated_at: datetime
    submitted_at: datetime | None
    row_version: int
    is_demo: bool
    checklist: ChecklistSummaryOut
    outstanding_action: str | None


class ApplicationDetailOut(ApplicationSummaryOut):
    institution: InstitutionOut
    created_by: str | None
    submitted_by: str | None
    form_data: dict
    rule_version: RuleVersionRefOut
    checklist_items: list[ChecklistStateOut]
    documents: list[DocumentOut]
    history: list[HistoryOut]
    editable: bool
    current_rule_version_differs: bool = False
    orphaned_data_keys: list[str] = []


class ApplicationPage(BaseModel):
    items: list[ApplicationSummaryOut]
    total: int
    page: int
    page_size: int


class CatalogueOptionsOut(BaseModel):
    categories: list[CategoryOut]
    academic_years: list[str]


class ResolvedChecklistOut(BaseModel):
    rule_set_key: str
    rule_set_title: str
    version_number: int
    is_demo: bool
    demo_banner: str | None
    verification: str
    checklist: list[ChecklistItemOut]
    route: list[RouteStageOut]
    sources: list[SourceOut]
