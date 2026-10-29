"""Phase 7: compliance obligations. An upload is not verification; only the assigned verifier's recorded review verifies; nothing is revoked or penalised."""

import uuid

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.guards import ImmutableRecordError
from app.models import AuditEvent
from app.models_applications import Application
from app.models_compliance import Obligation
from app.models_decisions import DecisionCase
from app.models_scrutiny import Notification
from tests.compliance_world import OFFICER1, OFFICER2, PW, clocks_of, make_pworld, obligation_row


@pytest.fixture
def pw() -> PW:
    return make_pworld()


@pytest.fixture
def issued(pw: PW) -> PW:
    pw.to_issued()
    return pw


def test_an_obligation_can_only_be_recorded_against_an_issued_decision(pw: PW):
    pw.w.upload()
    r = pw.bureau.post(f"/api/post-approval/applications/{pw.app_id()}/obligations", {"title": "Something", "condition_text": "A condition text.", "required_evidence": "Evidence text.", "verifier_id": pw.user_id(OFFICER1), "due": {"basis": "NONE"}, "idempotency_key": "key-before-issue"})
    assert r.status_code == 409 and r.json()["code"] == "no_issued_decision"


def test_who_may_define_verify_and_submit(issued: PW):
    pw = issued
    body = {"title": "Provide the certificate", "condition_text": "A condition text.", "required_evidence": "Evidence text.", "verifier_id": pw.user_id(OFFICER1), "due": {"basis": "NONE"}}
    for who, label in ((pw.inst, "institution"), (pw.officer, "compliance officer"), (pw.d.ec, "executive committee"), (pw.d.council, "council")):
        r = who.post(f"/api/post-approval/applications/{pw.app_id()}/obligations", {**body, "idempotency_key": f"k-{label}-0001"})
        assert r.status_code == 403, label
    o = pw.define()
    # the Bureau defines but cannot verify; the institution cannot verify; a compliance officer cannot submit evidence
    pw.evidence(o)
    o2 = pw.get_obligation(o["id"])
    rv = {"outcome": "VERIFIED", "submission_id": o2["latest_submission_id"], "reason": "Looks fine to me, verified.", "row_version": o2["row_version"], "idempotency_key": "k-bureau-review"}
    assert pw.bureau.post(f"/api/post-approval/obligations/{o['id']}/review", rv).status_code == 403
    assert pw.inst.post(f"/api/post-approval/obligations/{o['id']}/review", {**rv, "idempotency_key": "k-inst-review"}).status_code == 403
    r = pw.officer.http.post(f"/api/post-approval/obligations/{o['id']}/evidence", data={"idempotency_key": "k-officer-evid", "row_version": "1"}, files=[("files", ("a.pdf", b"%PDF-1.4\n%%EOF\n", "application/pdf"))], headers=pw.officer._csrf())
    assert r.status_code == 403


def test_uploading_evidence_never_marks_the_obligation_verified(issued: PW):
    pw = issued
    o = pw.define(due=pw.specified_due())
    assert o["status"] == "AWAITING_EVIDENCE"
    sub = pw.evidence(o)
    assert sub["status"] == "EVIDENCE_SUBMITTED" and "not verification" in sub["notes"]["upload"].lower()
    row = obligation_row(o["id"])
    assert row.status == "EVIDENCE_SUBMITTED" and row.closed_at is None
    (clock,) = clocks_of(o["id"])
    assert clock.status == "RUNNING"  # submitted but unverified: the obligation is still open
    # the institution has no way to verify, and a second submission while the first is under review is refused
    assert pw.evidence(sub, expect=409)["code"] == "invalid_transition"


def test_the_assigned_verifier_records_the_outcome_and_the_clock_finishes(issued: PW):
    pw = issued
    o = pw.define(due=pw.specified_due())
    pw.evidence(o)
    out = pw.review(o, "VERIFIED")
    assert out["status"] == "VERIFIED"
    (clock,) = clocks_of(o["id"])
    assert clock.status == "COMPLETED" and clock.completion_reason == "obligation_verified"
    # the institution sees the reason but never the verifier's internal note
    inst_view = pw.get_obligation(o["id"], pw.inst)
    review = inst_view["submissions_detail"][0]["review"]
    assert review["reason"].startswith("The certificate") and "internal_note" not in review
    assert "Checked the issuing office" not in str(inst_view)
    official = pw.get_obligation(o["id"], pw.officer)
    assert official["submissions_detail"][0]["review"]["internal_note"].startswith("Checked")


def test_a_requested_correction_creates_new_document_versions_and_keeps_the_old(issued: PW):
    pw = issued
    o = pw.define()
    pw.evidence(o)
    pw.review(o, "CORRECTION_REQUESTED", reason="The certificate is expired; please send the current one.")
    o = pw.get_obligation(o["id"], pw.inst)
    assert o["status"] == "CORRECTION_REQUESTED" and o["can"]["submit"]
    pw.evidence(o, files=[("Fire certificate", "fire-v2.pdf", b"%PDF-1.4\n%%EOF\n% v2", "application/pdf")])
    detail = pw.get_obligation(o["id"], pw.officer)
    docs = [d for s in detail["submissions_detail"] for d in s["documents"]]
    assert [(d["label"], d["version_number"]) for d in docs] == [("Fire certificate", 1), ("Fire certificate", 2)]
    assert detail["submissions_detail"][0]["review"]["outcome"] == "CORRECTION_REQUESTED"


def test_not_met_is_a_record_and_changes_nothing_else(issued: PW):
    pw = issued
    with SessionLocal() as db:
        before = (db.get(Application, uuid.UUID(pw.app_id())).status, db.scalar(select(DecisionCase.state)))
    o = pw.define()
    pw.evidence(o)
    out = pw.review(o, "NOT_MET", reason="The certificate does not cover the second building.")
    assert out["status"] == "NOT_MET" and "imposes nothing" in out["notes"]["penalty"]
    with SessionLocal() as db:
        after = (db.get(Application, uuid.UUID(pw.app_id())).status, db.scalar(select(DecisionCase.state)))
    assert before == after  # the application and the decision case are untouched
    text = str(pw.inst.get("/api/notifications").json())
    assert "this system takes none" in text  # the institution is told a consequence is for the competent authority, not for this system


def test_only_the_assigned_verifier_can_review_and_others_cannot_even_see_it(issued: PW):
    pw = issued
    o = pw.define()
    pw.evidence(o)
    assert pw.officer2.get(f"/api/post-approval/obligations/{o['id']}").status_code == 404
    od = pw.get_obligation(o["id"])
    body = {"outcome": "VERIFIED", "submission_id": od["latest_submission_id"], "reason": "Trying to verify someone else's item.", "row_version": od["row_version"], "idempotency_key": "k-other-officer"}
    assert pw.officer2.post(f"/api/post-approval/obligations/{o['id']}/review", body).status_code == 404
    assert obligation_row(o["id"]).status == "EVIDENCE_SUBMITTED"
    resp = pw.officer2.get("/api/post-approval/obligations")
    assert resp.status_code == 200, resp.text
    assert resp.json()["total"] == 0
    # reassigning (a Bureau power) makes it theirs
    r = pw.bureau.post(f"/api/post-approval/obligations/{o['id']}/reassign", {"verifier_id": pw.user_id(OFFICER2), "reason": "Workload."})
    assert r.status_code == 200
    assert pw.officer.get(f"/api/post-approval/obligations/{o['id']}").status_code == 404
    assert pw.officer2.get(f"/api/post-approval/obligations/{o['id']}").status_code == 200


def test_another_institution_sees_nothing(issued: PW):
    pw = issued
    o = pw.define()
    assert pw.d.w.b.get(f"/api/post-approval/obligations/{o['id']}").status_code == 404
    resp = pw.d.w.b.get("/api/post-approval/obligations")
    assert resp.status_code == 200, resp.text
    assert resp.json()["total"] == 0
    assert pw.d.w.b.get("/api/post-approval/overview").json()["obligations"]["total"] == 0
    r = pw.d.w.b.http.post(f"/api/post-approval/obligations/{o['id']}/evidence", data={"idempotency_key": "k-cross-inst-1", "row_version": "1"}, files=[("files", ("a.pdf", b"%PDF-1.4\n%%EOF\n", "application/pdf"))], headers=pw.d.w.b._csrf())
    assert r.status_code == 404
    with SessionLocal() as db:
        assert db.scalar(select(AuditEvent.id).where(AuditEvent.action == "compliance.access_denied")) is not None


def test_requests_are_idempotent_and_stale_versions_are_refused(issued: PW):
    pw = issued
    o = pw.define(key="define-key-0001")
    again = pw.bureau.post(f"/api/post-approval/applications/{pw.app_id()}/obligations", {"title": "Provide the fire safety certificate", "condition_text": "The institution shall provide a valid fire safety certificate for the premises.",
                                                                                         "required_evidence": "A current fire safety certificate (PDF).", "verifier_id": pw.user_id(OFFICER1), "due": {"basis": "NONE"}, "idempotency_key": "define-key-0001"})
    assert again.status_code == 200 and again.headers["X-Idempotent-Replay"] == "true" and again.json()["id"] == o["id"]
    with SessionLocal() as db:
        assert len(db.scalars(select(Obligation)).all()) == 1
    sub = pw.evidence(o, key="evidence-key-0001")
    replay = pw.evidence(o, key="evidence-key-0001", expect=200)
    assert replay["id"] == sub["id"] and len(replay["submissions_detail"]) == 1
    stale = pw.review(sub, "VERIFIED", expect=201, key="review-key-0001")
    assert stale["status"] == "VERIFIED"
    assert pw.review(sub, "VERIFIED", expect=200, key="review-key-0001")["status"] == "VERIFIED"  # the same request sent again
    # a stale row_version on a fresh obligation
    o2 = pw.define(title="Another obligation to satisfy")
    pw.evidence(o2)
    od = pw.get_obligation(o2["id"])
    r = pw.officer.post(f"/api/post-approval/obligations/{o2['id']}/review", {"outcome": "VERIFIED", "submission_id": od["latest_submission_id"], "reason": "A reason that is long enough.", "row_version": od["row_version"] - 1, "idempotency_key": "k-stale-rv-1"})
    assert r.status_code == 409 and r.json()["code"] == "stale_version"


def test_due_dates_come_from_a_usable_policy_or_a_cited_date_never_from_a_default(issued: PW):
    pw = issued
    by_policy = pw.define(due={"basis": "POLICY", "policy_key": "cmp-30"}, title="Obligation due by policy")
    assert by_policy["due"]["basis"] == "POLICY" and by_policy["clock"]["policy"]["duration_value"] == 30
    (c,) = clocks_of(by_policy["id"])
    assert c.policy_key == "cmp-30" and c.deadline_type == "INTERNAL_SLA"
    # a date needs its citation; a past date is refused; an unknown policy is refused; no due date means no clock
    assert pw.define(due={"basis": "SPECIFIED", "due_at": pw.specified_due()["due_at"]}, expect=422, title="No citation given", key="k-no-citation")
    assert pw.define(due={"basis": "POLICY", "policy_key": "nope"}, expect=422, title="Unknown policy", key="k-unknown-policy")
    none = pw.define(due={"basis": "NONE"}, title="No due date", key="k-no-due")
    assert none["due"]["due_at"] is None and clocks_of(none["id"]) == []
    cited = pw.define(due=pw.specified_due(20), title="Cited date", key="k-cited-date")
    assert cited["due"]["authority"] == "LoA para 4(b)" and clocks_of(cited["id"])[0].deadline_type == "SPECIFIED"


def test_withdrawing_an_obligation_ends_its_clock_and_keeps_the_record(issued: PW):
    pw = issued
    o = pw.define(due=pw.specified_due())
    r = pw.bureau.post(f"/api/post-approval/obligations/{o['id']}/withdraw", {"reason": "Recorded against the wrong application by mistake.", "row_version": o["row_version"]})
    assert r.status_code == 200 and r.json()["status"] == "WITHDRAWN"
    (c,) = clocks_of(o["id"])
    assert c.status == "COMPLETED" and c.completion_reason == "obligation_withdrawn"
    assert pw.officer.get(f"/api/post-approval/obligations/{o['id']}").json()["withdrawn_reason"].startswith("Recorded")


def test_a_recorded_obligation_cannot_be_rewritten(issued: PW):
    pw = issued
    o = pw.define()
    with SessionLocal() as db:
        row = db.get(Obligation, uuid.UUID(o["id"]))
        row.condition_text = "A softer condition."
        with pytest.raises(ImmutableRecordError):
            db.flush()
        db.rollback()
        row = db.get(Obligation, row.id)
        row.status = "VERIFIED"  # a status cannot be assigned either: it moves only through the workflow
        with pytest.raises(ImmutableRecordError):
            db.flush()


def test_the_institution_is_told_in_the_app_only_and_no_email_is_claimed(issued: PW):
    pw = issued
    pw.define()
    with SessionLocal() as db:
        rows = db.scalars(select(Notification).where(Notification.event_type == "OBLIGATION_DEFINED")).all()
    assert rows and all(r.delivery_state == "STORED_IN_APP" and r.external_delivery == "NOT_CONFIGURED" for r in rows)
    kinds = {r.recipient_kind for r in rows}
    assert kinds == {"institution", "verifier"}


def test_malware_scan_policy_is_honest(issued: PW, monkeypatch):
    """With no scanner, a file is recorded as NOT_SCANNED - never as clean - and a required scan with no scanner refuses the file."""
    pw = issued
    o = pw.define()
    pw.evidence(o)
    docs = pw.get_obligation(o["id"], pw.officer)["submissions_detail"][0]["documents"]
    assert docs[0]["scan_status"] == "NOT_SCANNED"
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "require_malware_scan", True)
    o2 = pw.define(title="Needs a scanned file", key="k-scan-required")
    r = pw.evidence(o2, expect=503)
    assert r["code"] == "scanner_required"
    assert obligation_row(o2["id"]).status == "AWAITING_EVIDENCE"
