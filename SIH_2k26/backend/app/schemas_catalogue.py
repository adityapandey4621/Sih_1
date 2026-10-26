import uuid
from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

from app.domain import Classification, DeadlineType, ExtractionKind, FieldKind, ItemType, SourceVerification, is_valid_academic_year

Key = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9_-]{0,79}$")]
Text300 = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)]
LongText = Annotated[str, StringConstraints(strip_whitespace=True, max_length=4000)]
OptText300 = Annotated[str, StringConstraints(strip_whitespace=True, max_length=300)] | None


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ---- sources / categories / rule sets ----------------------------------------------------------


class SourceIn(BaseModel):
    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=400)]
    issuing_authority: Text300
    url: Annotated[str, StringConstraints(strip_whitespace=True, max_length=1000, pattern=r"^https?://\S+$")] | None = None
    document_ref: OptText300 = None
    clause_ref: OptText300 = None
    page_ref: Annotated[str, StringConstraints(strip_whitespace=True, max_length=100)] | None = None
    publication_date: date | None = None
    effective_date: date | None = None
    retrieved_date: date | None = None
    verification_status: SourceVerification
    applicability_notes: LongText | None = None


class SourceOut(ORM):
    id: uuid.UUID
    title: str
    issuing_authority: str
    url: str | None
    document_ref: str | None
    clause_ref: str | None
    page_ref: str | None
    publication_date: date | None
    effective_date: date | None
    retrieved_date: date | None
    verification_status: str
    applicability_notes: str | None
    locked: bool = False  # cited by a version that is in review/published: cannot be edited


class CategoryIn(BaseModel):
    key: Key
    name: Text300
    description: LongText = ""
    applicability_note: LongText | None = None
    source_id: uuid.UUID | None = None
    is_active: bool = True


class CategoryOut(ORM):
    id: uuid.UUID
    key: str
    name: str
    description: str
    applicability_note: str | None
    source_id: uuid.UUID | None
    is_active: bool


class RuleSetCreateIn(BaseModel):
    key: Key
    title: Text300
    category_id: uuid.UUID
    classification: Classification


# ---- version content ---------------------------------------------------------------------------


class ExtractionFieldIn(BaseModel):
    """A value the processing layer should look for in a document supplied for this checklist item."""

    field_key: Key
    label: Text300
    kind: ExtractionKind = ExtractionKind.TEXT
    # Words that precede the value on the page, e.g. "Name of the Institution". Matching is case/punctuation-insensitive.
    anchors: list[Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=100)]] = Field(min_length=1, max_length=10)
    required: bool = True  # absence is reported as a preliminary issue for review (never as a deficiency)
    # Values sharing a compare_group are compared across documents; compare_to_application compares with the application record.
    compare_group: Key | None = None
    compare_to_application: Literal["institution_name"] | None = None


class ChecklistItemIn(BaseModel):
    item_key: Key
    label: Text300
    explanation: LongText = ""
    required: bool = True
    item_type: ItemType
    field_kind: FieldKind | None = None
    options: list[Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]] | None = None
    validation: dict[str, int | float] | None = None
    applies_when: dict | None = None
    source_id: uuid.UUID | None = None
    clause_ref: OptText300 = None
    display_order: int = 0
    extraction: list[ExtractionFieldIn] | None = Field(default=None, max_length=30)

    @model_validator(mode="after")
    def _shape(self) -> "ChecklistItemIn":
        if self.extraction:
            if self.item_type != ItemType.DOCUMENT_UPLOAD:
                raise ValueError("Extraction fields apply to document uploads only.")
            keys = [f.field_key for f in self.extraction]
            if len(set(keys)) != len(keys):
                raise ValueError("Duplicate extraction field keys.")
        if self.item_type == ItemType.FORM_FIELD:
            if self.field_kind is None:
                raise ValueError("A form field needs a field kind.")
            if self.field_kind == FieldKind.SELECT:
                if not self.options or len(set(self.options)) != len(self.options):
                    raise ValueError("A select field needs unique options.")
            elif self.options:
                raise ValueError("Only select fields take options.")
        elif self.field_kind or self.options or self.validation:
            raise ValueError("Field kind, options and validation apply to form fields only.")
        if self.validation:
            allowed = {"min_length", "max_length", "min", "max"}
            if not set(self.validation) <= allowed:
                raise ValueError(f"Unknown validation keys; allowed: {sorted(allowed)}.")
        if self.applies_when is not None:
            keys = set(self.applies_when)
            if "item_key" not in keys or not (keys & {"equals", "in"}) or not keys <= {"item_key", "equals", "in"}:
                raise ValueError('applies_when must be {"item_key": ..., "equals": ...} or {"item_key": ..., "in": [...]}.')
        return self


class RouteStageIn(BaseModel):
    stage_key: Key
    label: Text300
    description: LongText = ""
    display_order: int = 0
    is_conditional: bool = False
    condition_description: LongText | None = None
    next_stage_keys: list[Key] = Field(default_factory=list)
    source_id: uuid.UUID | None = None
    clause_ref: OptText300 = None


class DeadlinePolicyIn(BaseModel):
    policy_key: Key
    label: Text300
    deadline_type: DeadlineType
    trigger_event: Text300
    duration_value: Annotated[int, Field(ge=1, le=3650)] | None = None
    duration_unit: Literal["days", "hours"] | None = None
    calendar_convention: Literal["CALENDAR", "WORKING"] | None = None
    pause_resume_note: LongText | None = None
    escalation: dict | None = None
    source_id: uuid.UUID | None = None
    clause_ref: OptText300 = None

    @model_validator(mode="after")
    def _duration(self) -> "DeadlinePolicyIn":
        if (self.duration_value is None) != (self.duration_unit is None):
            raise ValueError("Duration value and unit must be given together (or both left empty when not established).")
        return self


class VersionContentIn(BaseModel):
    academic_years: list[str] = Field(default_factory=list, max_length=20)
    effective_from: date | None = None
    effective_to: date | None = None
    change_summary: LongText = ""
    source_ids: list[uuid.UUID] = Field(default_factory=list, max_length=50)
    checklist: list[ChecklistItemIn] = Field(default_factory=list, max_length=200)
    route: list[RouteStageIn] = Field(default_factory=list, max_length=50)
    deadlines: list[DeadlinePolicyIn] = Field(default_factory=list, max_length=50)
    row_version: int

    @field_validator("academic_years")
    @classmethod
    def _years(cls, v: list[str]) -> list[str]:
        for y in v:
            if not is_valid_academic_year(y):
                raise ValueError(f"'{y}' is not a valid academic year (expected e.g. 2026-27).")
        if len(set(v)) != len(v):
            raise ValueError("Duplicate academic years.")
        return v

    @model_validator(mode="after")
    def _unique(self) -> "VersionContentIn":
        if self.effective_from and self.effective_to and self.effective_from > self.effective_to:
            raise ValueError("Effective-from must not be after effective-to.")
        for name, keys in (
            ("checklist item keys", [i.item_key for i in self.checklist]),
            ("stage keys", [s.stage_key for s in self.route]),
            ("policy keys", [p.policy_key for p in self.deadlines]),
        ):
            if len(set(keys)) != len(keys):
                raise ValueError(f"Duplicate {name}.")
        return self


# ---- version output ----------------------------------------------------------------------------


class IssueOut(BaseModel):
    code: str
    message: str
    path: str = ""


class VersionEventOut(BaseModel):
    at: datetime
    actor: str | None
    action: str
    from_status: str | None
    to_status: str | None
    note: str | None


class RuleSetRef(BaseModel):
    id: uuid.UUID
    key: str
    title: str
    classification: str


class VersionSummaryOut(BaseModel):
    id: uuid.UUID
    rule_set: RuleSetRef
    category: CategoryOut
    version_number: int
    status: str
    academic_years: list[str]
    effective_from: date | None
    effective_to: date | None
    change_summary: str
    is_demo: bool
    verification: str  # summary of source verification: verified | unverified | demo
    created_at: datetime
    updated_at: datetime
    published_at: datetime | None


class ChecklistItemOut(ORM):
    item_key: str
    label: str
    explanation: str
    required: bool
    item_type: str
    field_kind: str | None
    options: list[str] | None
    validation: dict | None
    applies_when: dict | None
    source_id: uuid.UUID | None
    clause_ref: str | None
    display_order: int
    extraction: list[dict] | None = None


class RouteStageOut(ORM):
    stage_key: str
    label: str
    description: str
    display_order: int
    is_conditional: bool
    condition_description: str | None
    next_stage_keys: list[str]
    source_id: uuid.UUID | None
    clause_ref: str | None


class DeadlinePolicyOut(ORM):
    policy_key: str
    label: str
    deadline_type: str
    trigger_event: str
    duration_value: int | None
    duration_unit: str | None
    calendar_convention: str | None
    pause_resume_note: str | None
    escalation: dict | None
    source_id: uuid.UUID | None
    clause_ref: str | None


class VersionDetailOut(VersionSummaryOut):
    row_version: int
    created_by: str | None
    submitted_by: str | None
    submitted_at: datetime | None
    review_note: str | None
    published_by: str | None
    retired_by: str | None
    retired_at: datetime | None
    retirement_reason: str | None
    supersedes_version_id: uuid.UUID | None
    content_hash: str | None
    sources: list[SourceOut]
    checklist: list[ChecklistItemOut]
    route: list[RouteStageOut]
    deadlines: list[DeadlinePolicyOut]
    events: list[VersionEventOut]
    publication_issues: list[IssueOut]
    demo_banner: str | None


class RuleSetOut(BaseModel):
    id: uuid.UUID
    key: str
    title: str
    classification: str
    category: CategoryOut
    versions: list[VersionSummaryOut]


# ---- transitions -------------------------------------------------------------------------------

Note = Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)]


class SubmitReviewIn(BaseModel):
    row_version: int
    note: Note | None = None


class RequestChangesIn(BaseModel):
    note: Annotated[str, StringConstraints(strip_whitespace=True, min_length=5, max_length=2000)]


class PublishIn(BaseModel):
    supersede: bool = False  # explicitly retire an overlapping published version of the same rule set
    note: Note | None = None


class RetireIn(BaseModel):
    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=5, max_length=2000)]


class GapOut(BaseModel):
    id: uuid.UUID
    category: CategoryOut
    academic_year: str
    reason: str
    status: str
    report_count: int
    first_reported_at: datetime
    last_reported_at: datetime
    resolution_note: str | None


class GapResolveIn(BaseModel):
    note: Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=1000)]
