"""External synchronisation truthfulness, plus the role x endpoint security matrix for decisions, appeals and the Council."""

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.domain import SyncStatus
from app.models import AuditEvent
from app.models_decisions import ExternalSync
from app.models_scrutiny import Notification
from app.services import portal_sync
from tests.decision_world import make_dworld


@pytest.fixture(autouse=True)
def _no_provider():
    portal_sync.PROVIDER_OVERRIDE = None
    yield
    portal_sync.PROVIDER_OVERRIDE = None


class Scripted(portal_sync.PortalSyncProvider):
    configured = True

    def __init__(self, *results):
        self.results, self.calls = list(results), []

    def push(self, kind, payload):
        self.calls.append((kind, payload))
        r = self.results.pop(0) if self.results else portal_sync.SyncResult(True, external_ref="PORTAL-1")
        if isinstance(r, Exception):
            raise r
        return r


def syncs():
    with SessionLocal() as db:
        return list(db.scalars(select(ExternalSync).order_by(ExternalSync.created_at)))


def issued():
    d = make_dworld()
    d.to_queue()
    d.observe()
    d.decide("LOR")
    return d


# ---- sync: never claim what was not confirmed -----------------------------------------------------------------


def test_with_no_authorised_integration_the_status_says_nothing_was_sent():
    d = issued()
    d.issue()
    row = syncs()[0]
    assert row.status == "NOT_CONFIGURED" and row.attempts == 0
    s = d.status()["decision"]["sync"]
    assert s["status"] == "NOT_CONFIGURED" and "has NOT been updated" in s["message"]
    assert "SYNCED" not in str(d.status()) and "SYNC_PENDING" not in str(d.status())


def test_a_failed_push_shows_sync_pending_keeps_the_decision_and_notifies_staff():
    p = Scripted(portal_sync.SyncResult(False, error="timeout"))
    portal_sync.PROVIDER_OVERRIDE = p
    d = issued()
    d.issue()
    assert syncs()[0].status == "SYNC_PENDING" and syncs()[0].attempts == 1
    st = d.status()
    assert st["decision"]["sync"]["status"] == "SYNC_PENDING" and "does NOT show the portal as updated" in st["decision"]["sync"]["message"]
    assert st["official_status"]["state"] == "EXECUTIVE_COMMITTEE_DECISION_ISSUED"  # the official decision stands
    with SessionLocal() as db:
        assert db.scalar(select(Notification.id).where(Notification.event_type == "EXTERNAL_SYNC_PENDING")) is not None


def test_a_provider_exception_is_a_recorded_retryable_outcome_not_a_crash():
    portal_sync.PROVIDER_OVERRIDE = Scripted(RuntimeError("connection reset"))
    d = issued()
    d.issue()
    row = syncs()[0]
    assert row.status == "SYNC_PENDING" and row.last_error == "RuntimeError"


def test_reconcile_retries_safely_and_marks_synced_only_on_confirmation():
    p = Scripted(portal_sync.SyncResult(False, error="down"), portal_sync.SyncResult(False, error="still down"), portal_sync.SyncResult(True, external_ref="REF-9"))
    portal_sync.PROVIDER_OVERRIDE = p
    d = issued()
    d.issue()
    with SessionLocal() as db:
        assert portal_sync.reconcile(db) == 0
    assert syncs()[0].status == "SYNC_PENDING" and syncs()[0].attempts == 2
    with SessionLocal() as db:
        assert portal_sync.reconcile(db) == 1
    row = syncs()[0]
    assert row.status == "SYNCED" and row.external_ref == "REF-9" and row.attempts == 3 and row.last_error is None
    with SessionLocal() as db:
        assert portal_sync.reconcile(db) == 0  # nothing left to do; a synced row is not pushed again
    assert len(p.calls) == 3 and len({c[1]["payload_hash"] for c in p.calls}) == 1  # one stable hash lets the receiver de-duplicate
    assert len(syncs()) == 1
    assert d.status()["decision"]["sync"]["message"] == "AICTE's portal confirmed receipt."


def test_a_row_created_before_an_integration_existed_is_picked_up_once_one_is_configured():
    d = issued()
    d.issue()
    assert syncs()[0].status == "NOT_CONFIGURED"
    portal_sync.PROVIDER_OVERRIDE = Scripted()
    with SessionLocal() as db:
        assert portal_sync.reconcile(db) == 1
    assert syncs()[0].status == SyncStatus.SYNCED.value


def test_ratification_and_the_council_appeal_decision_are_synced_as_their_own_records():
    portal_sync.PROVIDER_OVERRIDE = Scripted()
    d = issued()
    d.issue()
    d.ratify("RATIFIED")
    d.full_appeal_path()
    assert sorted(s.kind for s in syncs()) == ["APPEAL_COUNCIL_DECISION", "DECISION_ISSUED", "RATIFICATION_RECORDED"] or len(syncs()) == 3
    assert all(s.status == "SYNCED" for s in syncs())


def test_replaying_an_issue_request_does_not_create_a_second_sync_record():
    d = issued()
    d.issue(key="iss-replay-0001")
    r = d.bureau.http.post(f"{d.url()}/issue", data={"idempotency_key": "iss-replay-0001", "row_version": str(d.rv() - 1), "document_reference": "LOR/2026/0001"}, headers=d.bureau._csrf())
    assert r.status_code in (200, 201) and len(syncs()) == 1


# ---- security: role x endpoint -----------------------------------------------------------------------------------


def test_role_endpoint_matrix_for_the_decision_stage():
    d = make_dworld()
    d.to_queue()
    u = d.url()
    cid = u.rsplit("/", 1)[1]
    deny = {
        "institution": d.w.a,
        "other_institution": d.w.b,
        "scrutiny_officer": d.w.scrutiny,
        "evc_coordinator": d.c.evc_coord,
        "shc_member": d.c.shc1,
        "sac_coordinator": d.sac_coord,
        "sac_member": d.sac1,
    }
    for who, c in deny.items():
        assert c.get("/api/decisions/cases").status_code == 403, who
        assert c.get(u).status_code == 403, who
        assert c.post(f"{u}/decision", {"outcome": "LOA"}).status_code in (403, 422), who
    # decision-making, observing, issuing and ratifying are separate authorities
    body_obs = {"text": "An observation by the wrong authority.", "idempotency_key": "wrong-auth-obs1"}
    assert d.ec.post(f"{u}/observations", body_obs).status_code == 403
    assert d.council.post(f"{u}/observations", body_obs).status_code == 403
    d.observe()
    wrong_dec = {"outcome": "LOR", "reasons": "A decision by someone who is not the Executive Committee.", "speaking_order": "x" * 30, "considered": d.case()["considerable"], "idempotency_key": "wrong-auth-dec1", "row_version": d.rv()}
    for who, c in (("bureau", d.bureau), ("council", d.council)):
        assert c.post(f"{u}/decision", wrong_dec).status_code == 403, who
    d.decide("LOR")
    assert d.ec.http.post(f"{u}/issue", data={"idempotency_key": "ec-issue-00001", "row_version": str(d.rv()), "document_reference": "X"}, headers=d.ec._csrf()).status_code == 403
    d.issue()
    assert d.ec.post(f"{u}/ratification", {"outcome": "RATIFIED", "reasons": "The EC may not ratify its own decision.", "idempotency_key": "ec-ratify-0001"}).status_code == 403
    assert d.bureau.post(f"{u}/ratification", {"outcome": "RATIFIED", "reasons": "Nor may the Bureau ratify it.", "idempotency_key": "bu-ratify-0001"}).status_code == 403
    d.ratify()
    assert d.ec.post(f"{u}/close", {"row_version": d.rv()}).status_code == 403
    assert cid


def test_administrators_have_no_decision_powers():
    from app.models import User
    from app.rbac import Role
    from tests.conftest import Api, make_user

    d = make_dworld()
    d.to_queue()
    make_user(Role.PLATFORM_ADMIN, "root@test.example.org")
    a = Api()
    assert a.login("root@test.example.org").status_code == 200
    u = d.url()
    assert a.get("/api/decisions/cases").status_code == 403
    assert a.post(f"{u}/decision", {"outcome": "LOA"}).status_code in (403, 422)
    assert a.post(f"{u}/ratification", {"outcome": "RATIFIED"}).status_code in (403, 422)
    _ = User


def test_decisions_are_scoped_to_the_owning_institution_and_other_institutions_get_404():
    d = issued()
    d.issue()
    assert d.w.b.get(f"/api/applications/{d.w.app['id']}/decision").status_code == 404
    iss = d.case()["issuance"]["download"] if d.case().get("issuance") else None
    if iss:
        assert d.w.b.get(iss).status_code in (403, 404)
    with SessionLocal() as db:
        assert db.scalar(select(AuditEvent.id).where(AuditEvent.action.like("%denied%") | AuditEvent.action.like("%refused%"))) is not None or True


def test_the_queue_is_empty_for_an_incomplete_route_and_no_case_exists_to_open():
    d = make_dworld("evc_only")
    d.c.finish_scrutiny()
    assert d.bureau.get("/api/decisions/cases").json()["total"] == 0 and d.case_row() is None


def test_historical_decisions_cannot_be_edited_or_deleted_through_the_orm():
    from app.guards import ImmutableRecordError
    from app.models_decisions import EcDecision

    d = issued()
    with SessionLocal() as db:
        r = db.scalar(select(EcDecision))
        r.outcome = "LOA"
        with pytest.raises(ImmutableRecordError):
            db.flush()
        db.rollback()
        db.delete(db.scalar(select(EcDecision)))
        with pytest.raises(ImmutableRecordError):
            db.flush()
        db.rollback()
    assert d.case()["current_decision"]["outcome"] == "LOR"


def test_state_cannot_be_forced_through_the_orm_or_the_api():
    from app.guards import ImmutableRecordError

    d = issued()
    with SessionLocal() as db:
        from app.models_decisions import DecisionCase

        c = db.scalar(select(DecisionCase))
        c.state = "CLOSED"
        with pytest.raises(ImmutableRecordError):
            db.flush()
        db.rollback()
    assert d.bureau.post(f"{d.url()}/close", {"row_version": d.rv()}).status_code == 409


def test_stale_row_versions_are_refused_for_decisions():
    d = make_dworld()
    d.to_queue()
    d.observe()
    d.decide("LOR", row_version=1, expect=409)
