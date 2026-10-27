"""The synchronisation of decisions with an external AICTE-side system, through the integration boundary (app/integration/aicte). Production integration is NOT built:
no AICTE interface is known to exist (docs/INTEGRATION.md).

Truthfulness rules:
  * With no adapter configured, a sync row is `NOT_CONFIGURED`: nothing was sent and nothing is claimed.
  * With an adapter, `SYNCED` (shown as "confirmed") is set ONLY when the receiver returned a receipt that echoes our message id and payload hash.
  * `SYNC_PENDING`: not confirmed, safe to retry (the same message id and idempotency key are reused, so a retry cannot duplicate). Retries back off and are BOUNDED:
    after `sync_max_attempts` the row becomes `FAILED` and waits for an operator.
  * `FAILED`: the receiver refused the message, or attempts ran out. Needs a person; never retried automatically.
  * `RECONCILIATION_REQUIRED`: an answer arrived that cannot be trusted (garbled, wrong hash). We do not know what the receiver holds, so `reconcile` ASKS it
    (`status`) instead of sending again blindly.
  * A failed or missing sync never undoes the decision that caused it, and the institution is never told AICTE's portal was updated before it is confirmed.
"""

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit
from app.config import get_settings
from app.domain import SyncStatus
from app.integration.aicte import factory, mapping
from app.integration.aicte.adapter import Attempt, Outcome
from app.models import utcnow
from app.models_applications import Application
from app.models_decisions import CouncilDecision, DecisionCase, EcDecision, ExternalSync, OfficialIssuance
from app.models_integration import IntegrationLog
from app.security import as_utc

NOT_CONFIGURED_NOTE = "No authorised integration with AICTE's portal is configured. Nothing was sent; AICTE's portal has NOT been updated by this system."
MAX_BACKOFF = timedelta(hours=6)


@dataclass
class SyncResult:
    confirmed: bool
    external_ref: str | None = None
    error: str | None = None
    verdict: SyncStatus | None = None  # an explicit verdict (FAILED / RECONCILIATION_REQUIRED); otherwise confirmed -> SYNCED, not confirmed -> SYNC_PENDING
    error_code: str | None = None
    attempts: list[Attempt] = field(default_factory=list)


class PortalSyncProvider:
    configured = False
    name = "provider"
    demo = False

    def push(self, kind: str, payload: dict) -> SyncResult:  # pragma: no cover - interface
        raise NotImplementedError

    def check(self, payload: dict) -> SyncResult:  # pragma: no cover - interface
        """Ask the receiver what it holds for this message (used to settle RECONCILIATION_REQUIRED)."""
        return SyncResult(False, error="This provider cannot be asked what it holds.")


class NotConfiguredProvider(PortalSyncProvider):
    configured = False

    def push(self, kind: str, payload: dict) -> SyncResult:
        return SyncResult(False, error=NOT_CONFIGURED_NOTE)


class AdapterProvider(PortalSyncProvider):
    """Bridges the sync service to an integration adapter (mock or http)."""

    configured = True

    def __init__(self, adapter) -> None:  # noqa: ANN001
        self.adapter, self.name, self.demo = adapter, adapter.name, adapter.demo

    def push(self, kind: str, payload: dict) -> SyncResult:
        env = mapping.build_envelope(kind, uuid.UUID(payload["message_id"]), payload, payload["payload_hash"])
        res = self.adapter.submit(env)
        return SyncResult(
            res.outcome == Outcome.CONFIRMED, external_ref=res.external_ref, error=res.detail, verdict=None if res.outcome in (Outcome.CONFIRMED, Outcome.TRANSIENT_FAILURE) else mapping.status_for(res),
            error_code=res.error_code, attempts=res.attempts,
        )

    def check(self, payload: dict) -> SyncResult:
        st = self.adapter.status(uuid.UUID(payload["message_id"]))
        if not st.known:
            return SyncResult(False, error=f"The receiver could not be asked ({st.error_code or 'unknown'}).", error_code=st.error_code, attempts=st.attempts)
        if st.status in ("RECEIVED", "PROCESSED") and st.payload_hash == payload["payload_hash"] and st.external_ref:
            return SyncResult(True, external_ref=st.external_ref, attempts=st.attempts)
        if st.status == "UNKNOWN":  # the receiver does not hold it: it is safe to send again
            return SyncResult(False, error="The receiver does not hold this message; it will be sent again.", verdict=SyncStatus.SYNC_PENDING, attempts=st.attempts)
        return SyncResult(False, error="The receiver holds something that does not match what we sent.", verdict=SyncStatus.RECONCILIATION_REQUIRED, error_code="receipt_mismatch", attempts=st.attempts)


_NONE = NotConfiguredProvider()
PROVIDER_OVERRIDE: PortalSyncProvider | None = None  # tests install a scripted double; production code never sets it


def provider() -> PortalSyncProvider:
    if PROVIDER_OVERRIDE is not None:
        return PROVIDER_OVERRIDE
    adapter = factory.get_adapter()
    return AdapterProvider(adapter) if adapter is not None else _NONE


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


def _log(db: Session, row: ExternalSync, operation: str, adapter: str, attempts: list[Attempt]) -> None:
    for i, a in enumerate(attempts, start=1):
        db.add(IntegrationLog(sync_id=row.id, adapter=adapter[:20], operation=operation, message_id=row.id, attempt_no=i, outcome=a.outcome[:30], http_status=a.http_status, latency_ms=a.latency_ms, payload_hash=row.payload_hash))


def _apply(db: Session, row: ExternalSync, res: SyncResult, prov: PortalSyncProvider, now: datetime) -> None:
    settings = get_settings()
    verdict = res.verdict or (SyncStatus.SYNCED if res.confirmed else SyncStatus.SYNC_PENDING)
    row.adapter, row.last_outcome = prov.name, res.error_code
    if verdict == SyncStatus.SYNCED:
        row.status, row.external_ref, row.confirmed_at, row.last_error, row.next_attempt_at = SyncStatus.SYNCED.value, res.external_ref, now, None, None
    elif verdict in (SyncStatus.FAILED, SyncStatus.RECONCILIATION_REQUIRED):
        row.status, row.last_error, row.next_attempt_at = verdict.value, (res.error or "The external system did not confirm receipt.")[:500], None
    else:
        row.last_error = (res.error or "The external system did not confirm receipt.")[:500]
        if row.attempts >= settings.sync_max_attempts:
            row.status, row.next_attempt_at, row.last_error = SyncStatus.FAILED.value, None, f"Gave up after {row.attempts} attempts. Last problem: {row.last_error}"[:500]
        else:
            row.status = SyncStatus.SYNC_PENDING.value
            row.next_attempt_at = now + min(MAX_BACKOFF, timedelta(seconds=settings.sync_retry_backoff_seconds * (2 ** max(0, row.attempts - 1))))


def attempt(db: Session, row: ExternalSync, now: datetime | None = None) -> ExternalSync:
    prov = provider()
    if not prov.configured:
        row.status = SyncStatus.NOT_CONFIGURED.value
        return row
    now = now or utcnow()
    payload = payload_for(db, row)
    row.attempts += 1
    row.last_attempt_at = now
    try:
        res = prov.push(row.kind, {**payload, "payload_hash": row.payload_hash, "message_id": str(row.id)})
    except Exception as exc:  # noqa: BLE001 - a provider fault is a recorded, retryable outcome
        res = SyncResult(False, error=f"{type(exc).__name__}", error_code=type(exc).__name__)
    _log(db, row, "submit", prov.name, res.attempts)
    _apply(db, row, res, prov, now)
    audit.record(db, "sync.attempted", target_type=row.subject_type, target_id=row.subject_id, detail={"kind": row.kind, "status": row.status, "attempts": row.attempts, "error": row.last_error})
    return row


def settle(db: Session, row: ExternalSync, now: datetime | None = None) -> ExternalSync:
    """RECONCILIATION_REQUIRED: ask the receiver what it holds. Confirmed -> SYNCED; not held -> SYNC_PENDING (safe to send again); a mismatch stays for a person."""
    prov = provider()
    if not prov.configured:
        return row
    now = now or utcnow()
    payload = {**payload_for(db, row), "payload_hash": row.payload_hash, "message_id": str(row.id)}
    try:
        res = prov.check(payload)
    except Exception as exc:  # noqa: BLE001
        res = SyncResult(False, error=type(exc).__name__)
    _log(db, row, "status", prov.name, res.attempts)
    if res.verdict == SyncStatus.SYNC_PENDING:
        row.status, row.last_error, row.next_attempt_at = SyncStatus.SYNC_PENDING.value, res.error, now
    elif res.verdict == SyncStatus.RECONCILIATION_REQUIRED or (not res.confirmed and res.verdict is None):
        row.last_error = (res.error or row.last_error or "")[:500]  # stays RECONCILIATION_REQUIRED: a person looks
    else:
        _apply(db, row, res, prov, now)
    row.last_attempt_at = now
    audit.record(db, "sync.reconciled", target_type=row.subject_type, target_id=row.subject_id, detail={"kind": row.kind, "status": row.status})
    return row


def reconcile(db: Session, *, respect_backoff: bool = False, now: datetime | None = None) -> int:
    """Retry every unconfirmed sync and settle every untrusted one. Idempotent. Rows that were NOT_CONFIGURED when created are picked up once a provider exists.
    `respect_backoff` (the scheduler's mode) skips rows whose next attempt is not yet due; the operator command retries everything now. FAILED rows are never touched here."""
    if not provider().configured:
        return 0
    now = now or utcnow()
    done = 0
    pending = (SyncStatus.SYNC_PENDING.value, SyncStatus.NOT_CONFIGURED.value)
    for row in db.scalars(select(ExternalSync).where(ExternalSync.status.in_((*pending, SyncStatus.RECONCILIATION_REQUIRED.value))).order_by(ExternalSync.created_at)):
        if respect_backoff and row.next_attempt_at is not None and as_utc(row.next_attempt_at) > now:
            continue
        before = row.status
        if before == SyncStatus.RECONCILIATION_REQUIRED.value:
            settle(db, row, now)
            if row.status == SyncStatus.SYNC_PENDING.value:
                attempt(db, row, now)
        else:
            attempt(db, row, now)
        done += int(before != row.status and row.status == SyncStatus.SYNCED.value)
    db.commit()
    return done


def retry_now(db: Session, row: ExternalSync) -> ExternalSync:
    """An operator's decision (integration:manage): take a FAILED / RECONCILIATION_REQUIRED row back to a fresh bounded set of attempts, and try once now."""
    if row.status in (SyncStatus.FAILED.value, SyncStatus.RECONCILIATION_REQUIRED.value):
        row.status, row.attempts, row.next_attempt_at = SyncStatus.SYNC_PENDING.value, 0, None
    return attempt(db, row)


def state_view(row: ExternalSync | None) -> dict | None:
    if row is None:
        return None
    words = {
        SyncStatus.NOT_CONFIGURED.value: NOT_CONFIGURED_NOTE,
        SyncStatus.SYNC_PENDING.value: "AICTE's portal has not confirmed receipt. The system will retry; it does NOT show the portal as updated.",
        SyncStatus.SYNCED.value: "AICTE's portal confirmed receipt.",
        SyncStatus.FAILED.value: "AICTE's portal did not accept or confirm this, and automatic retries have stopped. A person must look. It does NOT show the portal as updated.",
        SyncStatus.RECONCILIATION_REQUIRED.value: "An answer came back that cannot be trusted, so it is not known whether AICTE's portal holds this. It is being checked; it does NOT show the portal as updated.",
    }
    return {
        "status": row.status, "message": words[row.status], "attempts": row.attempts, "last_error": row.last_error, "last_attempt_at": row.last_attempt_at, "confirmed_at": row.confirmed_at,
        "external_ref": row.external_ref, "next_attempt_at": row.next_attempt_at, "adapter": row.adapter,
    }


def sync_for(db: Session, subject_type: str, subject_id: uuid.UUID) -> ExternalSync | None:
    return db.scalar(select(ExternalSync).where(ExternalSync.subject_type == subject_type, ExternalSync.subject_id == subject_id).order_by(ExternalSync.created_at.desc()))


_ = DecisionCase
