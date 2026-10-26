"""The EVC visit: scoped access, observations, private evidence, the versioned report, completion. A report is not an approval."""

import io
import uuid

import pytest
from fastapi import UploadFile
from sqlalchemy import select
from starlette.datastructures import Headers

from app.config import get_settings
from app.db import SessionLocal
from app.domain import ObservationStatus
from app.guards import ImmutableRecordError
from app.models import AuditEvent, User
from app.models_applications import Application
from app.models_committee import CommitteeEvidence, CommitteeReport, CommitteeSchedule, EvcObservation
from app.models_scrutiny import Notification
from app.services import committee_texts as ctexts
from app.services import documents as docs_svc
from tests.committee_world import make_cworld
from tests.docs_helpers import digital_pdf
from tests.evidence_world import doc_rows, storage


def reports(kind=None):
    with SessionLocal() as db:
        return list(db.scalars(select(CommitteeReport).order_by(CommitteeReport.version_number)))


@pytest.fixture
def c():
    c = make_cworld("evc_only")
    c.finish_scrutiny()
    c.ready_visit()
    return c


# ---- scope ----------------------------------------------------------------------------------------------


def test_an_assigned_member_sees_the_case_and_its_documents_and_an_unassigned_member_does_not(c):
    d = c.case("EVC", c.evc1)
    assert d["scope"] == ["member"] and d["application"]["reference"] == c.w.app["reference"]
    ev = c.evc1.get(f"{c.url('EVC')}/evidence")
    assert ev.status_code == 200 and ev.json()["items"][0]["item_key"] == "doc-main"
    doc_id = doc_rows(c.w.app["id"])[0].id
    assert c.evc1.get(f"/api/documents/{doc_id}").status_code == 200
    assert c.evc1.get(f"/api/documents/{doc_id}/download").status_code == 200
    # the other EVC member holds the same role but is not assigned: nothing is visible by changing an identifier
    assert c.evc2.get(c.url("EVC")).status_code == 404
    assert c.evc2.get(f"{c.url('EVC')}/evidence").status_code == 404
    assert c.evc2.get(f"/api/documents/{doc_id}").status_code == 404
    assert c.evc2.get(f"/api/documents/{doc_id}/download").status_code == 404
    assert c.evc2.get("/api/evc/cases").json()["items"] == []
    assert [i["id"] for i in c.evc1.get("/api/evc/cases").json()["items"]] == [str(c.row("EVC").id)]
    with SessionLocal() as db:
        assert db.scalar(select(AuditEvent.id).where(AuditEvent.action == "committee.access_denied")) is not None


def test_a_coordinator_can_read_the_case_documents_and_an_institution_cannot_use_the_staff_endpoints(c):
    doc_id = doc_rows(c.w.app["id"])[0].id
    assert c.evc_coord.get(f"/api/documents/{doc_id}").status_code == 200
    assert c.w.a.get(c.url("EVC")).status_code == 403
    assert c.shc_coord.get(c.url("EVC")).status_code == 403  # the SHC coordinator has no EVC permission at all


def test_a_member_cannot_act_on_another_case_by_changing_the_identifier(c):
    other = uuid.uuid4()
    assert c.evc1.post(f"/api/evc/cases/{other}/visit/start", {"row_version": 1}).status_code == 404
    assert c.evc1.put(f"/api/evc/cases/{other}/report", {"sections": {"summary": "x"}, "row_version": 1}).status_code == 404
    # an EVC case id presented on the SHC routes is simply not found
    assert c.shc_coord.get(f"/api/shc/cases/{c.row('EVC').id}").status_code == 404


# ---- the visit and observations ---------------------------------------------------------------------------


def test_the_visit_starts_only_from_a_confirmed_schedule_by_an_assigned_member(c):
    assert c.evc2.post(f"{c.url('EVC')}/visit/start", {"row_version": c.rv("EVC")}).status_code == 404
    assert c.evc_coord.post(f"{c.url('EVC')}/visit/start", {"row_version": c.rv("EVC")}).status_code == 403
    assert c.evc1.post(f"{c.url('EVC')}/visit/start", {"row_version": c.rv("EVC") - 1}).json()["code"] == "stale_version"
    d = c.start_visit()
    assert d["state"] == "VISIT_IN_PROGRESS" and d["evc"]["draft"]["row_version"] == 1 and d["evc"]["draft"]["status"] == "DRAFT"
    assert c.evc1.post(f"{c.url('EVC')}/visit/start", {"row_version": c.rv("EVC")}).json()["code"] == "invalid_transition"
    assert len(reports()) == 1
    with SessionLocal() as db:
        assert db.scalar(select(Notification.id).where(Notification.event_type == "VISIT_STARTED")) is not None


def test_observations_cannot_be_recorded_before_the_visit_starts(c):
    r = c.evc1.put(f"{c.url('EVC')}/observations/demo-premises", {"status": "OBSERVED", "note": "Seen.", "row_version": None})
    assert r.status_code == 409 and r.json()["code"] == "invalid_transition"


def test_an_observation_records_what_was_seen_without_calling_anything_compliant(c):
    c.start_visit()
    d = c.observe("demo-premises", note="The premises match the application's description.", internal_note="Coordinator: check the plan again.", shortcoming=True)
    o = d["evc"]["observations"][0]
    assert o["status"] == "OBSERVED" and o["shortcoming"] is True and o["recorded_by"] == "Test evc_member" and o["internal_note"].startswith("Coordinator")
    assert {s.value for s in ObservationStatus} == {"OBSERVED", "NOT_OBSERVED", "EVIDENCE_UNAVAILABLE", "UNCERTAIN"}  # no "compliant" / "approved"
    c.observe("demo-premises", status="UNCERTAIN", note="Could not see the second floor.")
    assert c.case("EVC", c.evc1)["evc"]["observations"][0]["status"] == "UNCERTAIN"
    c.observe("demo-premises", status="COMPLIANT", expect=422)
    c.observe("demo-premises", note="", expect=422)
    assert c.evc1.put(f"{c.url('EVC')}/observations/not-an-item", {"status": "OBSERVED", "note": "x y z", "row_version": None}).status_code == 404


def test_a_stale_observation_edit_is_refused_so_nobody_overwrites_a_colleague(c):
    c.start_visit()
    c.observe("demo-premises")
    r = c.evc1.put(f"{c.url('EVC')}/observations/demo-premises", {"status": "OBSERVED", "note": "Another version.", "row_version": 99})
    assert r.status_code == 409 and r.json()["code"] == "stale_version"
    r = c.evc1.put(f"{c.url('EVC')}/observations/demo-premises", {"status": "OBSERVED", "note": "Missing version.", "row_version": None})
    assert r.status_code == 409


def test_an_observation_links_to_the_exact_document_version_of_this_application_only(c):
    c.start_visit()
    doc = doc_rows(c.w.app["id"])[0]
    d = c.observe("demo-premises", document_refs=[{"document_id": str(doc.id), "page": 1}])
    ref = d["evc"]["observations"][0]["document_refs"][0]
    assert ref["document_id"] == str(doc.id) and ref["version_number"] == 1 and ref["item_key"] == "doc-main" and ref["page"] == 1
    # a document that belongs to another application cannot be cited
    app_b = c.w.b.post("/api/applications", {"category_id": str(c.w.cat.category_id), "academic_year": "2026-27"}).json()
    r = c.w.b.upload(f"/api/applications/{app_b['id']}/documents", "doc-main", "b.pdf", digital_pdf(["Another institution."]))
    assert r.status_code in (200, 201)
    foreign = [d for d in doc_rows(app_b["id"])][0]
    c.observe("demo-equipment", document_refs=[{"document_id": str(foreign.id)}], expect=422)
    c.observe("demo-equipment", document_refs=[{"document_id": str(uuid.uuid4())}], expect=422)


# ---- private evidence ---------------------------------------------------------------------------------------


def test_visit_evidence_is_stored_privately_and_only_the_committee_can_download_it(c):
    c.start_visit()
    d = c.evidence_pdf("demo-equipment")
    e = d["evc"]["evidence"][0]
    assert e["kind"] == "FILE" and e["sha256"] and e["filename"] == "photo-list.pdf" and e["download"].startswith("/api/evc/evidence/")
    assert c.evc1.get(e["download"]).status_code == 200 and c.evc_coord.get(e["download"]).status_code == 200
    assert c.evc2.get(e["download"]).status_code == 404  # not assigned
    assert c.w.a.get(e["download"]).status_code == 403  # the institution has no access
    row = c.evc1.get(e["download"])
    assert row.headers["cache-control"] == "private, no-store" and row.headers["x-content-type-options"] == "nosniff"
    with SessionLocal() as db:
        ev = db.scalar(select(CommitteeEvidence))
        assert ev.storage_key and "/" not in ev.storage_key and not ev.storage_key.endswith(".pdf")  # a generated key, never the user's filename


def test_evidence_files_are_checked_for_type_and_size_and_nothing_is_stored_on_refusal(c):
    c.start_visit()
    bad = c.evc1.http.post(f"{c.url('EVC')}/evidence", files={"file": ("notes.txt", b"hello", "text/plain")}, data={"description": "x"}, headers=c.evc1._csrf())
    assert bad.status_code == 415
    mismatch = c.evc1.http.post(f"{c.url('EVC')}/evidence", files={"file": ("image.png", digital_pdf(["x"]), "image/png")}, data={"description": "x"}, headers=c.evc1._csrf())
    assert mismatch.status_code == 415
    empty = c.evc1.http.post(f"{c.url('EVC')}/evidence", files={"file": ("e.pdf", b"", "application/pdf")}, data={"description": "x"}, headers=c.evc1._csrf())
    assert empty.status_code in (415, 422)
    with SessionLocal() as db:
        assert db.scalars(select(CommitteeEvidence)).all() == []


def test_evidence_can_also_be_a_reference_to_material_held_elsewhere_and_is_never_fetched(c):
    c.start_visit()
    r = c.evc1.post(f"{c.url('EVC')}/evidence/link", {"url": "https://trust.example.org/visit-video", "description": "The institution's recording (cl. 1.9(f))", "item_key": "demo-premises"})
    assert r.status_code == 201
    e = r.json()["evc"]["evidence"][0]
    assert e["kind"] == "LINK" and e["url"] == "https://trust.example.org/visit-video" and e["download"] is None
    for bad in ({"url": "javascript:alert(1)", "description": "x"}, {"url": "ftp://x.example.org/a", "description": "x"}, {"url": "https://ok.example.org/x", "description": ""}):
        assert c.evc1.post(f"{c.url('EVC')}/evidence/link", bad).status_code == 422


def test_removed_evidence_stays_on_record_and_evidence_is_frozen_once_the_report_is_submitted(c):
    c.start_visit()
    d = c.evidence_pdf("demo-equipment")
    eid = d["evc"]["evidence"][0]["id"]
    d = c.evc1.delete(f"{c.url('EVC')}/evidence/{eid}").json()
    assert d["evc"]["evidence"] == []
    with SessionLocal() as db:
        ev = db.get(CommitteeEvidence, uuid.UUID(eid))
        assert ev is not None and ev.removed_at is not None
    c.complete_checklist()
    c.save_report()
    c.submit_report()
    assert c.evc1.post(f"{c.url('EVC')}/evidence/link", {"url": "https://x.example.org/a", "description": "late"}).status_code == 409


# ---- the report ---------------------------------------------------------------------------------------------


def test_a_report_cannot_be_submitted_until_the_configured_requirements_are_met(c):
    c.start_visit()
    d = c.evc1.get(c.url("EVC")).json()
    r = c.evc1.post(f"{c.url('EVC')}/report/submit", {"idempotency_key": "early-key-001", "row_version": d["evc"]["draft"]["row_version"]})
    assert r.status_code == 409 and r.json()["code"] == "report_incomplete"
    codes = {i["code"] for i in r.json()["issues"]}
    assert codes == {"observation_missing", "section_missing"}
    c.observe("demo-premises")
    c.observe("demo-equipment", status="OBSERVED", note="Listed equipment seen.")  # needs evidence (or an "evidence unavailable" observation)
    c.save_report()
    r = c.evc1.post(f"{c.url('EVC')}/report/submit", {"idempotency_key": "early-key-002", "row_version": c.case("EVC", c.evc1)["evc"]["draft"]["row_version"]})
    assert {i["code"] for i in r.json()["issues"]} == {"evidence_missing"}
    c.observe("demo-equipment", status="EVIDENCE_UNAVAILABLE", note="The equipment room was locked.")
    assert c.submit_report()["state"] == "REPORT_SUBMITTED"


def test_a_submitted_report_is_preserved_with_a_snapshot_of_what_it_rests_on(c):
    c.start_visit()
    c.complete_checklist()
    c.save_report(limitations="No access to the library.")
    d = c.submit_report()
    v = d["evc"]["versions"][0]
    assert v["version_number"] == 1 and v["status"] == "SUBMITTED" and v["content_hash"] and v["submitted_by"] == "Test evc_member"
    snap = v["source_snapshot"]
    assert snap["documents"][0]["item_key"] == "doc-main" and snap["documents"][0]["version_number"] == 1 and snap["documents"][0]["sha256"]
    assert {o["item_key"] for o in snap["observations"]} == {"demo-premises", "demo-equipment"} and snap["evidence"][0]["sha256"]
    assert snap["policy"]["classification"] == "DEMO" and snap["rule_version"]["classification"] == "DEMO" and snap["visit"]["mode"] == "ONLINE" and snap["not_an_approval"] is True
    assert v["content"]["sections"]["limitations"] == "No access to the library."
    assert d["evc"]["draft"] is None
    assert d["schedule"]["history"][0]["status"] == "COMPLETED"  # the visit took place


def test_submitting_a_report_does_not_approve_reject_or_complete_anything(c):
    c.evc_to_report()
    d = c.case("EVC")
    assert d["state"] == "REPORT_SUBMITTED" and d["completed_at"] is None and "not an approval" in d["not_an_approval"]
    assert c.w.refresh()["status"] == "SUBMITTED"
    assert c.row("SHC") is None
    with SessionLocal() as db:
        from app.models_committee import DecisionHandoff

        assert db.scalar(select(DecisionHandoff.id)) is None  # nothing is handed on until the coordinator completes the stage
    for forbidden in ("approve", "reject", "decision"):
        assert not any(forbidden in a for a in d["allowed_actions"])


def test_a_repeated_submission_creates_no_second_record(c):
    c.start_visit()
    c.complete_checklist()
    c.save_report()
    d = c.case("EVC", c.evc1)
    body = {"idempotency_key": "submit-once-001", "row_version": d["evc"]["draft"]["row_version"]}
    first = c.evc1.post(f"{c.url('EVC')}/report/submit", body)
    again = c.evc1.post(f"{c.url('EVC')}/report/submit", body)
    assert first.status_code == 200 and again.status_code == 200 and again.headers.get("X-Idempotent-Replay") == "true"
    assert len(reports()) == 1 and reports()[0].status == "SUBMITTED"
    # a different key after submission is simply not allowed: the report is already submitted
    third = c.evc1.post(f"{c.url('EVC')}/report/submit", {"idempotency_key": "submit-twice-02", "row_version": 3})
    assert third.status_code == 409 and len(reports()) == 1


def test_a_submitted_report_cannot_be_edited_even_by_the_author(c):
    c.evc_to_report()
    assert c.evc1.put(f"{c.url('EVC')}/report", {"sections": {"summary": "Rewritten."}, "row_version": 2}).status_code == 409
    assert c.evc1.put(f"{c.url('EVC')}/observations/demo-premises", {"status": "OBSERVED", "note": "Changed after submission.", "row_version": 1}).status_code == 409
    with SessionLocal() as db:  # and the ORM guard refuses it even if a code path tried
        r = db.scalar(select(CommitteeReport))
        r.content = {"sections": {"summary": "Tampered."}}
        with pytest.raises(ImmutableRecordError):
            db.flush()
        db.rollback()


def test_only_an_authorised_coordinator_can_open_an_amendment_and_the_original_version_stays(c):
    c.evc_to_report()
    original = c.case("EVC")["evc"]["versions"][0]
    body = {"reason": "A date in the summary was wrong.", "row_version": c.rv("EVC")}
    assert c.evc1.post(f"{c.url('EVC')}/report/amend", body).status_code == 403  # the author cannot amend their own submission
    assert c.evc_coord.post(f"{c.url('EVC')}/report/amend", {"reason": "x", "row_version": c.rv("EVC")}).status_code == 422
    d = c.evc_coord.post(f"{c.url('EVC')}/report/amend", body).json()
    assert d["state"] == "VISIT_IN_PROGRESS" and d["evc"]["draft"]["version_number"] == 2 and d["evc"]["draft"]["amendment_reason"].startswith("A date") and d["evc"]["draft"]["supersedes_id"] == original["id"]
    c.save_report(summary="The visit took place as scheduled (corrected date).")
    d = c.submit_report()
    versions = d["evc"]["versions"]
    assert [v["version_number"] for v in versions] == [1, 2] and versions[0]["content"] == original["content"] and versions[0]["content_hash"] == original["content_hash"]
    assert versions[1]["content"]["sections"]["summary"].endswith("(corrected date).") and versions[1]["supersedes_id"] == versions[0]["id"]
    with SessionLocal() as db:
        assert db.scalar(select(AuditEvent.id).where(AuditEvent.action == "committee.amendment_opened")) is not None


def test_a_new_document_version_arriving_during_the_visit_blocks_a_report_that_relies_on_the_old_one(c):
    c.start_visit()
    c.complete_checklist()
    doc = doc_rows(c.w.app["id"])[0]
    cur = next(o for o in c.case("EVC", c.evc1)["evc"]["observations"] if o["item_key"] == "demo-premises")
    c.evc1.put(f"{c.url('EVC')}/observations/demo-premises", {"status": "OBSERVED", "note": "Seen.", "row_version": cur["row_version"], "document_refs": [{"document_id": str(doc.id), "page": 1}]})
    c.save_report()
    with SessionLocal() as db:  # the institution's correction path adds version 2 of the document
        owner = db.scalar(select(User).where(User.email == "a@test.example.org"))
        up = UploadFile(file=io.BytesIO(digital_pdf(["Replacement affiliation letter."])), filename="main-v2.pdf", headers=Headers({"content-type": "application/pdf"}))
        docs_svc.upload_version(db, owner, uuid.UUID(c.w.app["id"]), "doc-main", up, None, get_settings(), storage(), correction=True)
    assert [d.version_number for d in doc_rows(c.w.app["id"])] == [1, 2]
    d = c.case("EVC", c.evc1)
    r = c.evc1.post(f"{c.url('EVC')}/report/submit", {"idempotency_key": "stale-doc-key-1", "row_version": d["evc"]["draft"]["row_version"]})
    assert r.status_code == 409 and r.json()["code"] == "report_incomplete" and {i["code"] for i in r.json()["issues"]} == {"evidence_superseded"}
    # reviewing the current version and citing it unblocks the report; the snapshot then names version 2
    new = doc_rows(c.w.app["id"])[1]
    c.evc1.put(f"{c.url('EVC')}/observations/demo-premises", {"status": "OBSERVED", "note": "Seen in the replacement letter.", "row_version": cur["row_version"] + 1, "document_refs": [{"document_id": str(new.id), "page": 1}]})
    d = c.submit_report()
    assert d["evc"]["versions"][0]["source_snapshot"]["documents"][0]["version_number"] == 2


def test_if_report_submission_fails_halfway_nothing_is_left_submitted(c, monkeypatch):
    c.start_visit()
    c.complete_checklist()
    c.save_report()
    d = c.case("EVC", c.evc1)

    def boom(*a, **k):
        raise RuntimeError("the database went away")

    monkeypatch.setattr(ctexts, "report_submitted", boom)
    with pytest.raises(RuntimeError):
        c.evc1.post(f"{c.url('EVC')}/report/submit", {"idempotency_key": "half-way-key-1", "row_version": d["evc"]["draft"]["row_version"]})
    monkeypatch.undo()
    after = c.case("EVC", c.evc1)
    assert after["state"] == "VISIT_IN_PROGRESS" and after["evc"]["draft"]["status"] == "DRAFT" and after["evc"]["versions"] == []
    with SessionLocal() as db:
        assert db.scalar(select(CommitteeSchedule.status)) == "CONFIRMED"  # the visit is not marked as having taken place
    assert c.submit_report(key="half-way-key-1")["state"] == "REPORT_SUBMITTED"  # and the same request can simply be retried


def test_a_member_cannot_submit_a_report_for_a_case_they_are_not_assigned_to(c):
    c.start_visit()
    c.complete_checklist()
    c.save_report()
    r = c.evc2.post(f"{c.url('EVC')}/report/submit", {"idempotency_key": "intruder-key-1", "row_version": 1})
    assert r.status_code == 404
    assert [x.status for x in reports()] == ["DRAFT"]


# ---- completing the stage -------------------------------------------------------------------------------------


def test_only_the_coordinator_completes_the_stage_and_only_after_a_report_exists(c):
    assert c.evc_coord.post(f"{c.url('EVC')}/complete", {"summary": "Too early to complete.", "row_version": c.rv("EVC")}).json()["code"] == "invalid_transition"
    c.evc_to_report()
    assert c.evc1.post(f"{c.url('EVC')}/complete", {"summary": "A member tries.", "row_version": c.rv("EVC")}).status_code == 403
    assert c.evc_coord.post(f"{c.url('EVC')}/complete", {"summary": "x", "row_version": c.rv("EVC")}).status_code == 422
    d = c.complete_evc()
    comp = d["completion"]
    assert d["state"] == "COMPLETED" and comp["approved"] is False and comp["report_version"] == 1 and comp["observations"] == 2 and comp["shortcomings_flagged"] == 0
    assert d["state_label"] == "EVC stage completed (not an approval)"


def test_the_institution_sees_the_schedule_and_only_after_completion_the_report_without_internal_notes(c):
    inst = lambda: c.w.a.get(f"/api/applications/{c.w.app['id']}/committee").json()["evc"]  # noqa: E731
    assert inst()["label"] == "Visit scheduled" and inst()["schedule"]["status"] == "CONFIRMED" and inst()["report"] is None
    c.start_visit()
    assert inst()["label"] == "Visit under way"
    c.observe("demo-premises", note="Matches the application.", internal_note="Internal: the principal seemed unprepared.")
    c.evidence_pdf("demo-equipment")
    c.observe("demo-equipment", note="Equipment seen.")
    c.save_report()
    c.submit_report()
    s = inst()
    assert s["label"] == "Visit finished; the report is being finalised" and s["report"] is None  # not released yet
    c.complete_evc()
    s = inst()
    assert s["label"] == "Expert visit stage complete" and s["report"]["version_number"] == 1 and s["report"]["demo"] is True
    text = str(s)
    assert "Matches the application." in text and "unprepared" not in text and "Internal:" not in text and "internal_note" not in text
    assert "photo-list.pdf" not in text and "sha256" not in text  # evidence files are the committee's, not the institution's
    # another institution cannot read it at all
    assert c.w.b.get(f"/api/applications/{c.w.app['id']}/committee").status_code == 404


def test_every_visit_step_is_in_the_audit_trail_and_the_case_history(c):
    c.evc_to_report()
    c.complete_evc()
    with SessionLocal() as db:
        actions = {a for (a,) in db.execute(select(AuditEvent.action).where(AuditEvent.action.like("committee.%")))}
    assert {"committee.stage_activated", "committee.selection_requested", "committee.selection_recorded", "committee.schedule_proposed", "committee.schedule_confirmed", "committee.visit_started",
            "committee.observation_recorded", "committee.evidence_added", "committee.report_draft_saved", "committee.report_submitted", "committee.stage_completed", "committee.route_resolved",
            "committee.decision_handoff_ready"} <= actions
    ev = c.case("EVC")["events"]
    assert [e["action"] for e in ev][0] == "stage_activated" and "report_submitted" in [e["action"] for e in ev]
    assert all(e["actor"] for e in ev)
    with SessionLocal() as db:
        rows = db.scalars(select(EvcObservation)).all()
        assert all(o.recorded_by_id for o in rows)


def test_notifications_are_not_duplicated_when_an_event_is_processed_again(c):
    with SessionLocal() as db:
        before = len(db.scalars(select(Notification)).all())
        case = c.row("EVC")
        app = db.get(Application, case.application_id)
        assert ctexts.stage_activated(db, case, app) == 0
        assert ctexts.stage_activated(db, case, app) == 0
        db.commit()
        assert len(db.scalars(select(Notification)).all()) == before
