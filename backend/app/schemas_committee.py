"""Request bodies for the EVC / SHC endpoints. Responses are built by app/committee_presenters.py and documented in docs/API.md."""

import uuid
from typing import Annotated

from pydantic import BaseModel, Field, field_validator

Key = Annotated[str, Field(min_length=8, max_length=80)]
Reason = Annotated[str, Field(min_length=1, max_length=2000)]
Text = Annotated[str, Field(max_length=20000)]


class _Strict(BaseModel):
    model_config = {"extra": "forbid"}


class SelectionIn(_Strict):
    user_ids: list[uuid.UUID] = Field(default_factory=list, max_length=20)  # demonstration selection only; the official process takes none
    idempotency_key: Key


class ReselectIn(_Strict):
    reason: Reason
    row_version: int


class CancelIn(_Strict):
    reason: Reason


class ScheduleIn(_Strict):
    start_local: Annotated[str, Field(min_length=10, max_length=32)]
    timezone: Annotated[str, Field(min_length=1, max_length=64)]
    mode: Annotated[str, Field(min_length=1, max_length=12)]
    mode_reason: Annotated[str, Field(max_length=2000)] | None = None
    location: Annotated[str, Field(max_length=2000)] | None = None
    access_details: Annotated[str, Field(max_length=2000)] | None = None
    idempotency_key: Key


class RescheduleIn(ScheduleIn):
    reason: Reason


class ConfirmIn(_Strict):
    row_version: int


class VersionIn(_Strict):
    row_version: int


class ReasonVersionIn(_Strict):
    reason: Reason
    row_version: int


class SummaryIn(_Strict):
    summary: Reason
    row_version: int


class DetermineIn(_Strict):
    stage_key: str | None = Field(default=None, max_length=80)
    reason: Reason


class DocRef(_Strict):
    document_id: uuid.UUID
    page: int | None = Field(default=None, ge=1, le=100000)


class ObservationIn(_Strict):
    status: str
    note: Annotated[str, Field(max_length=8000)]
    internal_note: Annotated[str, Field(max_length=8000)] | None = None
    shortcoming: bool = False
    document_refs: list[DocRef] = Field(default_factory=list, max_length=20)
    row_version: int | None = None


class EvidenceLinkIn(_Strict):
    url: Annotated[str, Field(max_length=1000)]
    description: Annotated[str, Field(max_length=500)]
    item_key: str | None = Field(default=None, max_length=80)


class ReportDraftIn(_Strict):
    sections: dict[str, Text]
    row_version: int

    @field_validator("sections")
    @classmethod
    def _few(cls, v: dict) -> dict:
        if len(v) > 30:
            raise ValueError("Too many sections.")
        return v


class SubmitIn(_Strict):
    idempotency_key: Key
    row_version: int


class RequirementIn(_Strict):
    title: Annotated[str, Field(max_length=300)]
    description: Annotated[str, Field(max_length=8000)]
    requested_evidence: Annotated[str, Field(max_length=4000)] = ""
    item_key: str | None = Field(default=None, max_length=80)
    source_ref_key: str | None = Field(default=None, max_length=200)
    origin: dict | None = None


class RequirementPatchIn(_Strict):
    title: Annotated[str, Field(max_length=300)] | None = None
    description: Annotated[str, Field(max_length=8000)] | None = None
    requested_evidence: Annotated[str, Field(max_length=4000)] | None = None
    item_key: str | None = Field(default=None, max_length=80)
    source_ref_key: str | None = Field(default=None, max_length=200)
    row_version: int


class ComplianceRequestIn(_Strict):
    instructions: Annotated[str, Field(max_length=8000)]
    row_version: int


class FindingIn(_Strict):
    requirement_id: uuid.UUID | None = None
    assessment: str
    text: Annotated[str, Field(max_length=8000)]


class FindingPatchIn(_Strict):
    requirement_id: uuid.UUID | None = None
    assessment: str | None = None
    text: Annotated[str, Field(max_length=8000)] | None = None
    row_version: int


class ProceedingIn(_Strict):
    held_local: Annotated[str, Field(min_length=10, max_length=32)]
    timezone: Annotated[str, Field(min_length=1, max_length=64)]
    participants: Annotated[str, Field(max_length=2000)] = ""
    text: Annotated[str, Field(max_length=20000)]
    schedule_id: uuid.UUID | None = None


class RecommendationDraftIn(_Strict):
    option: str | None = Field(default=None, max_length=80)
    sections: dict[str, Text]
    row_version: int


class RespItemIn(_Strict):
    requirement_id: uuid.UUID
    explanation: Annotated[str, Field(max_length=8000)] = ""


class ComplianceDraftIn(_Strict):
    explanation: Annotated[str, Field(max_length=8000)] | None = None
    items: list[RespItemIn] = Field(default_factory=list, max_length=100)
    row_version: int | None = None


class ComplianceSubmitIn(_Strict):
    idempotency_key: Key
    row_version: int
