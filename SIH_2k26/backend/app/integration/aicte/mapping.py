"""Internal records <-> contract messages, and external answers -> internal sync states. The only place the two vocabularies meet."""

import hashlib
import json
from datetime import datetime

from app.domain import SyncStatus
from app.integration.aicte.adapter import AdapterResult, Outcome
from app.integration.aicte.contract import CouncilBody, Envelope, EventCode, OutcomeBody
from app.models import utcnow

KIND_TO_EVENT: dict[str, EventCode] = {
    "DECISION_ISSUED": EventCode.OFFICIAL_OUTCOME_ISSUED,
    "RATIFICATION_RECORDED": EventCode.COUNCIL_RATIFICATION_RECORDED,
    "APPEAL_DECIDED": EventCode.COUNCIL_APPEAL_DECISION_RECORDED,
}

# What each adapter outcome means for the sync record. SYNCED is set by CONFIRMED and by nothing else.
OUTCOME_TO_STATUS: dict[Outcome, SyncStatus] = {
    Outcome.CONFIRMED: SyncStatus.SYNCED,
    Outcome.TRANSIENT_FAILURE: SyncStatus.SYNC_PENDING,
    Outcome.PERMANENT_FAILURE: SyncStatus.FAILED,
    Outcome.RECONCILIATION_REQUIRED: SyncStatus.RECONCILIATION_REQUIRED,
    Outcome.NOT_CONFIGURED: SyncStatus.NOT_CONFIGURED,
}


def payload_hash(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def _dt(v: object) -> datetime | None:
    return datetime.fromisoformat(str(v)) if v else None


def build_envelope(kind: str, message_id, payload: dict, digest: str) -> Envelope:
    """`payload` is the internal record (portal_sync.payload_for). Only references and hashes cross the boundary; never document content."""
    event = KIND_TO_EVENT[kind]
    if event == EventCode.OFFICIAL_OUTCOME_ISSUED:
        body = OutcomeBody(
            application_reference=payload["application_reference"], subject_id=payload["subject_id"], outcome=payload["outcome"], decision_version=payload.get("decision_version"),
            decision_hash=payload.get("decision_hash"), issued_at=_dt(payload.get("issued_at")), document_reference=payload.get("document_reference"), document_sha256=payload.get("document_sha256"),
        )
    else:
        body = CouncilBody(
            application_reference=payload["application_reference"], subject_id=payload["subject_id"], council_kind=payload["council_kind"], outcome=payload["outcome"],
            recorded_at=_dt(payload["recorded_at"]), reference=payload.get("reference"), content_hash=payload["content_hash"],
        )
    return Envelope(message_id=message_id, event=event, payload_hash=digest, sent_at=utcnow(), body=body.model_dump(mode="json"))


def status_for(result: AdapterResult) -> SyncStatus:
    return OUTCOME_TO_STATUS[result.outcome]
