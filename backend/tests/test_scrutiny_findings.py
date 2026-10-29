"""Reviewer workspace: evidence access, structured findings, internal vs institution-facing text, concurrency, completion gates
(acceptance 5-10, 25-31)."""

import json

import pytest
from sqlalchemy import select, text

from app.db import SessionLocal, engine
from app.models import AuditEvent
from app.models_processing import PreliminaryFinding
from app.models_scrutiny import DeficiencyNotice, ScrutinyFinding
from tests.docs_helpers import digital_pdf
from tests.evidence_world import drain, make_world

world = pytest.fixture(make_world)

INTERNAL = "INTERNALMARKER-reviewer-only wording that must never reach the institution"
NOTES = "NOTESMARKER-private working notes"


def ready(w, upload=True):
    if upload:
        w.upload()
        drain()
    w.submit()
    drain()
    w.start()
    return w


def doc_of(w, item="doc-main") -> dict:
    return next(d for d in w.a.get(f"{w.url}/documents").json() if d["item_key"] == item)


# ---- 5. the reviewer sees the evidence -----------------------------------------------------------------------------


def test_the_reviewer_sees_original_evidence_extraction_and_automated_findings_for_their_case(world):
    world.upload(data=digital_pdf([["Name of the Institution: Somewhere Else College", "Date: 12/03/2024"]]))
    drain()
    world.submit()
    drain()
    ev = world.scrutiny.get(f"{world.case_url}/evidence").json()
    assert ev["viewer"] == "reviewer" and ev["institution_name"] == "Test Institute"
    item = next(i for i in ev["items"] if i["item_key"] == "doc-main")
    assert item["current"]["version_number"] == 1 and item["fields"] and item["pages"][0]["page_number"] == 1
    assert next(f for f in item["fields"] if f["field_key"] == "document_date")["page_number"] == 1
    assert {f["issue_type"] for f in item["findings"]} >= {"cross_document_inconsistency"} and all(f["status"] == "PRELIMINARY_ISSUE" for f in item["findings"])
    d = doc_of(world)
    assert world.scrutiny.get(f"/api/documents/{d['id']}/download").content.startswith(b"%PDF")
    assert world.scrutiny.get(f"/api/documents/{d['id']}/pages/1/preview").headers["content-type"] == "image/png"


def test_case_detail_lists_the_checklist_with_the_current_document_version_and_form_answers(world):
    ready(world)
    c = world.case()
    by = {i["item_key"]: i for i in c["checklist"]}
    assert by["doc-main"]["document_version"] == 1 and by["doc-main"]["required"] is True and by["doc-extra"]["document_version"] is None
    assert by["contact"]["answer"] == "R" and by["kind"]["answer"] == "A"


# ---- 6. findings link to precise evidence --------------------------------------------------------------------------


def test_a_finding_links_to_the_exact_document_version_and_page(world):
    ready(world)
    d = doc_of(world)
    f = world.finding(document_id=d["id"], page_number=1)["findings"][-1]
    assert f["document_id"] == d["id"] and f["document_version"] == 1 and f["page_number"] == 1 and f["item_key"] == "doc-main" and f["item_label"] == "Main document"
    assert f["created_by"] == "Test scrutiny_officer" and f["round_number"] == 1 and f["history"][0]["action"] == "recorded"


def test_invalid_evidence_links_are_refused(world):
    ready(world)
    d = doc_of(world)
    assert world.finding(document_id=d["id"], page_number=9, expect=422)["issues"][0]["path"] == "page_number"
    assert world.finding(page_number=1, expect=422)["issues"][0]["path"] == "page_number"
    assert world.finding(document_id=d["id"], item_key="doc-extra", expect=422)["issues"][0]["path"] == "item_key"
    assert world.finding(item_key="not-an-item", expect=422)["issues"][0]["path"] == "item_key"
    assert world.finding(document_id="00000000-0000-0000-0000-000000000000", expect=422)
    assert world.finding(description="x", expect=422)["issues"][0]["path"] == "description"
    assert world.finding(issue_type="invented", expect=422)


def test_findings_in_one_case_cannot_cite_another_applications_documents(world):
    from tests.test_scrutiny_queue import second_application

    ready(world)
    second = second_application(world)
    other_doc = world.a.get(f"/api/applications/{second['id']}/documents").json()[0]["id"]
    assert world.finding(document_id=other_doc, expect=422)["issues"][0]["path"] == "document_id"


def test_source_references_come_from_the_frozen_rule_version_and_are_never_typed_in(world):
    ready(world)
    opts = world.case()["source_options"]
    assert any(o["key"] == "item:doc-main" and o["clause_ref"] == "cl. 9" and o["verification_status"] == "DEMO_ONLY" for o in opts)
    f = world.finding(source_ref_key="item:doc-main")["findings"][-1]
    assert f["source_ref_status"] == "ESTABLISHED" and f["source_ref"]["clause_ref"] == "cl. 9" and f["source_ref"]["verification_status"] == "DEMO_ONLY"
    bare = world.finding()["findings"][-1]
    assert bare["source_ref_status"] == "NEEDS_VERIFICATION" and bare["source_ref"] is None  # flagged for verification, not invented
    assert world.finding(source_ref_key="Clause 99.9 of the Handbook", expect=422)["issues"][0]["path"] == "source_ref_key"


# ---- 7. OCR uncertainty is not a deficiency ---------------------------------------------------------------------


def test_automated_findings_never_become_reviewer_findings_or_notices_without_a_person(world):
    world.upload(data=digital_pdf([["Name of the Institution: Somewhere Else College"]]))
    drain()
    world.submit()
    drain()
    with SessionLocal() as db:
        assert db.scalars(select(PreliminaryFinding)).all()  # the automated checks did flag something
        assert db.scalars(select(ScrutinyFinding)).all() == [] and db.scalars(select(DeficiencyNotice)).all() == []
    world.start()
    drain()
    with SessionLocal() as db:
        assert db.scalars(select(ScrutinyFinding)).all() == [] and db.scalars(select(DeficiencyNotice)).all() == []
    pf = next(f for f in world.scrutiny.get(f"{world.case_url}/evidence").json()["items"][0]["findings"] if f["issue_type"] == "cross_document_inconsistency")
    # even once a reviewer records a finding from it, it is a *finding*, not a notice
    out = world.finding(preliminary_finding_id=pf["id"])["findings"][-1]
    assert out["preliminary_finding_id"] == pf["id"] and out["state"] == "OPEN" and out["visibility"] == "INTERNAL_ONLY"
    with SessionLocal() as db:
        assert db.scalars(select(DeficiencyNotice)).all() == []
        assert {f.status for f in db.scalars(select(PreliminaryFinding))} <= {"PRELIMINARY_ISSUE", "SUPERSEDED"}  # still preliminary


# ---- 8/9. audit and internal-only text -------------------------------------------------------------------------


def test_every_finding_action_is_audited_with_actor_object_and_reason(world):
    ready(world)
    f = world.finding(description="Audit trail check for this finding")["findings"][-1]
    r = world.scrutiny.post(f"{world.case_url}/findings/{f['id']}/disposition", {"state": "NOT_SUPPORTED", "reason": "On inspection the letter does show it.", "row_version": f["row_version"]})
    assert r.status_code == 200
    with SessionLocal() as db:
        rows = list(db.scalars(select(AuditEvent).where(AuditEvent.action.like("scrutiny.finding_%")).order_by(AuditEvent.occurred_at)))
    assert [e.action for e in rows] == ["scrutiny.finding_recorded", "scrutiny.finding_not_supported"]
    last = rows[-1]
    assert last.actor_label == "scrutiny@test.example.org" and last.target_type == "scrutiny_finding" and last.target_id == f["id"] and last.occurred_at
    assert last.detail["from"] == "OPEN" and last.detail["to"] == "NOT_SUPPORTED" and "does show it" in last.detail["reason"]
    hist = world.case()["findings"][0]["history"]
    assert [h["action"] for h in hist] == ["recorded", "not_supported"] and hist[1]["reason"]


def test_internal_text_never_reaches_the_institution_through_any_endpoint(world):
    ready(world)
    world.scrutiny.put(f"{world.case_url}/notes", {"notes": NOTES, "row_version": world.case()["rounds"][0]["row_version"]})
    world.finding(description=INTERNAL, visibility="INTERNAL_ONLY")
    shared = world.eligible_finding()
    n = world.submit_notice(world.make_notice([shared["id"]]))
    world.issue(n["id"])
    world.respond(n["id"])
    notice_id = n["id"]
    responses = [
        world.a.get(f"/api/notices/{notice_id}"), world.a.get("/api/notices"), world.a.get(f"{world.url}/scrutiny"), world.a.get(world.url), world.a.get(f"{world.url}/evidence"),
        world.a.get("/api/notifications"), world.a.get(f"{world.url}/documents"),
    ]
    blob = " ".join(json.dumps(r.json()) for r in responses)
    assert all(r.status_code == 200 for r in responses)
    assert "INTERNALMARKER" not in blob and "NOTESMARKER" not in blob
    assert "Inspected the notice" not in blob  # the issuer's recorded reason is internal too
    for key in ("review_notes", "issue_reason", "returned_reason", "withdrawn_reason", "made_by", "created_by", "prepared_by", "finding_id", "state_reason", "visibility"):
        assert key not in json.dumps(world.a.get(f"/api/notices/{notice_id}").json()), key  # no internal field names exist in the institution's model
    # and the staff view does carry it, so the test would notice if the markers simply never existed
    staff = json.dumps(world.case())
    assert "INTERNALMARKER" in staff and "NOTESMARKER" in staff


def test_an_internal_only_finding_cannot_be_put_in_a_notice_and_wording_is_required_for_eligibility(world):
    ready(world)
    internal = world.finding(description=INTERNAL)["findings"][-1]
    r = world.scrutiny.post(f"{world.case_url}/notices", {"finding_ids": [internal["id"]], "summary": "Trying"})
    assert r.status_code == 422 and r.json()["issues"][0]["code"] == "internal_only"
    assert world.finding(visibility="ELIGIBLE_FOR_NOTICE", expect=422)["issues"][0]["path"] == "institution_text"
    promoted = world.scrutiny.patch(f"{world.case_url}/findings/{internal['id']}", {"row_version": internal["row_version"], "visibility": "ELIGIBLE_FOR_NOTICE"})
    assert promoted.status_code == 422  # still no institution-facing wording
    ok = world.scrutiny.patch(f"{world.case_url}/findings/{internal['id']}", {"row_version": internal["row_version"], "visibility": "ELIGIBLE_FOR_NOTICE",
                                                                              "institution_text": "Please clarify the validity period.", "requested_correction": "Provide a letter showing the validity period."})
    assert ok.status_code == 200


# ---- 10. concurrent updates --------------------------------------------------------------------------------------


def test_concurrent_edits_cannot_silently_overwrite_each_other(world):
    ready(world)
    f = world.finding(description="Original wording of the finding")["findings"][-1]
    v = f["row_version"]
    first = world.scrutiny.patch(f"{world.case_url}/findings/{f['id']}", {"row_version": v, "description": "First reviewer's updated wording"})
    stale = world.scrutiny.patch(f"{world.case_url}/findings/{f['id']}", {"row_version": v, "description": "Second reviewer's overwriting wording"})
    assert first.status_code == 200 and stale.status_code == 409 and stale.json()["code"] == "stale_version"
    assert world.case()["findings"][0]["description"] == "First reviewer's updated wording"
    # the same protection on a conclusion, on the notes, and on case-level actions
    assert world.scrutiny.post(f"{world.case_url}/findings/{f['id']}/disposition", {"state": "RESOLVED", "reason": "Concluding from a stale view", "row_version": v}).status_code == 409
    rv = world.case()["rounds"][0]["row_version"]
    assert world.scrutiny.put(f"{world.case_url}/notes", {"notes": "one", "row_version": rv}).status_code == 200
    assert world.scrutiny.put(f"{world.case_url}/notes", {"notes": "two", "row_version": rv}).status_code == 409
    assert world.scrutiny.post(f"{world.case_url}/complete", {"row_version": 1, "summary": "Completing from a stale case view"}).status_code in (409,)


def test_two_starts_with_the_same_view_only_one_wins():
    w = make_world()
    w.upload()
    drain()
    w.submit()
    v = w.case_row().row_version
    a = w.scrutiny.post(f"{w.case_url}/start", {"row_version": v})
    b = w.scrutiny.post(f"{w.case_url}/start", {"row_version": v})
    assert (a.status_code, b.status_code) == (200, 409)


# ---- findings lifecycle ------------------------------------------------------------------------------------------


def test_concluding_a_finding_needs_a_reason_and_concluded_findings_are_final(world):
    ready(world)
    f = world.finding()["findings"][-1]
    url = f"{world.case_url}/findings/{f['id']}/disposition"
    assert world.scrutiny.post(url, {"state": "RESOLVED", "reason": "ok", "row_version": f["row_version"]}).status_code == 422
    assert world.scrutiny.post(url, {"state": "UNRESOLVED", "reason": "Not yet in a notice, so cannot be unresolved", "row_version": f["row_version"]}).status_code == 409
    done = world.scrutiny.post(url, {"state": "RESOLVED", "reason": "Confirmed from the original document.", "row_version": f["row_version"]}).json()["findings"][0]
    assert done["state"] == "RESOLVED" and done["state_reason"]
    assert world.scrutiny.post(url, {"state": "NOT_SUPPORTED", "reason": "Changing a concluded finding", "row_version": done["row_version"]}).status_code == 409
    assert world.scrutiny.patch(f"{world.case_url}/findings/{f['id']}", {"row_version": done["row_version"], "description": "Editing after conclusion"}).status_code == 409


def test_reviewer_actions_need_the_case_to_be_under_scrutiny():
    w = make_world()
    w.upload()
    drain()
    w.submit()  # assigned but not started
    assert w.finding(expect=409)["code"] == "invalid_transition"
    assert w.scrutiny.post(f"{w.case_url}/complete", {"row_version": w.case_row().row_version, "summary": "Completing before starting"}).status_code == 409
    assert w.scrutiny.post(f"{w.case_url}/start", {"row_version": w.case_row().row_version}).status_code == 200
    assert w.scrutiny.post(f"{w.case_url}/start", {"row_version": w.case_row().row_version}).status_code == 409


# ---- 28. completion gates ----------------------------------------------------------------------------------------


def test_scrutiny_cannot_be_completed_while_findings_are_open(world):
    ready(world)
    world.finding()
    r = world.scrutiny.post(f"{world.case_url}/complete", {"row_version": world.case_row().row_version, "summary": "Trying to finish with an open finding"})
    assert r.status_code == 409 and r.json()["code"] == "findings_pending" and world.case_row().state == "UNDER_SCRUTINY"


def test_scrutiny_cannot_be_completed_with_a_notice_draft_open(world):
    ready(world)
    f = world.eligible_finding()
    world.make_notice([f["id"]])
    world.scrutiny.post(f"{world.case_url}/findings/{f['id']}/disposition", {"state": "RESOLVED", "reason": "Concluding while a draft exists", "row_version": f["row_version"]})
    r = world.scrutiny.post(f"{world.case_url}/complete", {"row_version": world.case_row().row_version, "summary": "Trying to finish with a draft notice"})
    assert r.status_code == 409 and r.json()["code"] in ("notice_draft_open", "findings_pending")


def test_scrutiny_cannot_be_completed_while_documents_are_still_being_processed(world):
    world.upload()  # not drained: the worker has not run
    world.submit()
    world.start()
    r = world.scrutiny.post(f"{world.case_url}/complete", {"row_version": world.case_row().row_version, "summary": "Finishing before extraction ran"})
    assert r.status_code == 409 and r.json()["code"] == "documents_still_processing"
    drain()
    assert world.scrutiny.post(f"{world.case_url}/complete", {"row_version": world.case_row().row_version, "summary": "Finishing after extraction ran"}).status_code == 200


def test_completing_requires_a_recorded_summary_and_never_approves(world):
    ready(world)
    assert world.scrutiny.post(f"{world.case_url}/complete", {"row_version": world.case_row().row_version, "summary": "x"}).status_code == 422
    c = world.scrutiny.post(f"{world.case_url}/complete", {"row_version": world.case_row().row_version, "summary": "Reviewed; no deficiencies found."}).json()
    assert c["queue"]["state"] == "SCRUTINY_COMPLETED" and c["completion"]["outcome"] == "COMPLETED_NO_OPEN_FINDINGS" and c["completion"]["approved"] is False
    assert c["rounds"][0]["status"] == "COMPLETED" and c["rounds"][0]["outcome_reason"].startswith("Reviewed")
    assert world.a.get(world.url).json()["status"] == "SUBMITTED"  # the application record is untouched
    assert "approv" not in world.a.get(f"{world.url}/scrutiny").json()["state_label"].lower().replace("not an approval", "")
    # nothing more can be done on the case
    assert world.finding(expect=409)["code"] == "invalid_transition"
    assert world.scrutiny.post(f"{world.case_url}/start", {"row_version": world.case_row().row_version}).status_code == 409


def test_a_corrupted_frozen_route_at_completion_goes_to_the_exception_queue_instead_of_a_guessed_stage(world):
    ready(world)
    with engine.begin() as conn:  # simulate a damaged configuration snapshot (raw SQL: the ORM guard rightly forbids this)
        conn.execute(text("UPDATE applications SET submission_snapshot = :s WHERE id = :i"), {"s": json.dumps({**_snapshot(world), "configuration": {**_snapshot(world)["configuration"], "route": []}}), "i": world.app["id"].replace("-", "") if engine.dialect.name == "sqlite" else world.app["id"]})
    c = world.scrutiny.post(f"{world.case_url}/complete", {"row_version": world.case_row().row_version, "summary": "Completing against a broken route"})
    assert c.status_code == 200, c.text
    d = c.json()
    assert d["queue"]["state"] == "CONFIGURATION_EXCEPTION" and d["handoff"]["status"] == "EXCEPTION" and d["handoff"]["to_stage_key"] is None
    assert d["completion"]["summary"] and "no scrutiny stage" in d["handoff"]["reason"].lower()


def _snapshot(w) -> dict:
    with engine.connect() as conn:
        raw = conn.execute(text("SELECT submission_snapshot FROM applications WHERE id = :i"), {"i": w.app["id"].replace("-", "") if engine.dialect.name == "sqlite" else w.app["id"]}).scalar()
    return json.loads(raw) if isinstance(raw, str) else raw
