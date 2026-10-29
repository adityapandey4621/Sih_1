"""An HTTP adapter for the contract in contract.py. UNVERIFIED against any real AICTE system: it can only be exercised against the demo receiver.

Per call: Bearer key, Idempotency-Key (= message id), a timestamp and an HMAC signature of the raw body, hard timeouts, a rate limit, a circuit breaker, and
bounded retries only where retrying cannot duplicate anything (connection errors, timeouts, 429/502/503/504 - the receiver de-duplicates on the key).
A 2xx answer counts as confirmation only if it parses as a Receipt that echoes our message id AND payload hash. Anything else that "looks like success" is
RECONCILIATION_REQUIRED: we do not know what the receiver holds, so we ask (`status`) rather than guess. Secrets and bodies are never logged.
"""

import hashlib
import hmac
import json
import time
import uuid
from collections.abc import Callable

import httpx
from pydantic import ValidationError

from app.config import Settings
from app.integration.aicte.adapter import AdapterResult, Attempt, CircuitBreaker, Outcome, RateLimiter, StatusResult
from app.integration.aicte.contract import CONTRACT_VERSION, Envelope, Receipt, StatusResponse

MAX_RESPONSE_BYTES = 64 * 1024
RETRY_STATUSES = {429, 502, 503, 504}
MAX_RETRY_AFTER = 5.0


def canonical_body(envelope: Envelope) -> bytes:
    return json.dumps(envelope.model_dump(mode="json"), sort_keys=True, separators=(",", ":")).encode()


def sign(secret: str, timestamp: str, body: bytes) -> str:
    return hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()


class HttpAicteAdapter:
    name = "http"
    demo = False

    def __init__(self, settings: Settings, client: httpx.Client | None = None, sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic) -> None:
        self.s = settings
        self.base = settings.aicte_base_url.rstrip("/")
        self.configured = bool(self.base and settings.aicte_api_key and settings.aicte_hmac_secret)
        self.client = client or httpx.Client(timeout=httpx.Timeout(settings.aicte_timeout_seconds, connect=settings.aicte_connect_timeout_seconds), follow_redirects=False)
        self.sleep = sleep
        self.limiter = RateLimiter(settings.aicte_rate_limit_per_minute, clock)
        self.breaker = CircuitBreaker(settings.aicte_circuit_failures, settings.aicte_circuit_cooldown_seconds, clock)

    def describe(self) -> dict:
        return {"adapter": self.name, "demo_only": self.demo, "configured": self.configured, "circuit_open": self.breaker.is_open, "contract_version": CONTRACT_VERSION,
                "base_host": httpx.URL(self.base).host if self.base else None}  # the host only: never the path, key or secret

    def _headers(self, message_id: uuid.UUID, body: bytes) -> dict[str, str]:
        ts = str(int(time.time()))
        return {
            "Authorization": f"Bearer {self.s.aicte_api_key}", "Idempotency-Key": str(message_id), "X-Contract-Version": CONTRACT_VERSION, "X-Timestamp": ts,
            "X-Signature": sign(self.s.aicte_hmac_secret, ts, body), "Content-Type": "application/json", "Accept": "application/json", "User-Agent": "sih-aicte-workflow/0.3",
        }

    def submit(self, envelope: Envelope) -> AdapterResult:
        if not self.configured:
            return AdapterResult(Outcome.NOT_CONFIGURED, error_code="not_configured", detail="The AICTE integration is not configured.")
        attempts: list[Attempt] = []
        if not self.limiter.allow():
            attempts.append(Attempt("rate_limited"))
            return AdapterResult(Outcome.TRANSIENT_FAILURE, error_code="rate_limited", detail="Our own outbound rate limit was reached; it will be retried.", attempts=attempts)
        if not self.breaker.allow():
            attempts.append(Attempt("circuit_open"))
            return AdapterResult(Outcome.TRANSIENT_FAILURE, error_code="circuit_open", detail="Recent calls kept failing, so calls are paused for a short while.", attempts=attempts)
        body = canonical_body(envelope)
        last_code = "unknown"
        for n in range(1 + max(0, self.s.aicte_http_retries)):
            t0 = time.perf_counter()
            status: int | None = None
            try:
                resp = self.client.post(f"{self.base}/v1/outcomes", content=body, headers=self._headers(envelope.message_id, body))
                status = resp.status_code
            except httpx.TimeoutException:
                last_code = "timeout"
                attempts.append(Attempt("timeout", None, int((time.perf_counter() - t0) * 1000)))
                self._backoff(n)
                continue
            except httpx.TransportError:
                last_code = "connection_error"
                attempts.append(Attempt("connection_error", None, int((time.perf_counter() - t0) * 1000)))
                self._backoff(n)
                continue
            ms = int((time.perf_counter() - t0) * 1000)
            if status in RETRY_STATUSES or status >= 500:
                last_code = f"http_{status}"
                attempts.append(Attempt(last_code, status, ms))
                self._backoff(n, resp.headers.get("retry-after"))
                continue
            if 200 <= status < 300:
                return self._interpret(resp, envelope, attempts, status, ms)
            attempts.append(Attempt(f"http_{status}", status, ms))
            self.breaker.success()  # the service answered; the problem is the request, not its health
            return AdapterResult(Outcome.PERMANENT_FAILURE, error_code=f"http_{status}", detail=f"The receiver refused the message (HTTP {status}).", attempts=attempts)
        self.breaker.failure()
        return AdapterResult(Outcome.TRANSIENT_FAILURE, error_code=last_code, detail="The receiver could not be reached or kept failing; it will be retried.", attempts=attempts)

    def _backoff(self, n: int, retry_after: str | None = None) -> None:
        delay = min(MAX_RETRY_AFTER, float(retry_after)) if retry_after and retry_after.replace(".", "", 1).isdigit() else min(MAX_RETRY_AFTER, 0.25 * (2**n))
        self.sleep(delay)

    def _interpret(self, resp: httpx.Response, envelope: Envelope, attempts: list[Attempt], status: int, ms: int) -> AdapterResult:
        try:
            if len(resp.content) > MAX_RESPONSE_BYTES:
                raise ValueError("too large")
            receipt = Receipt.model_validate_json(resp.content)
        except (ValidationError, ValueError):
            attempts.append(Attempt("invalid_response", status, ms))
            self.breaker.success()
            return AdapterResult(Outcome.RECONCILIATION_REQUIRED, error_code="invalid_response", detail="The receiver answered, but not with a valid receipt, so we do not know whether it holds the message.", attempts=attempts)
        self.breaker.success()
        if receipt.message_id != envelope.message_id or receipt.payload_hash != envelope.payload_hash:
            attempts.append(Attempt("hash_mismatch", status, ms))
            return AdapterResult(Outcome.RECONCILIATION_REQUIRED, error_code="receipt_mismatch", detail="The receipt does not match the message that was sent.", attempts=attempts)
        if receipt.status == "REJECTED":
            attempts.append(Attempt("rejected", status, ms))
            return AdapterResult(Outcome.PERMANENT_FAILURE, error_code="rejected", detail=(receipt.reason or "The receiver rejected the message.")[:300], attempts=attempts)
        if not receipt.external_ref:
            attempts.append(Attempt("invalid_response", status, ms))
            return AdapterResult(Outcome.RECONCILIATION_REQUIRED, error_code="receipt_without_reference", detail="The receipt carries no reference for the message.", attempts=attempts)
        attempts.append(Attempt("ok", status, ms))
        return AdapterResult(Outcome.CONFIRMED, external_ref=receipt.external_ref, duplicate=receipt.status == "DUPLICATE", attempts=attempts)

    def status(self, message_id: uuid.UUID) -> StatusResult:
        if not self.configured:
            return StatusResult(False, error_code="not_configured")
        if not self.breaker.allow():
            return StatusResult(False, error_code="circuit_open", attempts=[Attempt("circuit_open")])
        t0 = time.perf_counter()
        try:
            resp = self.client.get(f"{self.base}/v1/messages/{message_id}", headers=self._headers(message_id, b""))
        except httpx.TimeoutException:
            self.breaker.failure()
            return StatusResult(False, error_code="timeout", attempts=[Attempt("timeout", None, int((time.perf_counter() - t0) * 1000))])
        except httpx.TransportError:
            self.breaker.failure()
            return StatusResult(False, error_code="connection_error", attempts=[Attempt("connection_error", None, int((time.perf_counter() - t0) * 1000))])
        ms = int((time.perf_counter() - t0) * 1000)
        if resp.status_code == 404:
            self.breaker.success()
            return StatusResult(True, status="UNKNOWN", attempts=[Attempt("http_404", 404, ms)])
        if resp.status_code != 200:
            self.breaker.failure()
            return StatusResult(False, error_code=f"http_{resp.status_code}", attempts=[Attempt(f"http_{resp.status_code}", resp.status_code, ms)])
        try:
            if len(resp.content) > MAX_RESPONSE_BYTES:
                raise ValueError
            body = StatusResponse.model_validate_json(resp.content)
        except (ValidationError, ValueError):
            return StatusResult(False, error_code="invalid_response", attempts=[Attempt("invalid_response", 200, ms)])
        self.breaker.success()
        return StatusResult(True, status=body.status, external_ref=body.external_ref, payload_hash=body.payload_hash, attempts=[Attempt("ok", 200, ms)])
