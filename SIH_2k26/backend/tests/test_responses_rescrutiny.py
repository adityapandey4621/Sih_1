"""Institution responses, corrected evidence, re-scrutiny and repeated notices (acceptance 17-27, 34)."""

import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, text

from app.db import SessionLocal, engine
from app.guards import ImmutableRecordError
from app.models import AuditEvent
from app.models_applications import Application, ApplicationDocument
from app.models_scrutiny import DeficiencyNotice, NoticeResponse, ScrutinyEvent, ScrutinyFinding, ScrutinyRound
from app.services import scrutiny_texts as texts
from tests.catalogue_helpers import MAIN_TEXT
from tests.docs_helpers import corrupt_pdf, digital_pdf
from tests.evidence_world import drain, make_world

world = pytest.fixture(make_world)


def issued(w, **finding_kw):
    w.upload()
    drain()
    w.submit()
    drain()
    w.start()
    n = w.submit_notice(w.make_notice([w.eligible_finding(**finding_kw)["id"]]))
    w.issue(n["id"])
    return n["id"]


def notice_of(w, nid) -> dict:
    return w.a.get(f"/api/notices/{nid}").json()


def upload_correction(w, nid, item_id, data=None, name="corrected.pdf", client=None, expect=(200, 201)):
    r = (client or w.a).upload(f"/api/notices/{nid}/items/{item_id}/documents", "file", name, data if data is not None else digital_pdf([MAIN_TEXT, ["Valid until: 31/03/2030"]]))
    assert r.status_code in expect, r.text
    return r


def doc_versions(w, item="doc-main"):
    with SessionLocal() as db:
        return list(db.scalars(select(ApplicationDocument).where(ApplicationDocument.application_id == uuid.UUID(w.app["id"]), ApplicationDocument.item_key == item).order_by(ApplicationDocument.version_number)))


def app_row(w) -> Application:
    with SessionLocal() as db:
        return db.get(Application, uuid.UUID(w.app["id"]))


# ---- 17/18. what the institution sees ---------------------------------------------------------------------------


def test_the_institution_sees_the_issued_notice_with_only_its_allowed_contents(world):
    nid = issued(world)
    d = notice_of(world, nid)
    assert d["status"] == "ISSUED" and d["reference"].startswith("DN-") and d["sequence"] == 1 and d["application_reference"] == world.app["reference"]
    assert d["items"][0]["requested_correction"] and d["items"][0]["item_label"] == "Main document" and d["items"][0]["accepts_files"] is True
    assert d["items"][0]["source_note"] == "No source reference has been established for this item." and d["items"][0]["response_state"] == "NOT_RESPONDED"
    assert "not a rejection" in d["not_an_approval"] and d["template_label"].startswith("DEMO ONLY")
    s = world.a.get(f"{world.url}/scrutiny").json()
    assert s["state_label"].startswith("Deficiency notice issued") and [n["reference"] for n in s["notices"]] == [d["reference"]]
    assert [e["label"] for e in s["events"]] == ["Application received for scrutiny", f"Deficiency notice {d['reference']} issued"]
    n = world.a.get("/api/notifications").json()["items"][0]
    assert d["reference"] in n["title"] and n["link"] == f"/app/notices/{nid}" and n["external_delivery"] == "NOT_CONFIGURED" and n["delivery_state"] == "STORED_IN_APP"


def test_another_institution_cannot_read_or_answer_a_notice_and_the_attempts_are_audited(world):
    nid = issued(world)
    item = notice_of(world, nid)["items"][0]["id"]
    b = world.b
    assert b.get(f"/api/notices/{nid}").status_code == 404
    assert b.get("/api/notices").json() == []
    assert b.put(f"/api/notices/{nid}/response", {"explanation": "Not mine", "items": []}).status_code == 404
    assert b.upload(f"/api/notices/{nid}/items/{item}/documents", "file", "x.pdf", digital_pdf([["x"]])).status_code == 404
    assert b.post(f"/api/notices/{nid}/response/submit", {"idempotency_key": "intruder-key-1", "row_version": 1}).status_code == 404
    assert b.get(f"{world.url}/scrutiny").status_code == 404
    for c in (world.scrutiny, world.issuer, world.coordinator, world.auditor):
        assert c.get(f"/api/notices/{nid}").status_code == 403  # staff use the staff endpoints, not the institution's
    with SessionLocal() as db:
        assert len(db.scalars(select(AuditEvent).where(AuditEvent.action == "notice.access_denied")).all()) >= 4
        assert db.scalars(select(NoticeResponse)).all() == []


# ---- 19. open notices only --------------------------------------------------------------------------------------


def test_a_response_can_only_be_made_to_an_issued_open_notice(world):
    world.upload()
    drain()
    world.submit()
    world.start()
    draft = world.make_notice()
    assert world.a.put(f"/api/notices/{draft['id']}/response", {"explanation": "x", "items": []}).status_code == 404  # a draft does not exist for the institution
    n = world.submit_notice(draft)
    assert world.a.post(f"/api/notices/{n['id']}/response/submit", {"idempotency_key": "too-early-key", "row_version": 1}).status_code == 404
    world.issue(n["id"])
    world.respond(n["id"])
    item = notice_of(world, n["id"])["items"][0]["id"]
    late = world.a.put(f"/api/notices/{n['id']}/response", {"explanation": "A later change", "items": [], "row_version": 2})
    assert late.status_code == 409 and late.json()["code"] == "notice_not_open"
    assert upload_correction(world, n["id"], item, expect=(409,)).json()["code"] == "notice_not_open"
    other = world.a.post(f"/api/notices/{n['id']}/response/submit", {"idempotency_key": "another-key-xx", "row_version": 2})
    assert other.status_code == 409 and other.json()["code"] == "response_already_submitted"


def test_the_normal_upload_endpoint_still_refuses_after_submission_and_the_orm_guard_agrees(world):
    nid = issued(world)
    assert world.a.upload(f"{world.url}/documents", "doc-main", "x.pdf", digital_pdf([["x"]])).status_code == 409
    assert world.a.delete(f"{world.url}/documents/{doc_versions(world)[0].id}").status_code == 409
    with SessionLocal() as db:
        db.add(ApplicationDocument(application_id=uuid.UUID(world.app["id"]), item_key="doc-extra", original_filename="x.pdf", storage_key="k" * 32, media_type="application/pdf", size_bytes=1,
                                   sha256="0" * 64, uploaded_by_id=app_row(world).created_by_id, slot_id=doc_versions(world)[0].slot_id))
        with pytest.raises(ImmutableRecordError):
            db.flush()
    _ = nid


# ---- 20/21/22. corrected evidence is a new version; nothing is lost -------------------------------------------------


def test_corrected_evidence_becomes_a_new_version_and_the_original_is_kept_untouched(world):
    nid = issued(world)
    before_app = world.a.get(world.url).json()
    snap_before = json.dumps(app_row(world).submission_snapshot, sort_keys=True)
    v1 = doc_versions(world)[0]
    item = notice_of(world, nid)["items"][0]
    up = upload_correction(world, nid, item["id"])
    assert up.status_code == 201
    versions = doc_versions(world)
    assert [v.version_number for v in versions] == [1, 2] and versions[0].superseded_at is not None and versions[1].superseded_at is None and versions[1].previous_version_id == v1.id
    assert versions[0].sha256 == v1.sha256 and versions[0].storage_key == v1.storage_key  # the original file and its record are intact
    after_app = app_row(world)
    assert json.dumps(after_app.submission_snapshot, sort_keys=True) == snap_before
    assert after_app.row_version == before_app["row_version"] and after_app.status == "SUBMITTED"
    # the owner and the assigned reviewer can still read the superseded original
    assert world.a.get(f"/api/documents/{v1.id}/download").content.startswith(b"%PDF")
    assert world.scrutiny.get(f"/api/documents/{v1.id}/pages/1/preview").status_code == 200
    d = notice_of(world, nid)["items"][0]
    assert d["response_documents"][0]["version_number"] == 2 and d["response_documents"][0]["current"] is True and d["document_current_version"] == 2
    with SessionLocal() as db:
        assert any(e.detail and e.detail.get("correction") for e in db.scalars(select(AuditEvent).where(AuditEvent.action == "application.document_uploaded")))


def test_corrected_evidence_goes_through_the_same_validation_and_processing_as_any_upload(world):
    nid = issued(world)
    item = notice_of(world, nid)["items"][0]["id"]
    assert upload_correction(world, nid, item, data=b"MZ\x90\x00 not a document", name="evil.pdf", expect=(415,)).json()["code"] in ("type_not_allowed", "type_mismatch")
    assert upload_correction(world, nid, item, data=digital_pdf([["x"]]), name="doc.exe", expect=(415,))
    assert len(doc_versions(world)) == 1  # refused files left nothing behind
    upload_correction(world, nid, item, data=digital_pdf([MAIN_TEXT, ["The affiliation is valid until 31 March 2030 as stated."]]))
    drain()
    v2 = doc_versions(world)[1]
    ex = world.scrutiny.get(f"/api/documents/{v2.id}/extraction").json()
    assert ex["version_number"] == 2 and ex["state"]["code"] in ("extraction_complete", "review_recommended")
    f = {x["field_key"]: x for x in ex["fields"]}
    assert f["institution_name"]["value"] == "Test Institute" and f["institution_name"]["version_number"] == 2 and f["institution_name"]["page_number"] == 1
    assert [p["page_number"] for p in ex["pages"]] == [1, 2] and "31 March 2030" in ex["pages"][1]["text"]  # the second page of the corrected file is read too


def test_uploading_the_identical_file_adds_no_version_but_the_response_still_relies_on_it(world):
    nid = issued(world)
    item = notice_of(world, nid)["items"][0]["id"]
    same = digital_pdf([MAIN_TEXT])  # byte-identical to what world.upload() stored? use the stored bytes to be sure
    from tests.evidence_world import storage

    with storage().open(doc_versions(world)[0].storage_key) as fh:
        same = fh.read()
    r = upload_correction(world, nid, item, data=same)
    assert r.status_code == 200 and r.headers["X-Upload-Result"] == "duplicate_of_current"
    assert len(doc_versions(world)) == 1
    assert notice_of(world, nid)["items"][0]["response_documents"][0]["version_number"] == 1


def test_a_notice_item_that_is_not_a_document_requirement_takes_no_upload(world):
    nid = issued(world, item_key="contact")
    item = notice_of(world, nid)["items"][0]
    assert item["accepts_files"] is False
    r = upload_correction(world, nid, item["id"], expect=(422,))
    assert "nothing to upload" in r.json()["detail"]
    assert len(doc_versions(world)) == 1


def test_responses_are_tied_to_the_correct_items_including_a_first_file_for_a_missing_requirement(world):
    world.upload()
    drain()
    world.submit()
    world.start()
    f1 = world.eligible_finding()
    f2 = world.eligible_finding(item_key="doc-extra", issue_type="missing_document", institution_text="The extra document was not supplied.", requested_correction="Upload the extra document.")
    n = world.submit_notice(world.make_notice([f1["id"], f2["id"]]))
    world.issue(n["id"])
    items = notice_of(world, n["id"])["items"]
    assert [i["item_key"] for i in items] == ["doc-main", "doc-extra"] and items[1]["document_version"] is None
    upload_correction(world, n["id"], items[1]["id"], data=digital_pdf([["Institute Name: Test Institute"]]))
    current = notice_of(world, n["id"])["response"]["row_version"]  # the upload started the draft response
    saved = world.a.put(f"/api/notices/{n['id']}/response", {"explanation": "Overall note", "items": [{"notice_item_id": items[1]["id"], "explanation": "Here is the extra document."}], "row_version": current}).json()
    by = {i["item_key"]: i for i in saved["items"]}
    assert by["doc-extra"]["response_state"] == "DRAFT" and by["doc-extra"]["response_documents"][0]["version_number"] == 1 and by["doc-main"]["response_state"] == "NOT_RESPONDED"
    assert [v.version_number for v in doc_versions(world, "doc-extra")] == [1] and len(doc_versions(world)) == 1  # the other requirement's slot was not touched
    done = world.a.post(f"/api/notices/{n['id']}/response/submit", {"idempotency_key": "two-items-key", "row_version": saved["response"]["row_version"]}).json()
    by = {i["item_key"]: i for i in done["items"]}
    assert by["doc-extra"]["response_state"] == "SUBMITTED" and by["doc-main"]["response_state"] == "NOT_RESPONDED"  # an unanswered item stays visibly unanswered
    case = world.case()
    assert {i["item_key"]: i["response_state"] for i in case["notices"][0]["items"]} == {"doc-main": "NOT_RESPONDED", "doc-extra": "SUBMITTED"}


def test_draft_responses_use_compare_and_set_and_validate_their_items(world):
    nid = issued(world)
    item = notice_of(world, nid)["items"][0]["id"]
    first = world.a.put(f"/api/notices/{nid}/response", {"explanation": "One", "items": [{"notice_item_id": item, "explanation": "A"}]}).json()
    v = first["response"]["row_version"]
    assert world.a.put(f"/api/notices/{nid}/response", {"explanation": "Two", "items": [], "row_version": v}).status_code == 200
    assert world.a.put(f"/api/notices/{nid}/response", {"explanation": "Stale", "items": [], "row_version": v}).status_code == 409
    assert world.a.put(f"/api/notices/{nid}/response", {"explanation": "No version at all", "items": []}).status_code == 409
    cur = notice_of(world, nid)["response"]["row_version"]
    bad = world.a.put(f"/api/notices/{nid}/response", {"items": [{"notice_item_id": "00000000-0000-0000-0000-000000000000", "explanation": "x"}], "row_version": cur})
    assert bad.status_code == 422 and bad.json()["issues"][0]["code"] == "unknown_item"
    assert notice_of(world, nid)["response"]["explanation"] == "Two"


# ---- 23/24. submission -------------------------------------------------------------------------------------------


def test_submission_needs_content_and_is_idempotent(world):
    nid = issued(world)
    url = f"/api/notices/{nid}/response/submit"
    assert world.a.post(url, {"idempotency_key": "empty-submit-1", "row_version": 1}).json()["code"] == "empty_response"
    item = notice_of(world, nid)["items"][0]["id"]
    saved = world.a.put(f"/api/notices/{nid}/response", {"explanation": "Only general words", "items": []}).json()
    assert world.a.post(url, {"idempotency_key": "empty-submit-2", "row_version": saved["response"]["row_version"]}).json()["code"] == "empty_response"  # nothing answers an item yet
    saved = world.a.put(f"/api/notices/{nid}/response", {"items": [{"notice_item_id": item, "explanation": "Here is our answer."}], "row_version": saved["response"]["row_version"]}).json()
    v = saved["response"]["row_version"]
    assert world.a.post(url, {"idempotency_key": "stale-submit-1", "row_version": v - 1}).status_code == 409
    ok = world.a.post(url, {"idempotency_key": "real-submit-001", "row_version": v})
    again = world.a.post(url, {"idempotency_key": "real-submit-001", "row_version": v})
    assert ok.status_code == 200 and again.status_code == 200 and again.headers["X-Idempotent-Replay"] == "true"
    assert world.a.post(url, {"idempotency_key": "different-key-1", "row_version": v}).status_code == 409
    with SessionLocal() as db:
        assert len(db.scalars(select(ScrutinyRound)).all()) == 2  # exactly one re-scrutiny round, however often it was submitted
        assert len(db.scalars(select(ScrutinyEvent).where(ScrutinyEvent.action == "response_submitted")).all()) == 1


def test_submission_moves_the_case_to_rescrutiny_notifies_reviewers_and_closes_nothing(world):
    nid = issued(world)
    world.respond(nid)
    c = world.case()
    assert c["queue"]["state"] == "AWAITING_RESCRUTINY" and c["notices"][0]["status"] == "RESPONSE_RECEIVED"
    assert [(r["number"], r["kind"], r["status"]) for r in c["rounds"]] == [(1, "INITIAL", "NOTICE_ISSUED"), (2, "RE_SCRUTINY", "OPEN")] and c["rounds"][1]["triggered_by_notice"] == c["notices"][0]["reference"]
    assert c["rounds"][0]["outcome"] == "NOTICE_ISSUED"  # the first round was not overwritten
    assert [f["state"] for f in c["findings"]] == ["NOTICED"] and c["notices"][0]["items"][0]["response_state"] == "SUBMITTED"
    assert any("re-scrutiny" in a.lower() for a in c["queue"]["outstanding_actions"])
    # nothing can be finished from here: the reviewer must start re-scrutiny and evaluate
    assert world.scrutiny.post(f"{world.case_url}/complete", {"row_version": world.case_row().row_version, "summary": "Trying to finish without looking"}).status_code == 409
    assert world.finding(expect=409)["code"] == "invalid_transition"
    n = world.scrutiny.get("/api/notifications").json()["items"]
    assert any(i["event_type"] == "READY_FOR_RESCRUTINY" for i in n)
    coord = world.coordinator.get("/api/notifications").json()["items"]
    assert any(i["event_type"] == "RESPONSE_SUBMITTED" for i in coord)
    q = world.coordinator.get("/api/scrutiny/cases?state=AWAITING_RESCRUTINY").json()
    assert q["total"] == 1 and q["counts"]["AWAITING_RESCRUTINY"] == 1
    s = world.a.get(f"{world.url}/scrutiny").json()
    assert s["state_label"].startswith("Response received") and "not been accepted" in s["explanation"]


def test_a_late_response_is_recorded_as_late_but_not_refused():
    w = make_world(response_days=7)
    nid = issued(w)
    past = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    with engine.begin() as conn:  # backdate the due date (raw SQL: the ORM guard forbids editing an issued notice)
        conn.execute(text("UPDATE deficiency_notices SET response_due_at = :d"), {"d": past})
    w.respond(nid)
    row = w.case()["notices"][0]
    assert row["response"]["after_due_date"] is True and row["status"] == "RESPONSE_RECEIVED"
    with SessionLocal() as db:
        assert db.scalar(select(ScrutinyEvent).where(ScrutinyEvent.action == "response_submitted")).detail["after_due_date"] is True


def test_if_persistence_fails_the_response_is_not_accepted(world, monkeypatch):
    nid = issued(world)
    d = notice_of(world, nid)
    saved = world.a.put(f"/api/notices/{nid}/response", {"items": [{"notice_item_id": d["items"][0]["id"], "explanation": "Our answer"}]}).json()

    def boom(*a, **k):
        raise RuntimeError("database went away")

    monkeypatch.setattr(texts, "ready_for_rescrutiny", boom)
    with pytest.raises(RuntimeError):
        world.a.post(f"/api/notices/{nid}/response/submit", {"idempotency_key": "fails-midway-1", "row_version": saved["response"]["row_version"]})
    monkeypatch.undo()
    with SessionLocal() as db:
        assert db.scalar(select(NoticeResponse.status)) == "DRAFT" and db.scalar(select(DeficiencyNotice.status)) == "ISSUED"
        assert len(db.scalars(select(ScrutinyRound)).all()) == 1 and db.scalar(select(ScrutinyEvent).where(ScrutinyEvent.action == "response_submitted")) is None
    assert world.case_row().state == "AWAITING_RESPONSE" and notice_of(world, nid)["can_respond"] is True
    ok = world.a.post(f"/api/notices/{nid}/response/submit", {"idempotency_key": "fails-midway-1", "row_version": saved["response"]["row_version"]})
    assert ok.status_code == 200 and world.case_row().state == "AWAITING_RESCRUTINY"


# ---- 8. OCR trouble after a response ----------------------------------------------------------------------------------


def test_unreadable_corrected_evidence_is_shown_as_such_and_never_becomes_a_notice_or_finding(world):
    nid = issued(world)
    item = notice_of(world, nid)["items"][0]["id"]
    upload_correction(world, nid, item, data=corrupt_pdf())
    world.respond(nid, upload=False)
    drain()
    c = world.case()
    r = c["notices"][0]["items"][0]["response_documents"][0]
    assert r["version_number"] == 2 and "could not be read" in r["state_label"].lower()
    assert c["queue"]["processing"]["failed"] == 1 and any("could not be processed" in a for a in c["queue"]["outstanding_actions"])
    with SessionLocal() as db:
        assert len(db.scalars(select(DeficiencyNotice)).all()) == 1  # no second notice appeared by itself
        assert len(db.scalars(select(ScrutinyFinding)).all()) == 1
    assert c["queue"]["state"] == "AWAITING_RESCRUTINY"


# ---- 7/9. a newer version appears while someone is looking at an older one --------------------------------------


def test_findings_and_notice_items_keep_pointing_at_the_version_they_concern(world):
    world.upload()
    drain()
    world.submit()
    world.start()
    d1 = doc_versions(world)[0]
    f = world.eligible_finding(document_id=str(d1.id), page_number=1)
    n = world.submit_notice(world.make_notice([f["id"]]))
    world.issue(n["id"])
    ev_before = world.scrutiny.get(f"{world.case_url}/evidence").json()["items"][0]
    assert ev_before["current"]["version_number"] == 1
    upload_correction(world, n["id"], notice_of(world, n["id"])["items"][0]["id"])  # the institution replaces it while the reviewer is looking
    ev_after = world.scrutiny.get(f"{world.case_url}/evidence").json()["items"][0]
    assert ev_after["current"]["version_number"] == 2 and [v["version_number"] for v in ev_after["versions"]] == [1, 2]
    c = world.case()
    fd = c["findings"][0]
    assert fd["document_id"] == str(d1.id) and fd["document_version"] == 1 and fd["document_superseded"] is True and fd["document_current_version"] == 2
    ni = c["notices"][0]["items"][0]
    assert ni["document_id"] == str(d1.id) and ni["document_version"] == 1 and ni["document_current_version"] == 2
    seen = notice_of(world, n["id"])["items"][0]
    assert seen["document_version"] == 1 and seen["document_current_version"] == 2 and seen["page_number"] == 1
    assert world.scrutiny.get(f"/api/documents/{d1.id}/extraction").json()["version_number"] == 1  # the old version's own extraction is intact


# ---- 25/26/27. re-scrutiny ----------------------------------------------------------------------------------------


def start_rescrutiny(w, nid):
    w.respond(nid)
    drain()
    w.start()


def conclude(w, finding_id, state, reason="Concluded after examining the corrected evidence."):
    f = next(x for x in w.case()["findings"] if x["id"] == finding_id)
    r = w.scrutiny.post(f"{w.case_url}/findings/{finding_id}/disposition", {"state": state, "reason": reason, "row_version": f["row_version"]})
    assert r.status_code == 200, r.text
    return r.json()


def test_rescrutiny_can_record_resolved_unresolved_and_not_supported_each_with_a_reason(world):
    world.upload()
    drain()
    world.submit()
    world.start()
    ids = [world.eligible_finding()["id"] for _ in range(3)]
    n = world.submit_notice(world.make_notice(ids))
    world.issue(n["id"])
    start_rescrutiny(world, n["id"])
    assert world.case()["queue"]["state"] == "UNDER_RESCRUTINY"
    f = next(x for x in world.case()["findings"] if x["id"] == ids[0])
    assert world.scrutiny.post(f"{world.case_url}/findings/{ids[0]}/disposition", {"state": "RESOLVED", "reason": "ok", "row_version": f["row_version"]}).status_code == 422
    conclude(world, ids[0], "RESOLVED")
    conclude(world, ids[1], "UNRESOLVED", "The new letter still does not state the validity period.")
    c = conclude(world, ids[2], "NOT_SUPPORTED", "The original letter did contain it on page 2.")
    assert {f["id"]: f["state"] for f in c["findings"]} == {ids[0]: "RESOLVED", ids[1]: "UNRESOLVED", ids[2]: "NOT_SUPPORTED"}
    assert c["notices"][0]["status"] == "CLOSED"  # every finding it carried has now been concluded
    states = {i["finding_id"]: i["response_state"] for i in c["notices"][0]["items"]}
    assert states[ids[0]] == "REVIEWED" and states[ids[1]] == states[ids[2]] == "NOT_RESPONDED"  # the institution answered only the first item; the others stay visibly unanswered
    hist = next(f for f in c["findings"] if f["id"] == ids[1])["history"]
    assert [(h["action"], h["round_number"]) for h in hist] == [("recorded", 1), ("noticed", 1), ("unresolved", 2)]


def test_completing_with_unresolved_findings_needs_an_explicit_acknowledgement_and_carries_them_forward(world):
    world.upload()
    drain()
    world.submit()
    world.start()
    f = world.eligible_finding()
    n = world.submit_notice(world.make_notice([f["id"]]))
    world.issue(n["id"])
    start_rescrutiny(world, n["id"])
    conclude(world, f["id"], "UNRESOLVED", "Still not shown in the corrected letter.")
    body = {"row_version": world.case_row().row_version, "summary": "Re-scrutiny complete; one deficiency remains."}
    r = world.scrutiny.post(f"{world.case_url}/complete", body)
    assert r.status_code == 422 and r.json()["code"] == "unresolved_findings_need_acknowledgement"
    c = world.scrutiny.post(f"{world.case_url}/complete", {**body, "acknowledge_unresolved": True}).json()
    assert c["completion"]["outcome"] == "COMPLETED_WITH_UNRESOLVED_FINDINGS" and c["completion"]["counts"]["UNRESOLVED"] == 1
    assert c["completion"]["findings"][0]["state"] == "UNRESOLVED" and c["completion"]["findings"][0]["reason"] and c["completion"]["approved"] is False
    assert c["completion"]["documents"][0]["version"] == 2  # the evidence this conclusion rests on is recorded
    assert c["queue"]["state"] == "SCRUTINY_COMPLETED"


def test_another_notice_can_follow_as_a_separate_linked_record_with_the_first_preserved(world):
    world.upload()
    drain()
    world.submit()
    world.start()
    f = world.eligible_finding()
    n1 = world.submit_notice(world.make_notice([f["id"]]))
    world.issue(n1["id"])
    start_rescrutiny(world, n1["id"])
    conclude(world, f["id"], "UNRESOLVED", "Still not shown.")
    new = world.finding(visibility="ELIGIBLE_FOR_NOTICE", institution_text="The corrected letter lacks a signature.", requested_correction="Upload a signed copy.", item_key="doc-main")["findings"][-1]
    assert new["round_number"] == 2
    n2 = world.scrutiny.post(f"{world.case_url}/notices", {"finding_ids": [f["id"], new["id"]], "summary": "Two points remain."}).json()["notices"][-1]
    assert n2["sequence"] == 2 and n2["previous_reference"] == world.case()["notices"][0]["reference"] and n2["round_number"] == 2
    n2 = world.submit_notice(n2)
    world.issue(n2["id"], key="second-notice-key")
    c = world.case()
    first, second = c["notices"]
    assert first["status"] == "CLOSED" and second["status"] == "ISSUED" and first["items"][0]["response_documents"][0]["version_number"] == 2  # the earlier notice and its response are intact
    assert c["queue"]["state"] == "AWAITING_RESPONSE" and {x["state"] for x in c["findings"]} == {"NOTICED"}
    seen = world.a.get("/api/notices").json()
    assert [s["sequence"] for s in seen] == [2, 1] or [s["sequence"] for s in seen] == [1, 2]
    assert notice_of(world, n2["id"])["previous_reference"] == first["reference"]


def test_no_maximum_number_of_notice_rounds_is_invented(world):
    world.upload()
    drain()
    world.submit()
    world.start()
    f = world.eligible_finding()
    nid = world.submit_notice(world.make_notice([f["id"]]))["id"]
    world.issue(nid, key="round-key-000001")
    for rnd in range(2, 6):
        start_rescrutiny_again(world, nid, rnd)
        conclude(world, f["id"], "UNRESOLVED", f"Round {rnd}: still not shown.")
        draft = world.scrutiny.post(f"{world.case_url}/notices", {"finding_ids": [f["id"]], "summary": f"Round {rnd}"}).json()["notices"][-1]
        nid = world.submit_notice(draft)["id"]
        world.issue(nid, key=f"round-key-{rnd:06d}")
    c = world.case()
    assert [n["sequence"] for n in c["notices"]] == [1, 2, 3, 4, 5] and c["queue"]["state"] == "AWAITING_RESPONSE"


def start_rescrutiny_again(w, nid, rnd):
    d = w.a.get(f"/api/notices/{nid}").json()
    item = d["items"][0]
    r = w.a.upload(f"/api/notices/{nid}/items/{item['id']}/documents", "file", f"c{rnd}.pdf", digital_pdf([MAIN_TEXT, [f"Revision {rnd}"]]))
    assert r.status_code in (200, 201), r.text
    d = w.a.get(f"/api/notices/{nid}").json()
    saved = w.a.put(f"/api/notices/{nid}/response", {"items": [{"notice_item_id": item["id"], "explanation": f"Answer {rnd}"}], "row_version": d["response"]["row_version"] if d["response"] else None}).json()
    assert w.a.post(f"/api/notices/{nid}/response/submit", {"idempotency_key": f"resp-key-{rnd:06d}", "row_version": saved["response"]["row_version"]}).status_code == 200
    drain()
    w.start()


def test_rescrutiny_is_not_an_automatic_pass_even_if_the_new_file_is_perfectly_clean(world):
    nid = issued(world)
    world.respond(nid)
    drain()
    c = world.case()
    assert c["findings"][0]["state"] == "NOTICED" and c["queue"]["state"] == "AWAITING_RESCRUTINY"
    world.start()
    # clean extraction, no preliminary issues: still nothing concluded on the reviewer's behalf
    c = world.case()
    assert c["findings"][0]["state"] == "NOTICED" and c["queue"]["state"] == "UNDER_RESCRUTINY"
    assert world.scrutiny.post(f"{world.case_url}/complete", {"row_version": world.case_row().row_version, "summary": "Trying to finish without concluding"}).json()["code"] == "findings_pending"


def test_a_further_notice_is_refused_when_there_is_nothing_eligible_to_put_in_it(world):
    nid = issued(world)
    start_rescrutiny(world, nid)
    f = world.case()["findings"][0]
    r = world.scrutiny.post(f"{world.case_url}/notices", {"finding_ids": [f["id"]], "summary": "Re-noticing something already noticed"})
    assert r.status_code == 422 and r.json()["issues"][0]["code"] == "finding_state"
