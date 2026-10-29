"""Request bodies for the decision, ratification and appeal endpoints. Responses are built by app/decision_presenters.py (docs/DECISIONS_AND_APPEALS.md)."""

import uuid
from typing import Annotated

from pydantic import BaseModel, Field

Key = Annotated[str, Field(min_length=8, max_length=80)]


class _Strict(BaseModel):
    model_config = {"extra": "forbid"}


class ObservationIn(_Strict):
    text: Annotated[str, Field(min_length=1, max_length=20000)]
    idempotency_key: Key


class DecisionIn(_Strict):
    outcome: Annotated[str, Field(min_length=1, max_length=3)]
    reasons: Annotated[str, Field(min_length=1, max_length=20000)]
    speaking_order: Annotated[str, Field(max_length=20000)] | None = None
    meeting_reference: Annotated[str, Field(max_length=200)] | None = None
    considered: list[Annotated[str, Field(max_length=20)]] = Field(default_factory=list, max_length=10)
    idempotency_key: Key
    row_version: int


class DecisionCorrectionIn(DecisionIn):
    correction_reason: Annotated[str, Field(min_length=1, max_length=4000)]


class RatificationIn(_Strict):
    outcome: Annotated[str, Field(min_length=1, max_length=24)]
    reasons: Annotated[str, Field(min_length=1, max_length=8000)]
    reference: Annotated[str, Field(max_length=300)] | None = None
    correction_reason: Annotated[str, Field(max_length=4000)] | None = None
    idempotency_key: Key


class CloseIn(_Strict):
    row_version: int


class AppealIn(_Strict):
    grounds: Annotated[str, Field(min_length=1, max_length=20000)]
    idempotency_key: Key


class AppealLinkIn(_Strict):
    url: Annotated[str, Field(max_length=1000)]
    description: Annotated[str, Field(max_length=500)]


class CouncilAppealDecisionIn(_Strict):
    outcome: Annotated[str, Field(min_length=1, max_length=24)]
    reasons: Annotated[str, Field(min_length=1, max_length=20000)]
    speaking_order: Annotated[str, Field(max_length=20000)] | None = None
    reference: Annotated[str, Field(max_length=300)] | None = None
    correction_reason: Annotated[str, Field(max_length=4000)] | None = None
    idempotency_key: Key


_ = uuid
