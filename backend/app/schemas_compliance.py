"""Request bodies for compliance, validity and clock endpoints. Responses are built by app/compliance_presenters.py."""

import uuid
from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field, model_validator

Key = Annotated[str, Field(min_length=8, max_length=80)]


class _Strict(BaseModel):
    model_config = {"extra": "forbid"}


class DueIn(_Strict):
    basis: Literal["POLICY", "SPECIFIED", "NONE"] = "NONE"
    policy_key: Annotated[str, Field(max_length=80)] | None = None
    due_at: datetime | None = None
    authority: Annotated[str, Field(max_length=300)] | None = None

    @model_validator(mode="after")
    def _shape(self) -> "DueIn":
        if self.basis == "POLICY" and not self.policy_key:
            raise ValueError("Name the deadline policy to use.")
        if self.basis == "SPECIFIED" and (self.due_at is None or not (self.authority or "").strip()):
            raise ValueError("A specified date needs the date and where the decision states it.")
        if self.basis == "SPECIFIED" and self.due_at is not None and self.due_at.tzinfo is None:
            raise ValueError("Give the date with its time zone, for example 2027-03-31T23:59:59+05:30.")
        if self.basis != "SPECIFIED" and (self.due_at or self.authority):
            raise ValueError("A date and citation apply only to a specified due date.")
        return self


class ObligationIn(_Strict):
    source: Literal["EC_DECISION", "COUNCIL_APPEAL_DECISION"] = "EC_DECISION"  # which decision the condition follows from; never inferred
    title: Annotated[str, Field(min_length=3, max_length=200)]
    condition_text: Annotated[str, Field(min_length=5, max_length=8000)]
    condition_ref: Annotated[str, Field(max_length=300)] | None = None
    required_evidence: Annotated[str, Field(min_length=5, max_length=8000)]
    verifier_id: uuid.UUID
    due: DueIn = Field(default_factory=DueIn)
    idempotency_key: Key


class ReviewIn(_Strict):
    outcome: Literal["VERIFIED", "CORRECTION_REQUESTED", "NOT_MET"]
    submission_id: uuid.UUID
    reason: Annotated[str, Field(min_length=10, max_length=4000)]
    internal_note: Annotated[str, Field(max_length=4000)] | None = None
    row_version: int
    idempotency_key: Key


class WithdrawIn(_Strict):
    reason: Annotated[str, Field(min_length=5, max_length=2000)]
    row_version: int


class ReassignIn(_Strict):
    verifier_id: uuid.UUID
    reason: Annotated[str, Field(min_length=5, max_length=2000)]


class VerifyDateIn(_Strict):
    verified_date: date
    reason: Annotated[str, Field(min_length=10, max_length=4000)]
    internal_note: Annotated[str, Field(max_length=4000)] | None = None
    idempotency_key: Key


class RejectDateIn(_Strict):
    reason: Annotated[str, Field(min_length=10, max_length=4000)]
    internal_note: Annotated[str, Field(max_length=4000)] | None = None


class ResolveIn(_Strict):
    reason: Annotated[str, Field(min_length=10, max_length=4000)]


class PauseIn(_Strict):
    reason_code: Annotated[str, Field(max_length=60)] | None = None
    reason: Annotated[str, Field(min_length=5, max_length=2000)]
    reference: Annotated[str, Field(max_length=300)] | None = None
    row_version: int


class ResumeIn(_Strict):
    row_version: int
