"""Request and response shapes for scrutiny, notices, responses and notifications.

Two families, kept apart on purpose:
  * `*Out` models for staff include internal fields (reviewer descriptions, reasons, issuer reasons).
  * `Inst*` models are an explicit whitelist for the institution. A field that is not declared here cannot reach it.
"""

import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field

Text = Annotated[str, Field(max_length=8000)]
Short = Annotated[str, Field(max_length=300)]
IssueType = Literal["missing_document", "unreadable_document", "inconsistent_information", "incomplete_information", "other"]


# ---- requests -----------------------------------------------------------------------------------


class AssignIn(BaseModel):
    user_id: uuid.UUID
    reason: Short | None = None
    row_version: int


class StartIn(BaseModel):
    row_version: int


class NotesIn(BaseModel):
    notes: Text
    row_version: int


class FindingIn(BaseModel):
    issue_type: IssueType
    description: Text
    item_key: Annotated[str, Field(max_length=80)] | None = None
    document_id: uuid.UUID | None = None
    page_number: Annotated[int, Field(ge=1, le=100_000)] | None = None
    institution_text: Text | None = None
    requested_correction: Text | None = None
    source_ref_key: Annotated[str, Field(max_length=120)] | None = None
    visibility: Literal["INTERNAL_ONLY", "ELIGIBLE_FOR_NOTICE"] = "INTERNAL_ONLY"
    preliminary_finding_id: uuid.UUID | None = None


class FindingPatchIn(BaseModel):
    row_version: int
    issue_type: IssueType | None = None
    description: Text | None = None
    item_key: Annotated[str, Field(max_length=80)] | None = None
    document_id: uuid.UUID | None = None
    page_number: Annotated[int, Field(ge=1, le=100_000)] | None = None
    institution_text: Text | None = None
    requested_correction: Text | None = None
    source_ref_key: Annotated[str, Field(max_length=120)] | None = None
    visibility: Literal["INTERNAL_ONLY", "ELIGIBLE_FOR_NOTICE"] | None = None


class DispositionIn(BaseModel):
    state: Literal["RESOLVED", "UNRESOLVED", "NOT_SUPPORTED"]
    reason: Text
    row_version: int


class NoticeCreateIn(BaseModel):
    finding_ids: list[uuid.UUID] = Field(min_length=1, max_length=50)
    summary: Text = ""


class NoticeItemEditIn(BaseModel):
    id: uuid.UUID
    title: Short | None = None
    description: Text | None = None
    requested_correction: Text | None = None


class NoticeUpdateIn(BaseModel):
    row_version: int
    summary: Text | None = None
    items: list[NoticeItemEditIn] | None = None  # omit an existing item to remove it; omit the whole field to leave items alone
    add_finding_ids: list[uuid.UUID] = Field(default_factory=list, max_length=50)


class NoticeSubmitIn(BaseModel):
    row_version: int


class ReasonIn(BaseModel):
    reason: Text


class IssueIn(BaseModel):
    content_hash: Annotated[str, Field(min_length=64, max_length=64)]
    issue_reason: Text
    acknowledge_unsourced: bool = False
    idempotency_key: Annotated[str, Field(min_length=8, max_length=80)]
    row_version: int


class CompleteIn(BaseModel):
    row_version: int
    summary: Text
    acknowledge_unresolved: bool = False


class DetermineIn(BaseModel):
    stage_key: Annotated[str, Field(max_length=80)] | None = None
    reason: Text


class ResponseItemIn(BaseModel):
    notice_item_id: uuid.UUID
    explanation: Text = ""


class ResponseDraftIn(BaseModel):
    explanation: Text | None = None
    items: list[ResponseItemIn] = Field(default_factory=list, max_length=100)
    row_version: int | None = None


class ResponseSubmitIn(BaseModel):
    idempotency_key: Annotated[str, Field(min_length=8, max_length=80)]
    row_version: int


# ---- staff responses ----------------------------------------------------------------------------


class RuleVersionRef(BaseModel):
    rule_set: str
    number: int
    classification: str
    demo_banner: str | None = None


class ProcessingSummary(BaseModel):
    documents: int
    still_processing: int
    failed: int
    review_recommended: int


class QueueItemOut(BaseModel):
    id: uuid.UUID
    application_id: uuid.UUID
    reference: str
    institution_name: str
    category_name: str
    academic_year: str
    state: str
    state_label: str
    assignees: list[str]
    submitted_at: datetime | None
    updated_at: datetime
    outstanding_actions: list[str]
    due_at: datetime | None
    due_basis: str | None
    rule_version: RuleVersionRef
    processing: ProcessingSummary
    round_number: int | None
    round_kind: str | None
    exception: str | None
    handoff_status: str | None
    handoff_stage: str | None
    row_version: int


class QueuePage(BaseModel):
    items: list[QueueItemOut]
    total: int
    page: int
    page_size: int
    counts: dict[str, int]  # per case state, across everything the viewer may see (not narrowed by filters)
    state_labels: dict[str, str]  # the wording for each state, owned by the server
    processing_exceptions: int
    scope: str  # coordinator | reviewer | issuer


class SelectionOut(BaseModel):
    mode: str
    available: bool
    label: str
    notice: str


class AssignmentOut(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    user_name: str
    source: str
    is_demo: bool
    made_by: str
    selection_ref: dict | None
    reason: str | None
    created_at: datetime
    revoked_at: datetime | None
    revoked_reason: str | None


class RoundOut(BaseModel):
    id: uuid.UUID
    number: int
    kind: str
    status: str
    opened_at: datetime
    started_at: datetime | None
    closed_at: datetime | None
    triggered_by_notice: str | None
    outcome: str | None
    outcome_reason: str | None
    review_notes: str | None
    review_notes_at: datetime | None
    row_version: int


class FindingEventOut(BaseModel):
    at: datetime
    actor: str
    action: str
    from_state: str | None
    to_state: str | None
    reason: str | None
    round_number: int


class FindingOut(BaseModel):
    id: uuid.UUID
    sequence: int
    round_number: int
    item_key: str | None
    item_label: str | None
    document_id: uuid.UUID | None
    document_version: int | None
    document_current_version: int | None
    document_superseded: bool
    page_number: int | None
    issue_type: str
    issue_label: str
    description: str  # internal
    institution_text: str | None
    requested_correction: str | None
    source_ref: dict | None
    source_ref_status: str
    preliminary_finding_id: uuid.UUID | None
    visibility: str
    state: str
    state_reason: str | None
    created_by: str
    created_at: datetime
    updated_at: datetime
    row_version: int
    notice_reference: str | None
    history: list[FindingEventOut]


class ResponseDocOut(BaseModel):
    document_id: uuid.UUID
    version_number: int
    filename: str
    uploaded_at: datetime
    current: bool
    state_label: str


class NoticeItemOut(BaseModel):
    id: uuid.UUID
    position: int
    finding_id: uuid.UUID
    finding_state: str
    title: str
    description: str
    requested_correction: str
    item_key: str | None
    document_id: uuid.UUID | None
    document_version: int | None
    document_current_version: int | None
    page_number: int | None
    source_ref: dict | None
    source_ref_status: str
    response_state: str
    response_explanation: str
    response_documents: list[ResponseDocOut]


class StaffResponseOut(BaseModel):
    id: uuid.UUID
    status: str
    explanation: str
    submitted_by: str | None
    submitted_at: datetime | None
    after_due_date: bool


class NoticeStaffOut(BaseModel):
    id: uuid.UUID
    reference: str
    sequence: int
    previous_reference: str | None
    status: str
    round_number: int
    summary: str
    items: list[NoticeItemOut]
    prepared_by: str
    issued_by: str | None
    issuer_role: str | None
    issued_at: datetime | None
    issue_reason: str | None
    returned_reason: str | None
    withdrawn_reason: str | None
    response_due_at: datetime | None
    policy_resolution: dict | None
    template: dict | None
    content_hash: str | None
    authority: dict | None
    row_version: int
    response: StaffResponseOut | None


class NoticePreviewOut(BaseModel):
    notice_id: uuid.UUID
    content: dict
    content_hash: str
    policy: dict
    blockers: list[dict]
    unsourced_positions: list[int]
    can_issue: bool
    row_version: int
    notice_status: str


class SourceOptionOut(BaseModel):
    key: str
    label: str
    clause_ref: str | None
    verification_status: str | None
    origin: str


class CaseEventOut(BaseModel):
    id: uuid.UUID
    at: datetime
    actor: str
    action: str
    object_type: str | None
    object_id: str | None
    from_state: str | None
    to_state: str | None
    reason: str | None
    detail: dict | None


class HandoffOut(BaseModel):
    id: uuid.UUID
    status: str
    from_stage_key: str | None
    to_stage_key: str | None
    to_stage_label: str | None
    reason: str | None
    options: list[dict]
    determined_by: str | None
    determined_at: datetime | None
    determination_reason: str | None
    created_at: datetime
    module_available: bool  # whether the next stage's workflow exists in this release and has been activated (EVC / SHC: Phase 5)
    activated_stage: str | None = None  # "EVC" | "SHC" once a committee case exists for the stage
    activated_case_id: uuid.UUID | None = None


class CaseDetailOut(BaseModel):
    queue: QueueItemOut
    scope: list[str]
    selection: SelectionOut
    assignments: list[AssignmentOut]
    rounds: list[RoundOut]
    findings: list[FindingOut]
    notices: list[NoticeStaffOut]
    source_options: list[SourceOptionOut]
    issue_types: dict[str, str]
    events: list[CaseEventOut]
    allowed_actions: list[str]
    completion: dict | None
    handoff: HandoffOut | None
    checklist: list[dict]  # requirement, required flag, and its current document version
    not_an_approval: str


class AssignableOut(BaseModel):
    id: uuid.UUID
    name: str
    email: str
    active_cases: int


class HandoffListItem(BaseModel):
    case_id: uuid.UUID
    reference: str
    institution_name: str
    category_name: str
    status: str
    to_stage_label: str | None
    reason: str | None
    completed_at: datetime | None


# ---- institution responses (whitelist) ----------------------------------------------------------


class InstResponseDoc(BaseModel):
    document_id: uuid.UUID
    version_number: int
    filename: str
    uploaded_at: datetime
    current: bool
    state_label: str


class InstItem(BaseModel):
    id: uuid.UUID
    position: int
    title: str
    description: str
    requested_correction: str
    item_key: str | None
    item_label: str | None
    accepts_files: bool
    document_version: int | None
    document_current_version: int | None
    page_number: int | None
    source_label: str | None
    source_note: str
    response_state: str
    response_explanation: str
    response_documents: list[InstResponseDoc]


class InstResponse(BaseModel):
    status: str
    explanation: str
    row_version: int
    submitted_at: datetime | None
    submitted_by: str | None


class InstNotice(BaseModel):
    id: uuid.UUID
    reference: str
    sequence: int
    previous_reference: str | None
    status: str
    status_label: str
    application_id: uuid.UUID
    application_reference: str
    issued_at: datetime | None
    response_due_at: datetime | None
    due_note: str
    demo_notice: str | None
    template_label: str | None
    summary: str
    items: list[InstItem]
    response: InstResponse | None
    can_respond: bool
    not_an_approval: str


class InstNoticeSummary(BaseModel):
    id: uuid.UUID
    reference: str
    sequence: int
    status: str
    status_label: str
    application_id: uuid.UUID
    application_reference: str
    issued_at: datetime | None
    response_due_at: datetime | None
    items: int
    items_answered: int


class InstEvent(BaseModel):
    at: datetime
    label: str


class InstScrutiny(BaseModel):
    application_id: uuid.UUID
    state_label: str
    explanation: str
    events: list[InstEvent]
    notices: list[InstNoticeSummary]
    not_an_approval: str


class NotificationOut(BaseModel):
    id: uuid.UUID
    event_type: str
    title: str
    body: str
    link: str | None
    created_at: datetime
    read: bool
    delivery_state: str
    external_delivery: str
    application_reference: str | None = None


class NotificationPage(BaseModel):
    items: list[NotificationOut]
    total: int
    unread: int
    page: int
    page_size: int
