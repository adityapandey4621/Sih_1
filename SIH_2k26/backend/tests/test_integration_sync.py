"""Synchronisation of decisions through the integration boundary: pending, confirmed, failed, reconciliation required, bounded retries, no duplicates.

The adapter here is the DEMO ONLY mock. These tests prove this application's behaviour around an adapter; they say nothing about any AICTE system."""

import json
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.config import get_settings
from app.db import SessionLocal
from app.integration.aicte import factory
from app.integration.aicte.mock import MockAicteAdapter
from app.models import AuditEvent, utcnow
from app.models_decisions import ExternalSync
from app.models_integration import IntegrationLog
from app.services import portal_sync
from tests.decision_world import make_dworld


@pytest.fixture(autouse=True)
def _clean():
    portal_sync.PROVIDER_OVERRIDE = None
    factory.OVERRIDE = None
    yield
    portal_sync.PROVIDER_OVERRIDE = None
    factory.OVERRIDE = None


def rows() -> list[ExternalSync]:
    with SessionLocal() as db:
        return list(db.scalars(select(ExternalSync).order_by(ExternalSync.created_at)))


def logs() -> list[IntegrationLog]:
    with SessionLocal() as db:
        return list(db.scalars(select(IntegrationLog).order_by(IntegrationLog.at)))


def decided():
    d = make_dworld()
    d.to_queue()
    d.observe()
    d.decide("LOR")
    return d


def reconcile(**kw) -> int:
    with SessionLocal() as db:
        return portal_sync.reconcile(db, **kw)


def test_a_confirmed_receipt_is_the_only_way_to_synced():
    m = factory.OVERRIDE = MockAicteAdapter()
    d = decided()
    d.issue()
    (r,) = rows()
    assert r.status == "SYNCED" and r.external_ref.startswith("MOCK-") and r.attempts == 1 and r.adapter == "mock" and r.next_attempt_at is None
    assert m.calls == 1 and len(m.received) == 1
    (g,) = logs()
    assert (g.operation, g.outcome, g.http_status, g.adapter) == ("submit", "ok", 200, "mock") and g.payload_hash == r.payload_hash
    assert d.status()["decision"]["sync"]["message"] == "AICTE's portal confirmed receipt."


def test_a_temporary_failure_backs_off_and_is_retried_until_confirmed():
    m = factory.OVERRIDE = MockAicteAdapter(["transient", "transient"])
    d = decided()
    d.issue()
    (r,) = rows()
    assert r.status == "SYNC_PENDING" and r.attempts == 1 and r.next_attempt_at is not None and r.last_outcome == "http_503"
    first_wait = r.next_attempt_at
    # the scheduler's mode respects the back-off: nothing is retried before the moment
    assert reconcile(respect_backoff=True) == 0 and m.calls == 1
    soon = utcnow() + timedelta(seconds=get_settings().sync_retry_backoff_seconds + 5)
    assert reconcile(respect_backoff=True, now=soon) == 0 and m.calls == 2  # retried, failed again
    (r,) = rows()
    assert r.attempts == 2 and r.next_attempt_at > first_wait  # the wait doubled
    assert reconcile(respect_backoff=True, now=utcnow() + timedelta(hours=1)) == 1  # third try succeeds
    (r,) = rows()
    assert r.status == "SYNCED" and r.attempts == 3 and r.last_error is None and r.next_attempt_at is None
    assert len(m.received) == 1


def test_retries_are_bounded_and_a_failed_record_waits_for_an_operator():
    m = factory.OVERRIDE = MockAicteAdapter(["transient"] * 20)
    d = decided()
    d.issue()
    limit = get_settings().sync_max_attempts
    for i in range(limit + 3):
        reconcile()  # an operator-style "retry everything now"
    (r,) = rows()
    assert r.status == "FAILED" and r.attempts == limit and "Gave up after" in r.last_error and r.next_attempt_at is None
    assert m.calls == limit  # it stopped: no attempt after the limit
    assert "does NOT show the portal as updated" in d.status()["decision"]["sync"]["message"] and d.status()["decision"]["sync"]["status"] == "FAILED"
    m.script.clear()  # the cause is fixed; an authorised person decides to retry
    assert d.ec.post(f"/api/integration/syncs/{r.id}/retry").status_code == 403  # the Executive Committee has no say in synchronisation
    assert d.bureau.post(f"/api/integration/syncs/{r.id}/retry").status_code == 200
    (r,) = rows()
    assert r.status == "SYNCED" and r.attempts == 1


def test_a_rejection_is_failed_at_once_and_not_retried():
    m = factory.OVERRIDE = MockAicteAdapter(["permanent"])
    d = decided()
    d.issue()
    (r,) = rows()
    assert r.status == "FAILED" and r.attempts == 1 and "rejected" in r.last_error
    assert reconcile() == 0 and m.calls == 1  # FAILED is never retried automatically
    assert d.status()["decision"]["sync"]["status"] == "FAILED"
    assert d.status()["official_status"]["state"] == "EXECUTIVE_COMMITTEE_DECISION_ISSUED"  # the official decision stands


def test_a_garbled_answer_is_reconciled_by_asking_not_by_sending_again():
    m = factory.OVERRIDE = MockAicteAdapter(["garbled"])
    d = decided()
    d.issue()
    (r,) = rows()
    assert r.status == "RECONCILIATION_REQUIRED" and d.status()["decision"]["sync"]["status"] == "RECONCILIATION_REQUIRED"
    assert "cannot be trusted" in d.status()["decision"]["sync"]["message"]
    assert reconcile() == 1  # asked the receiver: it holds the message with our hash
    (r,) = rows()
    assert r.status == "SYNCED" and r.external_ref and m.calls == 1  # confirmed WITHOUT sending a second message
    assert [g.operation for g in logs()] == ["submit", "status"]


def test_a_receipt_that_does_not_match_stays_for_a_person():
    factory.OVERRIDE = MockAicteAdapter(["mismatch"])
    d = decided()
    d.issue()
    assert reconcile() == 0
    (r,) = rows()
    assert r.status == "RECONCILIATION_REQUIRED" and "does not match" in r.last_error
    assert d.status()["decision"]["sync"]["status"] == "RECONCILIATION_REQUIRED"


def test_a_lost_acknowledgement_is_not_a_duplicate_message():
    m = factory.OVERRIDE = MockAicteAdapter(["lost_ack"])
    d = decided()
    d.issue()
    (r,) = rows()
    assert r.status == "SYNC_PENDING" and len(m.received) == 1  # the receiver has it; we do not know that yet
    assert reconcile() == 1
    (r,) = rows()
    assert r.status == "SYNCED" and len(m.received) == 1 and m.calls == 2  # sent twice, held once, confirmed once
    assert r.external_ref == next(iter(m.received.values()))[1]


def test_a_failed_push_never_undoes_the_decision_and_staff_are_told():
    factory.OVERRIDE = MockAicteAdapter(["permanent"])
    d = decided()
    d.issue()
    with SessionLocal() as db:
        from app.models_scrutiny import Notification

        n = db.scalars(select(Notification).where(Notification.event_type == "EXTERNAL_SYNC_PENDING")).all()
    assert n and all(x.external_delivery == "NOT_CONFIGURED" for x in n)
    assert d.case()["state"] == "ISSUED"


def test_the_integration_log_holds_outcomes_and_hashes_only():
    factory.OVERRIDE = MockAicteAdapter(["transient"])
    d = decided()
    d.issue()
    reconcile()
    text = json.dumps([{c.name: str(getattr(g, c.name)) for c in IntegrationLog.__table__.columns} for g in logs()])
    assert "LOR/2026/0001" not in text and "document" not in text.lower().replace("payload_hash", "")  # no reference, no content
    assert {"id", "at", "sync_id", "adapter", "operation", "message_id", "attempt_no", "outcome", "http_status", "latency_ms", "payload_hash"} == {c.name for c in IntegrationLog.__table__.columns}


def test_integration_records_are_append_only_in_the_application():
    from app.guards import ImmutableRecordError

    factory.OVERRIDE = MockAicteAdapter()
    decided().issue()
    with SessionLocal() as db:
        g = db.scalars(select(IntegrationLog)).first()
        g.outcome = "tampered"
        with pytest.raises(ImmutableRecordError):
            db.flush()
        db.rollback()
        db.delete(db.scalars(select(IntegrationLog)).first())
        with pytest.raises(ImmutableRecordError):
            db.flush()
        db.rollback()
        a = db.scalars(select(AuditEvent)).first()
        a.action = "something.else"
        with pytest.raises(ImmutableRecordError):
            db.flush()


# ---- the status API for staff ----------------------------------------------------------------------------------------------


def test_the_status_page_is_honest_about_what_is_unverified_and_hides_secrets(monkeypatch):
    monkeypatch.setattr(get_settings(), "aicte_api_key", "SECRET-API-KEY-VALUE")
    monkeypatch.setattr(get_settings(), "aicte_hmac_secret", "SECRET-HMAC-VALUE")
    d = decided()
    body = d.bureau.get("/api/integration/status")
    assert body.status_code == 200
    j = body.json()
    assert j["mode"] == "none" and j["adapter"] is None and j["verified"] is False and "UNVERIFIED" in j["verification_note"]
    assert "SECRET-API-KEY-VALUE" not in body.text and "SECRET-HMAC-VALUE" not in body.text
    factory.OVERRIDE = MockAicteAdapter()
    d.issue()
    j = d.bureau.get("/api/integration/status").json()
    assert j["adapter"]["demo_only"] is True and j["counts"] == {"SYNCED": 1} and j["recent"][0]["application_reference"].startswith("APP-") and j["log"][0]["outcome"] == "ok"
    assert j["verified"] is False  # a green mock says nothing about a real system


def test_who_may_see_and_who_may_manage_the_integration():
    d = decided()
    allowed = {"bureau": d.bureau, "auditor": d.w.auditor}
    for name, c in allowed.items():
        assert c.get("/api/integration/status").status_code == 200, name
        assert c.get("/api/integration/contract").status_code == 200, name
    for name, c in {"ec": d.ec, "council": d.council, "institution": d.w.a, "scrutiny": d.w.scrutiny}.items():
        assert c.get("/api/integration/status").status_code == 403, name
        assert c.get("/api/integration/contract").status_code == 403, name
    assert d.w.auditor.post("/api/integration/syncs/00000000-0000-0000-0000-000000000000/retry").status_code == 403  # read-only
    assert d.bureau.post("/api/integration/syncs/00000000-0000-0000-0000-000000000000/retry").status_code == 404
    assert d.bureau.get("/api/integration/contract").json()["label"].startswith("DEMO ONLY")
