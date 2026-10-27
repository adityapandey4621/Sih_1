"""The external synchronisation boundary (AICTE's portal). Production integration is NOT part of this phase.

Truthfulness rules:
  * With no authorised provider, a sync row is `NOT_CONFIGURED`: nothing was sent and nothing is claimed.
  * With a provider, an attempt that fails or is not confirmed leaves `SYNC_PENDING` (with the error and attempt count) and is retried by
    `python -m app.reconcile_decisions`. It is safe to retry: one row per (subject, kind), and the payload carries a stable hash the receiver can de-duplicate on.
  * `SYNCED` is set only when the provider CONFIRMS receipt. The institution is never told AICTE's portal was updated before that.
  * A failed or missing sync never undoes the decision that caused it.
"""

import hashlib
import json
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit
from app.domain import SyncStatus
from app.models import utcnow
from app.models_applications import Application
from app.models_decisions import CouncilDecision, DecisionCase, EcDecision, ExternalSync, OfficialIssuance

NOT_CONFIGURED_NOTE = "No authorised integration with AICTE's portal is configured. Nothing was sent; AICTE's portal has NOT been updated by this system."


@dataclass
class SyncResult:
    confirmed: bool
    external_ref: str | None = None
    error: str | None = None


class PortalSyncProvider:
    configured = False

    def push(self, kind: str, payload: dict) -> SyncResult:  # pragma: no cover - interface
        raise NotImplementedError


class NotConfiguredProvider(PortalSyncProvider):
    configured = False

    def push(self, kind: str, payload: dict) -> SyncResult:
        return SyncResult(False, error=NOT_CONFIGURED_NOTE)


_NONE = NotConfiguredProvider()
PROVIDER_OVERRIDE: PortalSyncProvider | None = None  # tests install a scripted double; production code never sets it


def provider() -> PortalSyncProvider:
    return PROVIDER_OVERRIDE or _NONE


def payload_for(db: Session, row: ExternalSync) -> dict:
    app = db.get(Application, row.application_id)
    base = {"application_reference": app.reference, "kind": row.kind, "subject_id": str(row.subject_id)}
    if row.subject_type == "official_issuance":
        iss = db.get(OfficialIssuance, row.subject_id)
        dec = db.get(EcDecision, iss.decision_id)
        return {**base, "outcome": dec.outcome, "decision_version": dec.version_number, "decision_hash": dec.content_hash, "issued_at": iss.issued_at.isoformat(), "document_reference": iss.document_reference, "document_sha256": iss.sha256}
    cd = db.get(CouncilDecision, row.subject_id)
    return {**base, "council_kind": cd.kind, "outcome": cd.outcome, "recorded_at": cd.recorded_at.isoformat(), "reference": cd.reference, "content_hash": cd.content_hash}


def _hash(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def record(db: Session, application_id: uuid.UUID, subject_type: str, subject_id: uuid.UUID, kind: str) -> ExternalSync:
    """Create (once) the sync record for a subject and make the first attempt if a provider is configured. Never raises: a sync problem must not undo a decision."""
    row = db.scalar(select(ExternalSync).where(ExternalSync.subject_type == subject_type, ExternalSync.subject_id == subject_id, ExternalSync.kind == kind))
    if row is not None:
        return row
    row = ExternalSync(application_id=application_id, subject_type=subject_type, subject_id=subject_id, kind=kind, status=SyncStatus.NOT_CONFIGURED.value, payload_hash="")
    db.add(row)
    db.flush()
    row.payload_hash = _hash(payload_for(db, row))
    attempt(db, row)
    return row


def attempt(db: Session, row: ExternalSync) -> ExternalSync:
    prov = provider()
    if not prov.configured:
        row.status = SyncStatus.NOT_CONFIGURED.value
        return row
    payload = payload_for(db, row)
    row.attempts += 1
    row.last_attempt_at = utcnow()
    try:
        res = prov.push(row.kind, {**payload, "payload_hash": row.payload_hash})
    except Exception as exc:  # noqa: BLE001 - a provider fault is a recorded, retryable outcome
        res = SyncResult(False, error=f"{type(exc).__name__}")
    if res.confirmed:
        row.status, row.external_ref, row.confirmed_at, row.last_error = SyncStatus.SYNCED.value, res.external_ref, utcnow(), None
    else:
        row.status, row.last_error = SyncStatus.SYNC_PENDING.value, (res.error or "The external system did not confirm receipt.")[:500]
    audit.record(db, "sync.attempted", target_type=row.subject_type, target_id=row.subject_id, detail={"kind": row.kind, "status": row.status, "attempts": row.attempts, "error": row.last_error})
    return row


def reconcile(db: Session) -> int:
    """Retry every pending sync. Rows that were NOT_CONFIGURED when created are picked up if a provider has since been configured. Idempotent."""
    if not provider().configured:
        return 0
    done = 0
    for row in db.scalars(select(ExternalSync).where(ExternalSync.status.in_((SyncStatus.SYNC_PENDING.value, SyncStatus.NOT_CONFIGURED.value))).order_by(ExternalSync.created_at)):
        before = row.status
        attempt(db, row)
        done += int(before != row.status and row.status == SyncStatus.SYNCED.value)
    db.commit()
    return done


def state_view(row: ExternalSync | None) -> dict | None:
    if row is None:
        return None
    words = {
        SyncStatus.NOT_CONFIGURED.value: NOT_CONFIGURED_NOTE,
        SyncStatus.SYNC_PENDING.value: "AICTE's portal has not confirmed receipt. The system will retry; it does NOT show the portal as updated.",
        SyncStatus.SYNCED.value: "AICTE's portal confirmed receipt.",
    }
    return {"status": row.status, "message": words[row.status], "attempts": row.attempts, "last_error": row.last_error, "last_attempt_at": row.last_attempt_at, "confirmed_at": row.confirmed_at, "external_ref": row.external_ref}


def sync_for(db: Session, subject_type: str, subject_id: uuid.UUID) -> ExternalSync | None:
    return db.scalar(select(ExternalSync).where(ExternalSync.subject_type == subject_type, ExternalSync.subject_id == subject_id).order_by(ExternalSync.created_at.desc()))


_ = DecisionCase
