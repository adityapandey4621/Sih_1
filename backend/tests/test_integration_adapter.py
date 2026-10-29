"""The AICTE integration boundary, at the adapter level. DEMO ONLY: nothing here talks to, or proves anything about, a real AICTE system.

Two kinds of test: the HTTP adapter against httpx's MockTransport (every response shape, deterministic), and against the demo receiver over a REAL socket
(signing, idempotency across a lost answer, timeouts)."""

import json
import threading
import time
import uuid
from datetime import UTC, datetime

import httpx
import pytest
import uvicorn

from app.config import Settings
from app.integration.aicte import mapping
from app.integration.aicte.adapter import CircuitBreaker, Outcome, RateLimiter
from app.integration.aicte.contract import CONTRACT_LABEL, Envelope, EventCode, Receipt, json_schemas
from app.integration.aicte.demo_server import create_demo_app
from app.integration.aicte.http_adapter import HttpAicteAdapter, sign
from app.integration.aicte.mock import MockAicteAdapter
from pydantic import ValidationError

KEY, SECRET = "k" * 32, "s" * 32
SENSITIVE = "TOP-SECRET-DO-NOT-LOG"


def settings(**kw) -> Settings:
    base = dict(environment="test", database_url="sqlite://", aicte_integration_mode="http", aicte_base_url="https://aicte.example.invalid", aicte_api_key=KEY, aicte_hmac_secret=SECRET,
                aicte_timeout_seconds=1.0, aicte_http_retries=2, aicte_rate_limit_per_minute=600, aicte_circuit_failures=3, aicte_circuit_cooldown_seconds=30)
    return Settings(**{**base, **kw})


def envelope(message_id: uuid.UUID | None = None, digest: str = "a" * 64) -> Envelope:
    payload = {"application_reference": "APP-AAAA-BBBB", "kind": "DECISION_ISSUED", "subject_id": str(uuid.uuid4()), "outcome": "LOA", "decision_version": 1, "decision_hash": "b" * 64,
               "issued_at": datetime(2026, 10, 27, tzinfo=UTC).isoformat(), "document_reference": "LOA/2026/0001", "document_sha256": None}
    return mapping.build_envelope("DECISION_ISSUED", message_id or uuid.uuid4(), payload, digest)


def receipt_for(env: Envelope, **over) -> dict:
    return {"status": "RECEIVED", "message_id": str(env.message_id), "payload_hash": env.payload_hash, "external_ref": "EXT-1", **over}


def adapter_with(handler, **kw) -> tuple[HttpAicteAdapter, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def wrapped(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    client = httpx.Client(transport=httpx.MockTransport(wrapped), timeout=1.0)
    return HttpAicteAdapter(settings(**kw), client=client, sleep=lambda s: None), seen


# ---- the contract -----------------------------------------------------------------------------------------------------


def test_the_contract_is_strict_and_labelled_as_a_project_definition():
    env = envelope()
    assert env.event == EventCode.OFFICIAL_OUTCOME_ISSUED
    data = env.model_dump(mode="json")
    with pytest.raises(ValidationError):
        Envelope.model_validate({**data, "surprise": 1})  # unknown fields are refused
    with pytest.raises(ValidationError):
        Envelope.model_validate({**data, "body": {**data["body"], "extra_field": "x"}})
    with pytest.raises(ValidationError):
        Envelope.model_validate({**data, "event": "COUNCIL_RATIFICATION_RECORDED"})  # the body must be what the event carries
    with pytest.raises(ValidationError):
        Envelope.model_validate({**data, "payload_hash": "short"})
    with pytest.raises(ValidationError):
        Receipt.model_validate({"status": "OK", "message_id": str(uuid.uuid4()), "payload_hash": "a" * 64})
    schemas = json_schemas()
    assert schemas["label"] == CONTRACT_LABEL and "NOT an AICTE specification" in CONTRACT_LABEL
    assert {"envelope", "receipt", "status", "bodies", "transport"} <= set(schemas)
    props = set(schemas["bodies"]["OFFICIAL_OUTCOME_ISSUED"]["properties"])
    assert props == {"application_reference", "subject_id", "outcome", "decision_version", "decision_hash", "issued_at", "document_reference", "document_sha256"}  # references and hashes; never content


# ---- the HTTP adapter: every shape of answer -------------------------------------------------------------------------


def test_a_matching_receipt_confirms_and_the_request_is_signed_and_idempotent():
    env = envelope()
    a, seen = adapter_with(lambda r: httpx.Response(200, json=receipt_for(env)))
    res = a.submit(env)
    assert res.outcome == Outcome.CONFIRMED and res.external_ref == "EXT-1" and not res.duplicate
    req = seen[0]
    assert req.headers["idempotency-key"] == str(env.message_id) and req.headers["authorization"] == f"Bearer {KEY}"
    assert req.headers["x-signature"] == sign(SECRET, req.headers["x-timestamp"], req.content) and req.headers["x-contract-version"] == "sih-demo-0.1"
    assert [(x.outcome, x.http_status) for x in res.attempts] == [("ok", 200)]
    assert KEY not in repr(res) and SECRET not in repr(res)  # nothing about the call carries a secret


def test_a_duplicate_receipt_is_still_confirmation():
    env = envelope()
    a, _ = adapter_with(lambda r: httpx.Response(200, json=receipt_for(env, status="DUPLICATE")))
    res = a.submit(env)
    assert res.outcome == Outcome.CONFIRMED and res.duplicate


@pytest.mark.parametrize("answer,code", [
    (lambda env: httpx.Response(200, content=b"<html>fine</html>", headers={"content-type": "text/html"}), "invalid_response"),
    (lambda env: httpx.Response(200, json=receipt_for(env, payload_hash="f" * 64)), "receipt_mismatch"),
    (lambda env: httpx.Response(200, json=receipt_for(env, message_id=str(uuid.uuid4()))), "receipt_mismatch"),
    (lambda env: httpx.Response(200, json=receipt_for(env, external_ref=None)), "receipt_without_reference"),
    (lambda env: httpx.Response(200, json={**receipt_for(env), "extra": 1}), "invalid_response"),
    (lambda env: httpx.Response(200, content=b"x" * 70000), "invalid_response"),
])
def test_an_answer_that_cannot_be_trusted_is_never_confirmation(answer, code):
    env = envelope()
    a, seen = adapter_with(lambda r: answer(env))
    res = a.submit(env)
    assert res.outcome == Outcome.RECONCILIATION_REQUIRED and res.error_code == code
    assert len(seen) == 1  # and it is not blindly re-sent: the question is now "what does the receiver hold?"


def test_a_rejection_is_permanent_and_is_not_retried():
    env = envelope()
    a, seen = adapter_with(lambda r: httpx.Response(200, json=receipt_for(env, status="REJECTED", external_ref=None, reason="duplicate application reference")))
    res = a.submit(env)
    assert res.outcome == Outcome.PERMANENT_FAILURE and "duplicate application reference" in res.detail and len(seen) == 1
    a, seen = adapter_with(lambda r: httpx.Response(403, json={"detail": "forbidden"}))
    res = a.submit(env)
    assert res.outcome == Outcome.PERMANENT_FAILURE and res.error_code == "http_403" and len(seen) == 1
    a, seen = adapter_with(lambda r: httpx.Response(302, headers={"location": "https://elsewhere.example.invalid/"}))
    res = a.submit(env)
    assert res.outcome == Outcome.PERMANENT_FAILURE and len(seen) == 1  # redirects are never followed: a redirect is not an answer


def test_a_temporary_failure_is_retried_with_the_same_key_and_then_succeeds():
    env = envelope()
    n = {"i": 0}

    def flaky(request: httpx.Request) -> httpx.Response:
        n["i"] += 1
        return httpx.Response(503) if n["i"] == 1 else httpx.Response(200, json=receipt_for(env))

    a, seen = adapter_with(flaky)
    res = a.submit(env)
    assert res.outcome == Outcome.CONFIRMED and len(seen) == 2
    assert {r.headers["idempotency-key"] for r in seen} == {str(env.message_id)}  # a retry can never be a second message
    assert [x.outcome for x in res.attempts] == ["http_503", "ok"]


def test_retries_are_bounded_then_the_circuit_opens_and_stops_hammering():
    env = envelope()
    a, seen = adapter_with(lambda r: httpx.Response(503))
    res = a.submit(env)
    assert res.outcome == Outcome.TRANSIENT_FAILURE and res.error_code == "http_503" and len(seen) == 3  # 1 try + 2 retries, no more
    a.submit(env)
    a.submit(env)  # third failed submit: the breaker (threshold 3) opens
    before = len(seen)
    res = a.submit(env)
    assert res.outcome == Outcome.TRANSIENT_FAILURE and res.error_code == "circuit_open" and len(seen) == before  # no call at all while the circuit is open
    assert a.describe()["circuit_open"] is True


def test_timeouts_and_connection_errors_are_transient():
    env = envelope()
    for exc, code in ((httpx.ReadTimeout("slow"), "timeout"), (httpx.ConnectError("refused"), "connection_error")):
        def boom(request, exc=exc):
            raise exc

        a, seen = adapter_with(boom)
        res = a.submit(env)
        assert res.outcome == Outcome.TRANSIENT_FAILURE and res.error_code == code and len(seen) == 3


def test_our_own_outbound_rate_limit_applies_before_any_call():
    env = envelope()
    a, seen = adapter_with(lambda r: httpx.Response(200, json=receipt_for(env)), aicte_rate_limit_per_minute=2)
    assert [a.submit(env).outcome for _ in range(3)] == [Outcome.CONFIRMED, Outcome.CONFIRMED, Outcome.TRANSIENT_FAILURE]
    assert len(seen) == 2


def test_an_unconfigured_adapter_sends_nothing():
    a, seen = adapter_with(lambda r: httpx.Response(200), aicte_api_key="")
    assert not a.configured
    assert a.submit(envelope()).outcome == Outcome.NOT_CONFIGURED and seen == []


def test_describe_never_exposes_a_secret_or_a_path():
    a, _ = adapter_with(lambda r: httpx.Response(200), aicte_base_url="https://aicte.example.invalid/private/path?token=abc")
    d = json.dumps(a.describe())
    assert KEY not in d and SECRET not in d and "private" not in d and "token" not in d and "aicte.example.invalid" in d


def test_the_status_question_distinguishes_unknown_from_unreachable():
    env = envelope()
    a, _ = adapter_with(lambda r: httpx.Response(200, json={"message_id": str(env.message_id), "status": "RECEIVED", "external_ref": "EXT-9", "payload_hash": env.payload_hash}))
    st = a.status(env.message_id)
    assert st.known and st.status == "RECEIVED" and st.external_ref == "EXT-9"
    a, _ = adapter_with(lambda r: httpx.Response(404))
    assert a.status(env.message_id).status == "UNKNOWN"  # the receiver does not hold it
    a, _ = adapter_with(lambda r: httpx.Response(500))
    st = a.status(env.message_id)
    assert not st.known and st.error_code == "http_500"  # unreachable is not the same as "does not hold it"


def test_rate_limiter_and_breaker_units():
    t = {"now": 0.0}
    rl = RateLimiter(2, clock=lambda: t["now"])
    assert rl.allow() and rl.allow() and not rl.allow()
    t["now"] = 30.0  # half a minute refills one token
    assert rl.allow() and not rl.allow()
    cb = CircuitBreaker(2, 10, clock=lambda: t["now"])
    cb.failure()
    assert cb.allow()
    cb.failure()
    assert not cb.allow() and cb.is_open
    t["now"] += 11
    assert cb.allow()  # half-open: one more failure re-opens it at once
    cb.failure()
    assert not cb.allow()
    t["now"] += 11
    cb.allow()
    cb.success()
    assert cb.allow() and not cb.is_open


def test_the_mock_adapter_is_demo_only_and_deduplicates_like_a_receiver_should():
    m = MockAicteAdapter()
    env = envelope()
    a, b = m.submit(env), m.submit(env)
    assert m.demo and a.external_ref == b.external_ref and not a.duplicate and b.duplicate and len(m.received) == 1
    assert "demo_only" in m.describe() and m.describe()["demo_only"] is True


# ---- over a real socket, against the demo receiver ---------------------------------------------------------------------


@pytest.fixture
def receiver():
    app = create_demo_app(KEY, SECRET)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    assert server.started, "the demo receiver did not start"
    port = server.servers[0].sockets[0].getsockname()[1]
    yield f"http://127.0.0.1:{port}", app
    server.should_exit = True
    thread.join(5)


def real_adapter(url: str, fault: str | None = None, **kw) -> HttpAicteAdapter:
    headers = {"X-Demo-Fault": fault} if fault else {}
    client = httpx.Client(timeout=httpx.Timeout(1.0, connect=1.0), headers=headers, follow_redirects=False)
    return HttpAicteAdapter(settings(aicte_base_url=url, **kw), client=client, sleep=lambda s: None)


def test_over_a_real_socket_a_message_is_received_once_and_a_resend_is_a_duplicate(receiver):
    url, app = receiver
    a = real_adapter(url)
    env = envelope()
    first, second = a.submit(env), a.submit(env)
    assert first.outcome == second.outcome == Outcome.CONFIRMED
    assert first.external_ref == second.external_ref and not first.duplicate and second.duplicate
    assert len(app.state.store) == 1  # one message held, however many times it was sent
    st = a.status(env.message_id)
    assert st.known and st.status == "RECEIVED" and st.payload_hash == env.payload_hash


def test_over_a_real_socket_a_wrong_secret_is_refused_and_never_confirmed(receiver):
    url, app = receiver
    res = real_adapter(url, aicte_hmac_secret="x" * 32).submit(envelope())
    assert res.outcome == Outcome.PERMANENT_FAILURE and res.error_code == "http_401" and len(app.state.store) == 0
    res = real_adapter(url, aicte_api_key="y" * 32).submit(envelope())
    assert res.outcome == Outcome.PERMANENT_FAILURE and res.error_code == "http_401"


def test_over_a_real_socket_a_lost_answer_is_recovered_by_the_idempotent_retry(receiver):
    """The receiver stores the message, then fails to answer. The retry carries the same key, so it is recognised as a duplicate and confirmed: stored once, confirmed once."""
    url, app = receiver
    env = envelope()
    a = real_adapter(url, "lose", aicte_http_retries=0)  # the first call stores and answers 503
    first = a.submit(env)
    assert first.outcome == Outcome.TRANSIENT_FAILURE and len(app.state.store) == 1
    second = real_adapter(url).submit(env)  # the later retry, no fault
    assert second.outcome == Outcome.CONFIRMED and second.duplicate and len(app.state.store) == 1


@pytest.mark.parametrize("fault,outcome,code", [
    ("503", Outcome.TRANSIENT_FAILURE, "http_503"), ("500", Outcome.TRANSIENT_FAILURE, "http_500"), ("garbage", Outcome.RECONCILIATION_REQUIRED, "invalid_response"),
    ("reject", Outcome.PERMANENT_FAILURE, "rejected"), ("lie", Outcome.RECONCILIATION_REQUIRED, "receipt_mismatch"),
])
def test_over_a_real_socket_every_fault_maps_to_the_right_state(receiver, fault, outcome, code):
    url, _ = receiver
    res = real_adapter(url, fault).submit(envelope())
    assert res.outcome == outcome and res.error_code == code


def test_over_a_real_socket_a_slow_receiver_times_out_instead_of_hanging(receiver):
    url, _ = receiver
    t0 = time.perf_counter()
    res = real_adapter(url, "slow", aicte_timeout_seconds=0.5, aicte_http_retries=0).submit(envelope())
    assert res.outcome == Outcome.TRANSIENT_FAILURE and res.error_code == "timeout" and time.perf_counter() - t0 < 2.4


def test_nothing_unreachable_hangs_or_leaks():
    a = real_adapter("http://127.0.0.1:9", aicte_http_retries=0)  # nothing listens on port 9
    res = a.submit(envelope())
    assert res.outcome == Outcome.TRANSIENT_FAILURE and res.error_code in ("connection_error", "timeout")
    assert KEY not in repr(res)
