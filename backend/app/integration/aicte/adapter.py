"""The adapter interface, its result vocabulary, and the two guards every network adapter needs: a rate limiter and a circuit breaker.

The adapter turns one internal message into one external call and says, in a closed vocabulary, what is now KNOWN. It never raises for an expected
failure, never logs a secret or a body, and reports every attempt as a small record (outcome code, HTTP status class, latency) that the caller stores.
"""

import time
import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol

from app.integration.aicte.contract import Envelope, StatusResponse


class Outcome(StrEnum):
    CONFIRMED = "CONFIRMED"  # a receipt echoing our message id and payload hash: the receiver HAS it
    TRANSIENT_FAILURE = "TRANSIENT_FAILURE"  # nothing is known to have been received; retrying is safe (same idempotency key)
    PERMANENT_FAILURE = "PERMANENT_FAILURE"  # the receiver refused it; retrying the same message cannot help
    RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"  # the answer cannot be trusted (garbled receipt, wrong hash): ask the receiver what it holds
    NOT_CONFIGURED = "NOT_CONFIGURED"


@dataclass
class Attempt:
    """One call, as logged. No bodies, no headers, no secrets."""

    outcome: str  # ok | timeout | connection_error | http_<status> | invalid_response | hash_mismatch | rate_limited | circuit_open | rejected
    http_status: int | None = None
    latency_ms: int = 0


@dataclass
class AdapterResult:
    outcome: Outcome
    external_ref: str | None = None
    error_code: str | None = None
    detail: str | None = None  # safe to show to staff: never contains a secret, a body or a stack trace
    duplicate: bool = False
    attempts: list[Attempt] = field(default_factory=list)


@dataclass
class StatusResult:
    known: bool
    status: str | None = None
    external_ref: str | None = None
    payload_hash: str | None = None
    error_code: str | None = None
    attempts: list[Attempt] = field(default_factory=list)


class AicteAdapter(Protocol):
    name: str
    demo: bool
    configured: bool

    def submit(self, envelope: Envelope) -> AdapterResult: ...

    def status(self, message_id: uuid.UUID) -> StatusResult: ...

    def describe(self) -> dict: ...


class RateLimiter:
    """Token bucket: `per_minute` calls per minute, bursting up to that many. In-process (one adapter per process)."""

    def __init__(self, per_minute: int, clock=time.monotonic) -> None:
        self.capacity = max(1, per_minute)
        self.tokens = float(self.capacity)
        self.clock = clock
        self.stamp = clock()

    def allow(self) -> bool:
        now = self.clock()
        self.tokens = min(self.capacity, self.tokens + (now - self.stamp) * self.capacity / 60.0)
        self.stamp = now
        if self.tokens >= 1:
            self.tokens -= 1
            return True
        return False


class CircuitBreaker:
    """After `threshold` consecutive failures the circuit opens for `cooldown` seconds: calls fail fast instead of piling onto a struggling service."""

    def __init__(self, threshold: int, cooldown: float, clock=time.monotonic) -> None:
        self.threshold, self.cooldown, self.clock = max(1, threshold), cooldown, clock
        self.failures = 0
        self.opened_at: float | None = None

    def allow(self) -> bool:
        if self.opened_at is None:
            return True
        if self.clock() - self.opened_at >= self.cooldown:
            self.opened_at = None  # half-open: let one attempt through; one more failure re-opens it
            self.failures = self.threshold - 1
            return True
        return False

    def success(self) -> None:
        self.failures, self.opened_at = 0, None

    def failure(self) -> None:
        self.failures += 1
        if self.failures >= self.threshold:
            self.opened_at = self.clock()

    @property
    def is_open(self) -> bool:
        return self.opened_at is not None


_ = StatusResponse
