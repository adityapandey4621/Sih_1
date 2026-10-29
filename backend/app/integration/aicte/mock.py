"""A mock adapter. DEMO ONLY. It proves nothing about any real AICTE system - only that this application behaves correctly around an adapter that confirms,
fails, answers garbage or disappears. It keeps what it "received" in memory and de-duplicates on the message id, as the contract asks of a receiver.
"""

import uuid

from app.integration.aicte.adapter import AdapterResult, Attempt, Outcome, StatusResult
from app.integration.aicte.contract import CONTRACT_VERSION, Envelope


class MockAicteAdapter:
    name = "mock"
    demo = True
    configured = True

    def __init__(self, script: list[str] | None = None) -> None:
        """`script` lists the behaviour of the next calls: confirm | transient | permanent | garbled | mismatch | lost_ack. Once it is used up, every call confirms."""
        self.script = list(script or [])
        self.received: dict[uuid.UUID, tuple[str, str]] = {}  # message_id -> (payload_hash, reference)
        self.calls = 0

    def describe(self) -> dict:
        return {"adapter": self.name, "demo_only": True, "configured": True, "circuit_open": False, "contract_version": CONTRACT_VERSION, "base_host": None}

    def _hold(self, env: Envelope, tamper: bool = False) -> tuple[str, bool]:
        existing = self.received.get(env.message_id)
        if existing:
            return existing[1], True
        ref = f"MOCK-{env.message_id.hex[:10].upper()}"
        self.received[env.message_id] = (("0" * 64) if tamper else env.payload_hash, ref)
        return ref, False

    def submit(self, envelope: Envelope) -> AdapterResult:
        self.calls += 1
        mode = self.script.pop(0) if self.script else "confirm"
        ok = [Attempt("ok", 200, 1)]
        if mode == "transient":
            return AdapterResult(Outcome.TRANSIENT_FAILURE, error_code="http_503", detail="The mock receiver is unavailable.", attempts=[Attempt("http_503", 503, 1)])
        if mode == "permanent":
            return AdapterResult(Outcome.PERMANENT_FAILURE, error_code="rejected", detail="The mock receiver rejected the message.", attempts=[Attempt("rejected", 200, 1)])
        if mode == "garbled":
            self._hold(envelope)
            return AdapterResult(Outcome.RECONCILIATION_REQUIRED, error_code="invalid_response", detail="The mock receiver answered with something that is not a receipt.", attempts=[Attempt("invalid_response", 200, 1)])
        if mode == "mismatch":
            self._hold(envelope, tamper=True)
            return AdapterResult(Outcome.RECONCILIATION_REQUIRED, error_code="receipt_mismatch", detail="The receipt does not match the message that was sent.", attempts=[Attempt("hash_mismatch", 200, 1)])
        if mode == "lost_ack":  # the receiver stored it but the answer never arrived: exactly the case idempotency and reconciliation exist for
            self._hold(envelope)
            return AdapterResult(Outcome.TRANSIENT_FAILURE, error_code="timeout", detail="No answer arrived in time.", attempts=[Attempt("timeout", None, 1)])
        ref, dup = self._hold(envelope)
        return AdapterResult(Outcome.CONFIRMED, external_ref=ref, duplicate=dup, attempts=ok)

    def status(self, message_id: uuid.UUID) -> StatusResult:
        held = self.received.get(message_id)
        if held is None:
            return StatusResult(True, status="UNKNOWN", attempts=[Attempt("ok", 200, 1)])
        return StatusResult(True, status="RECEIVED", external_ref=held[1], payload_hash=held[0], attempts=[Attempt("ok", 200, 1)])
