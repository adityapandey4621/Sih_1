"""The message contract between this system and an external AICTE-side receiver. DEFINED BY THIS PROJECT; NOT AN AICTE SPECIFICATION.

Strict on purpose: unknown fields are refused on both sides, every message has an id the receiver can de-duplicate on, and the payload carries a hash of
itself so a receipt can be checked against what was sent. A receipt that does not echo the right message id and hash is never treated as confirmation.
Nothing in a message is a document's content: references and hashes only.
"""

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

CONTRACT_VERSION = "sih-demo-0.1"
CONTRACT_LABEL = "DEMO ONLY — a contract defined by this project. It is NOT an AICTE specification, and no AICTE endpoint is known to implement it."


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EventCode(StrEnum):
    OFFICIAL_OUTCOME_ISSUED = "OFFICIAL_OUTCOME_ISSUED"
    COUNCIL_RATIFICATION_RECORDED = "COUNCIL_RATIFICATION_RECORDED"
    COUNCIL_APPEAL_DECISION_RECORDED = "COUNCIL_APPEAL_DECISION_RECORDED"


class OutcomeBody(_Strict):
    application_reference: str = Field(max_length=40)
    subject_id: uuid.UUID
    outcome: str = Field(max_length=24)
    decision_version: int | None = None
    decision_hash: str | None = Field(default=None, min_length=64, max_length=64)
    issued_at: datetime | None = None
    document_reference: str | None = Field(default=None, max_length=300)
    document_sha256: str | None = Field(default=None, min_length=64, max_length=64)


class CouncilBody(_Strict):
    application_reference: str = Field(max_length=40)
    subject_id: uuid.UUID
    council_kind: Literal["RATIFICATION", "APPEAL_DECISION"]
    outcome: str = Field(max_length=24)
    recorded_at: datetime
    reference: str | None = Field(default=None, max_length=300)
    content_hash: str = Field(min_length=64, max_length=64)


BODY_FOR_EVENT: dict[EventCode, type[_Strict]] = {
    EventCode.OFFICIAL_OUTCOME_ISSUED: OutcomeBody,
    EventCode.COUNCIL_RATIFICATION_RECORDED: CouncilBody,
    EventCode.COUNCIL_APPEAL_DECISION_RECORDED: CouncilBody,
}


class Envelope(_Strict):
    contract_version: Literal["sih-demo-0.1"] = CONTRACT_VERSION  # type: ignore[assignment]
    message_id: uuid.UUID  # = our sync record's id: the same message is always sent under the same id
    event: EventCode
    payload_hash: str = Field(min_length=64, max_length=64)
    sent_at: datetime
    body: dict[str, Any]

    @model_validator(mode="after")
    def _body_matches_event(self) -> "Envelope":
        BODY_FOR_EVENT[self.event].model_validate(self.body)  # raises if the body is not exactly what this event carries
        return self


class Receipt(_Strict):
    """What the receiver answers to a message."""

    status: Literal["RECEIVED", "DUPLICATE", "REJECTED"]
    message_id: uuid.UUID
    payload_hash: str = Field(min_length=64, max_length=64)
    external_ref: str | None = Field(default=None, max_length=200)
    received_at: datetime | None = None
    reason: str | None = Field(default=None, max_length=300)

    @field_validator("reason")
    @classmethod
    def _strip(cls, v: str | None) -> str | None:
        return v.strip() if v else v


class StatusResponse(_Strict):
    message_id: uuid.UUID
    status: Literal["RECEIVED", "PROCESSED", "REJECTED", "UNKNOWN"]
    external_ref: str | None = Field(default=None, max_length=200)
    payload_hash: str | None = Field(default=None, min_length=64, max_length=64)


def json_schemas() -> dict[str, Any]:
    """Published for whoever would build the receiving side. Generated from the models above, so it cannot drift."""
    return {
        "label": CONTRACT_LABEL,
        "contract_version": CONTRACT_VERSION,
        "envelope": Envelope.model_json_schema(),
        "bodies": {e.value: m.model_json_schema() for e, m in BODY_FOR_EVENT.items()},
        "receipt": Receipt.model_json_schema(),
        "status": StatusResponse.model_json_schema(),
        "transport": {
            "submit": "POST {base}/v1/outcomes with the Envelope as JSON. Headers: Authorization: Bearer <key>, Idempotency-Key: <message_id>, X-Timestamp, X-Signature, X-Contract-Version.",
            "signature": "hex(HMAC-SHA256(secret, X-Timestamp + '.' + raw request body))",
            "status": "GET {base}/v1/messages/{message_id} -> StatusResponse",
            "confirmation": "Only a Receipt with status RECEIVED or DUPLICATE whose message_id and payload_hash equal what was sent counts as confirmation.",
        },
    }
