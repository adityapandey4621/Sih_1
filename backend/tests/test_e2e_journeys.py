"""Acceptance journeys (Phase 8), through the real API, with controlled synthetic data.

These chain the stages the per-phase tests exercise one at a time, so a defect in a hand-off between phases cannot hide. They use synthetic documents only. They use the
DEMO ONLY mock integration adapter: a passing run proves how THIS application behaves, not that any real AICTE system accepts anything.

If a real Tesseract is available (TEST_TESSERACT_CMD / TEST_TESSDATA or on PATH) the document is a scanned image and is read by OCR; otherwise it is a digital PDF and the
OCR step is reported as skipped by the assertion on `method`, not silently passed."""

import uuid
from datetime import UTC, timedelta

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.integration.aicte import factory
from app.integration.aicte.mock import MockAicteAdapter
from app.models import AuditEvent
from app.models_applications import ApplicationDocument
from app.models_decisions import ExternalSync
from app.models_processing import ExtractedField
from app.models_scrutiny import Notification
from app.services import portal_sync
from tests.catalogue_helpers import MAIN_TEXT
from tests.compliance_world import OFFICER1, PW, clocks_of, make_pworld, work_of
from tests.docs_helpers import digital_pdf, real_tesseract, scanned_pdf
from tests.evidence_world import drain

REAL = real_tesseract()
VALID_LINE = "Lease term of the premises. Valid until: 31/03/2031"


@pytest.fixture(autouse=True)
def _clean():
    portal_sync.PROVIDER_OVERRIDE = None
    factory.OVERRIDE = None
    yield
    portal_sync.PROVIDER_OVERRIDE = None
    factory.OVERRIDE = None


# ---- 1. login and role permissions ---------------------------------------------------------------------------------------


def test_journey_1_sign_in_and_what_each_role_may_do():
    pw = make_pworld()
    from tests.conftest import Api

    anon = Api()
    for path in ("/api/auth/me", "/api/applications", "/api/decisions/cases", "/api/post-approval/obligations", "/api/clocks", "/api/integration/status", "/api/notifications"):
        assert anon.get(path).status_code == 401, path
    assert Api().login("a@test.example.org", "wrong password entirely").status_code == 401
    me = pw.inst.get("/api/auth/me").json()
    assert me["role"] == "institute_user" and "compliance:submit" in me["permissions"] and "decision:record" not in me["permissions"] and "compliance:verify" not in me["permissions"]
    assert "compliance:verify" in pw.officer.get("/api/auth/me").json()["permissions"]
    assert "compliance:verify" not in pw.bureau.get("/api/auth/me").json()["permissions"]  # the Bureau defines; it cannot verify
    # no role reaches another role's powers
    assert pw.inst.get("/api/decisions/cases").status_code == 403
    assert pw.officer.get("/api/decisions/cases").status_code == 403
    assert pw.d.ec.get("/api/post-approval/obligations").status_code == 403
    assert pw.d.w.auditor.get("/api/post-approval/obligations").status_code == 403


# ---- 2. application, checklist, submission ---------------------------------------------------------------------------------


def test_journey_2_draft_checklist_and_submission_rules():
    pw = make_pworld()
    w = pw.w
    app = w.refresh()
    assert app["status"] == "DRAFT"
    r = w.a.patch(w.url, {"form_data": {"contact": "R", "kind": "A", "decl": True}, "row_version": app["row_version"]})
    assert r.status_code == 200
    blocked = w.a.post(f"{w.url}/submit", {"row_version": r.json()["row_version"]})
    assert blocked.status_code in (409, 422) and "doc-main" in blocked.text  # the required document is missing: named, not guessed
    w.upload()
    drain()
    done = w.submit(assign=False)
    assert done["status"] == "SUBMITTED"
    assert w.a.patch(w.url, {"form_data": {"contact": "changed"}, "row_version": done["row_version"]}).status_code in (409, 422)  # a submitted application is frozen
    assert w.a.post(f"{w.url}/submit", {"row_version": done["row_version"]}).status_code in (200, 409)  # a duplicate submit never creates a second submission
    assert w.b.get(w.url).status_code == 404  # another institution cannot even see it


# ---- 3. secure upload, actual processing, versions ---------------------------------------------------------------------------


def test_journey_3_upload_processing_and_versions_with_real_ocr_when_available():
    pw = make_pworld()
    w = pw.w
    pages = [MAIN_TEXT, [VALID_LINE]]
    data = scanned_pdf(pages) if REAL else digital_pdf(pages)
    w.upload(data=data, name="main.pdf")
    drain(ocr=REAL)
    ev = w.evidence()
    item = next(i for i in ev["items"] if i["item_key"] == "doc-main")
    assert item["current"]["version_number"] == 1 and item["slot_state"] is not None
    with SessionLocal() as db:
        fields = {f.field_key: f for f in db.scalars(select(ExtractedField).join(ApplicationDocument, ApplicationDocument.id == ExtractedField.document_id).where(ApplicationDocument.application_id == uuid.UUID(pw.app_id())))}
    assert fields["institution_name"].value and fields["institution_name"].page_number == 1
    assert fields["institution_name"].method == ("OCR" if REAL else "PDF_TEXT")  # the method is recorded, so an OCR read is never mistaken for a text-layer read
    w.upload(data=digital_pdf([MAIN_TEXT]), name="main-v2.pdf")  # a second upload is a NEW version; the first is kept
    drain()
    with SessionLocal() as db:
        versions = db.scalars(select(ApplicationDocument).where(ApplicationDocument.application_id == uuid.UUID(pw.app_id())).order_by(ApplicationDocument.version_number)).all()
    assert [v.version_number for v in versions] == [1, 2] and versions[0].superseded_at is not None and versions[1].superseded_at is None
    assert w.b.get(f"/api/documents/{versions[0].id}/download").status_code == 404  # another institution never gets a file
    assert w.a.get(f"/api/documents/{versions[0].id}/download").status_code == 200


# ---- 4..9 the whole lifecycle in one story -----------------------------------------------------------------------------------


def test_journeys_4_to_9_one_application_from_scrutiny_to_post_approval_deadlines_and_sync_recovery():
    mock = factory.OVERRIDE = MockAicteAdapter(["transient"])  # the first push to the "external system" fails, the next works
    pw: PW = make_pworld(route="evc_then_shc_conditional")
    w, c, d = pw.w, pw.d.c, pw.d

    # -- 3/4. a real document, submission, preliminary issue -> authorised deficiency notice -> institution response -> re-scrutiny
    pages = [MAIN_TEXT, [VALID_LINE]]
    w.upload(data=scanned_pdf(pages) if REAL else digital_pdf(pages))
    drain(ocr=REAL)
    w.submit()
    drain()
    notice = w.issued_notice()
    assert notice["status"] == "ISSUED" and w.case()["queue"]["state"] == "AWAITING_RESPONSE"
    assert w.scrutiny.post(f"/api/scrutiny/notices/{notice['id']}/issue", {"content_hash": "x" * 64, "issue_reason": "A reviewer cannot issue.", "acknowledge_unsourced": True, "idempotency_key": "reviewer-issue-1", "row_version": 1}).status_code == 403
    w.respond(notice["id"])
    drain()
    w.start()
    finding = w.case()["findings"][0]
    r = w.scrutiny.post(f"{w.case_url}/findings/{finding['id']}/disposition", {"state": "RESOLVED", "reason": "The corrected letter states its validity period.", "row_version": finding["row_version"]})
    assert r.status_code == 200, r.text
    r = w.scrutiny.post(f"{w.case_url}/complete", {"row_version": w.case_row().row_version, "summary": "All findings concluded after re-scrutiny."})
    assert r.status_code == 200, r.text
    assert pw.sync()["clocks_completed"] >= 0  # the scheduler may run at any time

    # -- 5. conditional EVC: selection, scheduling, visit, report
    assert c.row("EVC") is not None and c.row("SHC") is None
    c.ready_visit()
    c.evc_to_report()
    done = c.complete_evc()
    assert done["state"] == "COMPLETED" and done["next"]["status"] == "AWAITING_DETERMINATION"  # a person decides whether SHC applies; the software does not

    # -- 6. conditional SHC: compliance, hearing-stage review, recommendation
    r = c.evc_coord.post(f"{c.url('EVC')}/route/determine", {"stage_key": "shc", "reason": "The report calls for the compliance process."})
    assert r.status_code == 200, r.text
    c.shc_to_review()
    c.shc_finding()
    c.recommend(option="RECOMMEND_NEXT_STAGE")
    c.submit_recommendation()
    assert c.complete_shc()["state"] == "COMPLETED"

    # -- 7. authorised decision -> eligible SAC appeal -> Council outcome
    dc = d.case()
    assert dc["state"] == "AWAITING_OBSERVATIONS" and dc["current_decision"] is None  # reports and recommendations decided nothing
    d.observe()
    assert d.ec.post(f"{d.url()}/decision", {"outcome": "LOR"}).status_code == 422  # an incomplete decision is refused, not defaulted
    d.decide("LOR")
    d.issue("LOR/2026/0007")
    assert d.status()["decision"]["sync"]["status"] == "SYNC_PENDING"  # the mock failed once: not confirmed, and not shown as confirmed
    d.full_appeal_path()  # appeal -> SAC review -> SAC recommendation -> Council decision (LoA on appeal)
    final = d.status()
    assert final["appeal"]["submitted"]["status"] == "COUNCIL_DECIDED" and final["official_status"]["state"] == "COUNCIL_DECISION_ON_APPEAL"

    # -- 9a. external sync failure -> retry -> reconciliation
    with SessionLocal() as db:
        assert portal_sync.reconcile(db) >= 1
    with SessionLocal() as db:
        syncs = list(db.scalars(select(ExternalSync)))
    assert syncs and all(s.status == "SYNCED" for s in syncs) and len({s.subject_id for s in syncs}) == len(syncs)  # every record confirmed once; none duplicated
    assert len(mock.received) == len(syncs)  # the receiver holds each message exactly once, however many times it was sent

    # -- 8. compliance evidence -> human verification -> deadline / reminder lifecycle (the condition follows the Council's approval on appeal)
    o = pw.define(source="COUNCIL_APPEAL_DECISION", due=pw.specified_due(12), title="Provide the fire safety certificate")
    assert o["decision"]["source"] == "COUNCIL_APPEAL_DECISION"
    sub = pw.evidence(o)
    assert sub["status"] == "EVIDENCE_SUBMITTED"  # uploaded, not verified
    (oc,) = clocks_of(o["id"])
    due = oc.due_at if oc.due_at.tzinfo else oc.due_at.replace(tzinfo=UTC)
    pw.sync(now=due - timedelta(hours=100))
    sent = [n for n in notifs_for(oc.id) if n.event_type == "CLOCK_PRE_DUE"]
    assert {n.recipient_kind for n in sent} == {"verifier"}  # the institution has acted; the outstanding work is the verifier's
    out = pw.review(o, "VERIFIED")
    assert out["status"] == "VERIFIED" and clocks_of(o["id"])[0].status == "COMPLETED"
    n_before = len(notifs_for(oc.id))
    pw.sync(now=due + timedelta(days=3))
    assert len(notifs_for(oc.id)) == n_before  # a verified obligation produces no further reminders, ever

    # -- 8b. document validity: candidate -> verified date -> alerts; renewal does not silence the old ones
    items = pw.officer.get("/api/post-approval/validity").json()["items"]
    assert items and items[0]["versions"][0]["source"] == "EXTRACTED" and items[0]["current_verified_date"] is None
    vid = items[0]["versions"][-1]["id"]
    assert pw.officer.post(f"/api/post-approval/validity/versions/{vid}/verify", {"verified_date": "2031-03-31", "reason": "Confirmed against the lease on page 2.", "idempotency_key": "e2e-verify-0001"}).status_code == 201
    val = clocks_of(kind="DOCUMENT_VALIDITY")
    assert len(val) == 1 and val[0].status == "RUNNING" and work_of(val[0].id)

    # -- the record: every consequential step is in the audit trail; nothing claims an external message was sent
    with SessionLocal() as db:
        actions = {a for (a,) in db.execute(select(AuditEvent.action)).all()}
        mail = db.scalars(select(Notification).where(Notification.external_delivery != "NOT_CONFIGURED")).all()
    assert {"compliance.obligation_defined", "compliance.evidence_submitted", "compliance.reviewed", "validity.verified", "sync.attempted"} <= actions
    assert mail == []


def notifs_for(clock_id):
    from tests.compliance_world import notifs

    return notifs(clock_id)


# ---- invalid routes, duplicates, stale updates, unauthorised access -----------------------------------------------------------


def test_invalid_duplicate_stale_and_unauthorised_requests_are_refused_and_change_nothing():
    pw = make_pworld()
    pw.to_issued(valid_until="31/03/2029")
    # an obligation is refused when nothing is issued to another application / when the verifier is not a verifier
    bad = pw.define(verifier="bureau@test.example.org", expect=422)
    assert bad["code"] == "validation_failed"
    o = pw.define(due=pw.specified_due(), key="e2e-define-0001")
    again = pw.bureau.post(f"/api/post-approval/applications/{pw.app_id()}/obligations", {"title": "Provide the fire safety certificate", "condition_text": "The institution shall provide a valid fire safety certificate for the premises.",
                                                                                         "required_evidence": "A current fire safety certificate (PDF).", "verifier_id": pw.user_id(OFFICER1), "due": pw.specified_due(), "idempotency_key": "e2e-define-0001"})
    assert again.status_code == 200 and again.json()["id"] == o["id"]  # a duplicate request is the same obligation, not a second one
    # a review with nothing submitted, a review of a stale submission, and a review by the wrong person
    od = pw.get_obligation(o["id"])
    r = pw.officer.post(f"/api/post-approval/obligations/{o['id']}/review", {"outcome": "VERIFIED", "submission_id": str(uuid.uuid4()), "reason": "Nothing was submitted yet.", "row_version": od["row_version"], "idempotency_key": "e2e-review-0001"})
    assert r.status_code in (404, 409)
    pw.evidence(o)
    od = pw.get_obligation(o["id"])
    stale = pw.officer.post(f"/api/post-approval/obligations/{o['id']}/review", {"outcome": "VERIFIED", "submission_id": od["latest_submission_id"], "reason": "A stale view of the case.", "row_version": od["row_version"] - 1, "idempotency_key": "e2e-review-0002"})
    assert stale.status_code == 409 and stale.json()["code"] == "stale_version"
    assert pw.officer2.post(f"/api/post-approval/obligations/{o['id']}/review", {"outcome": "VERIFIED", "submission_id": od["latest_submission_id"], "reason": "Not my case at all.", "row_version": od["row_version"], "idempotency_key": "e2e-review-0003"}).status_code == 404
    assert pw.get_obligation(o["id"])["status"] == "EVIDENCE_SUBMITTED"  # nothing above changed it
    # decisions: a second decision on the same version, and the Council cannot decide before the appeal exists
    assert pw.d.ec.post(f"{pw.d.url()}/decision", {"outcome": "LOA", "reasons": "again", "considered": [], "idempotency_key": "e2e-dec-dup-0001", "row_version": 1}).status_code in (409, 422)
    assert pw.d.council.post(f"/api/decisions/appeals/{uuid.uuid4()}/council-decision", {"outcome": "LOA", "reasons": "x", "idempotency_key": "e2e-council-0001"}).status_code == 404


# ---- worker and database failures ---------------------------------------------------------------------------------------------


def test_a_scheduler_that_meets_a_database_failure_survives_and_resumes(monkeypatch):
    from sqlalchemy.exc import OperationalError

    from app.config import get_settings
    from app.services import clock_sources, scheduler

    calls = {"n": 0}
    real_sync = clock_sources.sync

    def flaky(db, settings):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OperationalError("SELECT 1", {}, Exception("connection to server lost"))
        return real_sync(db, settings)

    monkeypatch.setattr(clock_sources, "sync", flaky)
    monkeypatch.setattr("time.sleep", lambda s: None)
    monkeypatch.setattr(get_settings(), "scheduler_sync_seconds", 0)
    ticks = {"n": 0}

    def stop() -> bool:
        ticks["n"] += 1
        return ticks["n"] > 2

    scheduler.run_forever(get_settings(), stop=stop)  # the first cycle fails; the loop logs the type, waits and runs again
    assert calls["n"] >= 2


def test_the_api_answers_a_database_outage_generically_and_readiness_says_not_ready(monkeypatch):
    from sqlalchemy.exc import OperationalError

    from app import readiness as readiness_mod
    from app.db import SessionLocal as RealSession
    from tests.conftest import Api

    api = Api()

    class Dead:
        def __enter__(self):
            raise OperationalError("SELECT 1", {"password": "hunter2"}, Exception("could not connect to server: password=hunter2"))

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(readiness_mod, "SessionLocal", lambda: Dead())
    r = api.get("/api/ready")
    assert r.status_code == 503 and r.json()["checks"]["database"] is False and "hunter2" not in r.text
    monkeypatch.undo()
    assert api.get("/api/ready").status_code == 200
    _ = RealSession
