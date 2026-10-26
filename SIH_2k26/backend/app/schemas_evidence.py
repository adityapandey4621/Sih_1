import uuid
from datetime import datetime

from pydantic import BaseModel


class DocumentStateOut(BaseModel):
    """What is true of one file version, in words. Technical state only: never 'verified' or 'accepted'."""

    code: str  # stable key, e.g. extraction_complete, review_recommended, technical_failed
    label: str
    explanation: str
    tone: str  # neutral | info | success | warning | error
    terminal: bool  # false while processing is still expected: the UI keeps polling
    can_retry: bool
    technical_status: str | None
    scan_status: str | None
    scan_note: str | None
    extraction_status: str | None
    page_count: int | None
    text_method: str | None
    review_recommended: bool
    error_code: str | None


class VersionOut(BaseModel):
    id: uuid.UUID
    item_key: str
    version_number: int
    original_filename: str
    media_type: str
    size_bytes: int
    sha256: str
    uploaded_at: datetime
    uploaded_by: str | None
    is_current: bool
    previous_version_id: uuid.UUID | None
    state: DocumentStateOut


class PageOut(BaseModel):
    page_number: int
    method: str
    status: str
    confidence: float | None
    char_count: int
    note: str | None
    text: str | None = None  # included only on the extraction endpoint


class FieldOut(BaseModel):
    field_key: str
    label: str
    kind: str
    status: str  # EXTRACTED | UNCERTAIN | NOT_FOUND ("unable to extract")
    value: str | None
    normalised_value: str | None
    page_number: int | None
    method: str | None
    confidence: float | None
    evidence: str | None
    note: str | None
    document_id: uuid.UUID
    version_number: int


class FindingOut(BaseModel):
    id: uuid.UUID
    issue_type: str
    label: str
    status: str  # PRELIMINARY_ISSUE
    priority: str | None  # review priority; hidden from institutions
    check_key: str | None
    item_key: str | None
    document_id: uuid.UUID | None
    version_number: int | None
    pages: list[int]
    values: list[dict]
    explanation: str
    plain_text: str  # what the institution is told
    rule_ref: str | None
    review_recommended: bool
    created_at: datetime
    disclaimer: str


class EventOut(BaseModel):
    at: datetime
    actor: str | None
    action: str
    detail: dict | None


class ItemEvidenceOut(BaseModel):
    item_key: str
    label: str
    explanation: str
    required: bool
    clause_ref: str | None
    configured_fields: list[str]  # labels of values the configuration asks the extractor to look for
    slot_state: DocumentStateOut | None  # None = no file submitted (distinct from an unreadable file)
    current: VersionOut | None
    versions: list[VersionOut]
    fields: list[FieldOut]
    pages: list[PageOut]
    findings: list[FindingOut]


class ApplicationEvidenceOut(BaseModel):
    application_id: uuid.UUID
    reference: str
    status: str
    academic_year: str
    category_name: str
    institution_name: str | None  # reviewers only
    viewer: str  # owner | reviewer
    items: list[ItemEvidenceOut]
    cross_document_findings: list[FindingOut]
    disclaimer: str
    ocr_available: bool
    malware_scanner: str


class ExtractionOut(BaseModel):
    document_id: uuid.UUID
    version_number: int
    state: DocumentStateOut
    pages: list[PageOut]
    fields: list[FieldOut]


class DocumentDetailOut(BaseModel):
    version: VersionOut
    application_id: uuid.UUID
    reference: str
    item_label: str
    versions: list[VersionOut]
    viewer: str


class ReviewListItem(BaseModel):
    id: uuid.UUID
    reference: str
    institution_name: str
    category_name: str
    academic_year: str
    submitted_at: datetime | None
    documents: int
    still_processing: int
    review_recommended: int
    open_findings: int
