"""Phase 7: document validity. Extracted dates are candidates, declared dates are claims, only a person's verification creates an expiry alert."""

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.guards import ImmutableRecordError
from app.models_compliance import ValidityItem, ValidityVersion
from app.models_scrutiny import Notification
from tests.catalogue_helpers import PDF
from tests.compliance_world import PW, clocks_of, make_pworld, work_of
from tests.docs_helpers import digital_pdf

OLD = "31/03/2029"
KEY = "verify-key-0001"


def utc(dt: datetime) -> datetime:
    """The instant in UTC. PostgreSQL hands back timestamps in the session's zone (IST on the development machine), SQLite hands back naive ones: compare instants, not renderings."""
    return dt.astimezone(UTC) if dt.tzinfo else dt.replace(tzinfo=UTC)


@pytest.fixture
def pw() -> PW:
    return make_pworld()


@pytest.fixture
def issued(pw: PW) -> PW:
    pw.to_issued(valid_until=OLD)
    pw.sync()  # reads the candidates
    return pw


def items(pw: PW, client=None) -> list[dict]:
    r = (client or pw.officer).get("/api/post-approval/validity")
    assert r.status_code == 200, r.text
    return r.json()["items"]


def verify(pw: PW, version_id: str, when: str = "2029-03-31", expect: int = 201, client=None, key: str = KEY, reason: str = "Read the date on page 2 of the lease against the document itself.") -> dict:
    r = (client or pw.officer).post(f"/api/post-approval/validity/versions/{version_id}/verify", {"verified_date": when, "reason": reason, "internal_note": "Registry stamp visible.", "idempotency_key": key})
    assert r.status_code == expect, r.text
    return r.json()


def renew(pw: PW, item_id: str, declared: str = "2032-03-31", expect: int = 201, client=None, key: str | None = None) -> dict:
    c = client or pw.inst
    r = c.http.post(f"/api/post-approval/validity/items/{item_id}/renewals", data={"declared_date": declared, "idempotency_key": key or pw.d.key("rn")}, files={"file": ("lease-renewed.pdf", PDF, "application/pdf")}, headers=c._csrf())
    assert r.status_code == expect, r.text
    return r.json()


def test_an_extracted_date_is_only_a_candidate_with_its_source_and_creates_no_alert(issued: PW):
    pw = issued
    (item,) = items(pw)
    (v,) = item["versions"]
    assert v["source"] == "EXTRACTED" and v["status"] == "UNVERIFIED" and v["value_date"] == "2029-03-31" and v["verified_date"] is None
    prov = v["provenance"]
    assert prov["document_version"] == 1 and prov["page"] and prov["method"] == "PDF_TEXT" and prov["document_id"]  # where the date came from
    assert item["current_verified_date"] is None and v["clock"] is None
    assert clocks_of(kind="DOCUMENT_VALIDITY") == []  # no expiry clock, therefore no expiry alert, from an unconfirmed date
    with SessionLocal() as db:
        n = db.scalars(select(Notification).where(Notification.event_type == "VALIDITY_NEEDS_REVIEW")).all()
    assert n and {x.recipient_kind for x in n} == {"compliance_officer"}
    assert pw.sync()["clocks_created"] == 0
    with SessionLocal() as db:
        assert len(db.scalars(select(ValidityVersion)).all()) == 1  # reading again creates no second candidate


def test_a_person_confirming_the_date_creates_the_expiry_clock_and_its_alerts(issued: PW):
    pw = issued
    vid = items(pw)[0]["versions"][0]["id"]
    out = verify(pw, vid)
    v = out["versions"][0]
    assert v["status"] == "VERIFIED" and v["verified_date"] == "2029-03-31" and v["verified_by"] and v["internal_note"] == "Registry stamp visible."
    (c,) = clocks_of(kind="DOCUMENT_VALIDITY")
    assert c.status == "RUNNING" and c.deadline_type == "VALIDITY" and utc(c.due_at) == datetime(2029, 3, 31, 23, 59, 59, tzinfo=UTC)
    assert sorted((w.alert_kind, w.offset_hours) for w in work_of(c.id)) == [("DUE", 0), ("OVERDUE", 24), ("PRE_DUE", 24), ("PRE_DUE", 168), ("PRE_DUE", 720)]
    # the institution sees the confirmed date but never the verifier's internal note
    mine = pw.inst.get("/api/post-approval/validity").json()["items"][0]
    assert mine["current_verified_date"] == "2029-03-31" and "internal_note" not in str(mine["versions"])


def test_the_verifiers_date_may_differ_from_the_extracted_one_and_both_are_kept(issued: PW):
    pw = issued
    vid = items(pw)[0]["versions"][0]["id"]
    out = verify(pw, vid, when="2029-03-30", reason="The document says 30 March in the stamp on page 2; the typed line is wrong.")
    v = out["versions"][0]
    assert v["value_date"] == "2029-03-31" and v["verified_date"] == "2029-03-30"  # extracted vs human-verified, side by side
    assert utc(clocks_of(kind="DOCUMENT_VALIDITY")[0].due_at).date() == date(2029, 3, 30)


def test_an_uncertain_or_missing_date_needs_review_and_never_becomes_an_alert():
    pw = make_pworld()
    pw.to_issued(valid_until=None)  # the document carries no validity line
    pw.sync()
    (item,) = items(pw)
    (v,) = item["versions"]
    assert v["status"] == "NEEDS_REVIEW" and v["value_date"] is None and "must read it" in v["needs_review_reason"]
    assert clocks_of(kind="DOCUMENT_VALIDITY") == []
    # a person can supply the date they read off the document, with a reason
    out = verify(pw, v["id"], when="2031-06-30")
    assert out["versions"][0]["status"] == "VERIFIED" and clocks_of(kind="DOCUMENT_VALIDITY")[0].status == "RUNNING"


def test_a_renewal_upload_is_a_new_version_and_does_not_cancel_the_old_alerts(issued: PW):
    pw = issued
    item = items(pw)[0]
    verify(pw, item["versions"][0]["id"])
    (old,) = clocks_of(kind="DOCUMENT_VALIDITY")
    old_work = [(w.alert_kind, w.offset_hours, w.status) for w in work_of(old.id)]
    out = renew(pw, item["id"])
    assert [v["source"] for v in out["versions"]] == ["EXTRACTED", "DECLARED"] and out["versions"][1]["status"] == "UNVERIFIED"
    pw.sync()
    cs = clocks_of(kind="DOCUMENT_VALIDITY")
    assert len(cs) == 1 and cs[0].id == old.id and cs[0].status == "RUNNING"  # the renewal created no clock of its own ...
    assert [(w.alert_kind, w.offset_hours, w.status) for w in work_of(old.id)] == old_work  # ... and the earlier date's alerts are untouched
    # a second renewal while the first awaits review is refused
    assert renew(pw, item["id"], expect=409)["code"] == "renewal_pending"
    # the renewal is verified: now (and only now) the old date ends and the new one takes over
    verify(pw, out["versions"][1]["id"], when="2032-03-31", key="verify-key-renewal")
    pw.sync()
    by_status = {c.status: c for c in clocks_of(kind="DOCUMENT_VALIDITY")}
    assert set(by_status) == {"COMPLETED", "RUNNING"} and by_status["COMPLETED"].id == old.id and by_status["COMPLETED"].completion_reason == "renewal_verified"
    assert utc(by_status["RUNNING"].due_at).date() == date(2032, 3, 31)
    assert all(w.status in ("CANCELLED", "SKIPPED_STALE") for w in work_of(old.id) if w.status != "DONE")
    with SessionLocal() as db:
        rows = db.scalars(select(ValidityVersion).order_by(ValidityVersion.version_number)).all()
    assert [r.status for r in rows] == ["SUPERSEDED", "VERIFIED"]


def test_a_rejected_renewal_leaves_the_old_alerts_in_force_and_allows_another_upload(issued: PW):
    pw = issued
    item = items(pw)[0]
    verify(pw, item["versions"][0]["id"])
    out = renew(pw, item["id"])
    rv = out["versions"][1]["id"]
    assert pw.inst.post(f"/api/post-approval/validity/versions/{rv}/reject", {"reason": "This is not the renewed lease."}).status_code == 403
    r = pw.officer.post(f"/api/post-approval/validity/versions/{rv}/reject", {"reason": "The upload is a different document, not the renewed lease.", "internal_note": "Wrong building."})
    assert r.status_code == 200
    assert clocks_of(kind="DOCUMENT_VALIDITY")[0].status == "RUNNING"
    mine = pw.inst.get("/api/post-approval/validity").json()["items"][0]
    assert mine["versions"][1]["status"] == "REJECTED" and mine["versions"][1]["review_reason"].startswith("The upload") and mine["can"]["renew"]
    assert "Wrong building" not in str(mine)
    renew(pw, item["id"], declared="2033-03-31")  # may try again


def test_verification_is_permissioned_scoped_and_idempotent(issued: PW):
    pw = issued
    item = items(pw)[0]
    vid = item["versions"][0]["id"]
    body = {"verified_date": "2029-03-31", "reason": "Compared against the document itself.", "idempotency_key": "k-someone-else"}
    for who in (pw.inst, pw.bureau, pw.d.ec, pw.d.w.auditor):
        assert who.post(f"/api/post-approval/validity/versions/{vid}/verify", body).status_code in (403, 404)
    assert pw.d.w.b.post(f"/api/post-approval/validity/versions/{vid}/verify", body).status_code == 403
    assert pw.d.w.b.get("/api/post-approval/validity").json()["total"] == 0  # another institution sees nothing
    assert pw.d.w.b.http.post(f"/api/post-approval/validity/items/{item['id']}/renewals", data={"declared_date": "2032-03-31", "idempotency_key": "k-other-inst-1"}, files={"file": ("x.pdf", PDF, "application/pdf")}, headers=pw.d.w.b._csrf()).status_code == 404
    verify(pw, vid)
    again = pw.officer.post(f"/api/post-approval/validity/versions/{vid}/verify", {"verified_date": "2029-03-31", "reason": "Compared against the document itself.", "idempotency_key": "k-retry-0001"})
    assert again.status_code == 200 and again.headers["X-Idempotent-Replay"] == "true"
    assert len(clocks_of(kind="DOCUMENT_VALIDITY")) == 1
    assert pw.officer.post(f"/api/post-approval/validity/versions/{vid}/verify", {**body, "verified_date": "2030-01-01"}).status_code == 409  # already verified: not rewritten
    assert pw.officer.post(f"/api/post-approval/validity/versions/{vid}/verify", {**body, "verified_date": "1990-01-01"}).status_code in (409, 422)


def test_the_source_of_a_date_and_a_recorded_review_cannot_be_rewritten(issued: PW):
    pw = issued
    vid = items(pw)[0]["versions"][0]["id"]
    verify(pw, vid)
    with SessionLocal() as db:
        v = db.get(ValidityVersion, uuid.UUID(vid))
        v.value_date = date(2040, 1, 1)
        with pytest.raises(ImmutableRecordError):
            db.flush()
        db.rollback()
        v = db.get(ValidityVersion, uuid.UUID(vid))
        v.verified_date = date(2040, 1, 1)
        with pytest.raises(ImmutableRecordError):
            db.flush()


def test_resolving_an_item_ends_its_clocks_but_keeps_the_history(issued: PW):
    pw = issued
    item = items(pw)[0]
    verify(pw, item["versions"][0]["id"])
    r = pw.officer.post(f"/api/post-approval/validity/items/{item['id']}/resolve", {"reason": "The policy no longer requires this document to be tracked (permitted change)."})
    assert r.status_code == 200 and r.json()["status"] == "RESOLVED"
    pw.sync()
    (c,) = clocks_of(kind="DOCUMENT_VALIDITY")
    assert c.status == "COMPLETED" and c.completion_reason == "resolved_by_authorised_person"
    with SessionLocal() as db:
        assert db.scalar(select(ValidityItem.resolution_reason)).startswith("The policy")
        assert len(db.scalars(select(ValidityVersion)).all()) == 1
    assert renew(pw, item["id"], expect=409)["code"] == "item_resolved"


def test_a_conflicting_second_date_is_flagged_for_review_not_applied():
    """A corrected document (uploaded in answer to a deficiency notice) that shows a different date is a conflict with the first, never a silent update."""
    pw = make_pworld()
    w = pw.w
    w.upload(data=digital_pdf([["Name of the Institution: Test Institute", "Date: 12/03/2024", "Reference No: RF-2024-9"], ["Lease term of the premises. Valid until: 31/03/2029"]]))
    from tests.evidence_world import drain

    drain()
    w.submit()
    notice = w.issued_notice()
    w.respond(notice["id"])  # uploads a corrected letter that says "Valid until: 31/03/2030"
    drain()
    pw.sync()
    (item,) = items(pw)
    assert [v["value_date"] for v in item["versions"]] == ["2029-03-31", "2030-03-31"]
    first, second = item["versions"]
    assert second["status"] == "NEEDS_REVIEW" and second["conflict"]["dates"] == ["2029-03-31"] and "differs" in second["needs_review_reason"]
    assert first["status"] == "UNVERIFIED"
    assert clocks_of(kind="DOCUMENT_VALIDITY") == []  # neither date is used until a person decides
