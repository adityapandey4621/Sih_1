"""Evidence API: who may see what, how state is described, and the distinctions that must never blur."""

import json
import re

import pytest
from sqlalchemy import select

from app.config import Settings
from app.db import SessionLocal
from app.main import app as fastapi_app
from app.models import AuditEvent
from app.processing.scanner import ScanResult
from app.domain import ScanStatus
from tests.conftest import Api, make_user
from app.rbac import Role
from tests.docs_helpers import FakeOcr, corrupt_pdf, digital_pdf, ocr_page, scanned_pdf
from tests.evidence_world import doc_rows, drain, make_world, step

world = pytest.fixture(make_world)


class Infected:
    name = "fake-infected"

    def scan(self, data):
        return ScanResult(ScanStatus.INFECTED, "Test-Signature")


class Clean:
    name = "fake-clean"

    def scan(self, data):
        return ScanResult(ScanStatus.CLEAN, "fake")


def doc_id(w) -> str:
    return str(doc_rows(w.app["id"])[0].id)


def submitted(w):
    w.upload()
    drain()
    w.submit()
    drain()
    return doc_id(w)


# ---- who may see a document -----------------------------------------------------------------------------------------


DOC_ENDPOINTS = ["", "/download", "/pages/1/preview", "/extraction", "/history"]


def test_everything_requires_authentication(world):
    world.upload()
    d = doc_id(world)
    anon = Api()
    for suffix in DOC_ENDPOINTS:
        assert anon.get(f"/api/documents/{d}{suffix}").status_code == 401, suffix
    assert anon.post(f"/api/documents/{d}/retry").status_code == 401
    assert anon.get(f"{world.url}/evidence").status_code == 401
    assert anon.get(f"{world.url}/documents").status_code == 401
    assert anon.get("/api/scrutiny/cases").status_code == 401


def test_another_institution_cannot_see_any_part_of_a_document_and_it_is_audited(world):
    world.upload()
    drain()
    d = doc_id(world)
    for suffix in DOC_ENDPOINTS:
        assert world.b.get(f"/api/documents/{d}{suffix}").status_code == 404, suffix
    assert world.b.post(f"/api/documents/{d}/retry").status_code == 404
    assert world.b.get(f"{world.url}/evidence").status_code == 404
    assert world.b.get(f"{world.url}/documents").status_code == 404
    with SessionLocal() as db:
        denied = [e for e in db.scalars(select(AuditEvent).where(AuditEvent.action.in_(("document.access_denied", "application.access_denied"))))]
    assert len(denied) >= 7


def test_unrelated_roles_cannot_read_documents(world):
    world.upload()
    d = doc_id(world)
    assert world.auditor.get(f"/api/documents/{d}").status_code == 404
    make_user(Role.PLATFORM_ADMIN, "admin@test.example.org")
    admin = Api()
    admin.login("admin@test.example.org")
    assert admin.get(f"/api/documents/{d}/download").status_code == 404
    assert world.auditor.get("/api/scrutiny/cases").status_code == 403 and admin.get("/api/scrutiny/cases").status_code == 403


def test_reviewers_cannot_see_drafts_but_can_read_their_assigned_submitted_applications_read_only(world):
    world.upload()
    drain()
    d = doc_id(world)
    assert world.scrutiny.get(f"/api/documents/{d}").status_code == 404  # a draft is the institution's work in progress
    assert world.scrutiny.get("/api/scrutiny/cases").json()["items"] == []
    world.submit()
    drain()
    listing = world.scrutiny.get("/api/scrutiny/cases").json()["items"]
    assert [x["reference"] for x in listing] == [world.app["reference"]] and listing[0]["processing"]["documents"] == 1 and listing[0]["institution_name"] == "Test Institute"
    for suffix in DOC_ENDPOINTS:
        assert world.scrutiny.get(f"/api/documents/{d}{suffix}").status_code == 200, suffix
    # read-only: none of the owner's actions
    assert world.scrutiny.upload(f"{world.url}/documents", "doc-main", "x.pdf", digital_pdf([["x"]])).status_code == 403
    assert world.scrutiny.delete(f"{world.url}/documents/{d}").status_code == 403
    assert world.scrutiny.post(f"{world.url}/submit", {"row_version": 1}).status_code == 403
    assert world.scrutiny.get(f"{world.url}").status_code == 403  # the owner-side application endpoints stay owner-only


def test_downloads_and_review_views_are_audited(world):
    d = submitted(world)
    assert world.a.get(f"/api/documents/{d}/download").status_code == 200
    assert world.scrutiny.get(f"/api/documents/{d}/download").content.startswith(b"%PDF")
    world.scrutiny.get(f"{world.case_url}/evidence")
    with SessionLocal() as db:
        acts = [(e.action, e.actor_label) for e in db.scalars(select(AuditEvent).order_by(AuditEvent.occurred_at)) if e.action in ("document.downloaded", "evidence.viewed")]
    assert ("document.downloaded", "a@test.example.org") in acts and ("document.downloaded", "scrutiny@test.example.org") in acts and ("evidence.viewed", "scrutiny@test.example.org") in acts


# ---- what each viewer sees ------------------------------------------------------------------------------------------


def test_owner_view_hides_internal_priorities_and_scanner_notes_reviewer_view_shows_them(world):
    world.upload(data=digital_pdf([["Name of the Institution: Somewhere Else College"]]))
    drain()
    world.submit()
    drain()
    own = world.evidence()
    rev = world.scrutiny.get(f"{world.case_url}/evidence").json()
    assert own["viewer"] == "owner" and own["institution_name"] is None and rev["viewer"] == "reviewer" and rev["institution_name"] == "Test Institute"
    own_f, rev_f = own["items"][0]["findings"], rev["items"][0]["findings"]
    assert {f["issue_type"] for f in rev_f} >= {"cross_document_inconsistency", "scanner_unavailable"}
    assert "scanner_unavailable" not in {f["issue_type"] for f in own_f}  # an environment note, not for the institution
    assert all(f["priority"] is None and f["check_key"] is None for f in own_f) and all(f["priority"] and f["check_key"] for f in rev_f)
    mismatch = next(f for f in own_f if f["issue_type"] == "cross_document_inconsistency")
    assert mismatch["version_number"] == 1 and mismatch["pages"] == [1] and mismatch["rule_ref"] == "Main document (cl. 9)" and mismatch["item_key"] == "doc-main"
    assert {v["source"] for v in mismatch["values"]} == {"document", "application_record"}
    assert "not a deficiency notice" in mismatch["disclaimer"] and "reviewer will look" in mismatch["plain_text"]


def test_extraction_endpoint_returns_page_text_and_fields_with_provenance(world):
    d = submitted(world)
    ex = world.scrutiny.get(f"/api/documents/{d}/extraction").json()
    assert ex["version_number"] == 1 and ex["state"]["code"] == "extraction_complete"
    assert [(p["page_number"], p["method"], p["status"]) for p in ex["pages"]] == [(1, "PDF_TEXT", "OK")] and "Test Institute" in ex["pages"][0]["text"]
    f = {x["field_key"]: x for x in ex["fields"]}
    assert f["document_date"]["normalised_value"] == "2024-03-12" and f["document_date"]["page_number"] == 1 and f["document_date"]["version_number"] == 1
    assert f["institution_name"]["evidence"] == "Name of the Institution: Test Institute"
    own = world.a.get(f"/api/documents/{d}/extraction").json()
    assert own["fields"] == ex["fields"]


def test_checklist_mapping_distinguishes_no_file_from_a_file_without_extracted_values(world):
    ev = world.evidence()
    main = next(i for i in ev["items"] if i["item_key"] == "doc-main")
    assert main["slot_state"] is None and main["current"] is None and main["versions"] == [] and main["fields"] == []  # no file submitted
    assert main["configured_fields"] == ["Institution name", "Document date", "Reference number"]
    world.upload(data=digital_pdf([["A page with enough text but none of the labels we look for"]]))
    drain()
    main = next(i for i in world.evidence()["items"] if i["item_key"] == "doc-main")
    assert main["slot_state"]["code"] == "review_recommended"  # file present, processed, but expected values missing
    assert {f["status"] for f in main["fields"]} == {"NOT_FOUND"} and all(f["value"] is None for f in main["fields"])
    assert "expected_field_not_found" in {f["issue_type"] for f in main["findings"]}


def test_missing_and_unreadable_and_failed_extraction_are_three_different_findings(world):
    world.upload(item="doc-extra", data=corrupt_pdf(), name="broken.pdf")
    world.upload(data=scanned_pdf([["scan"]]))  # no OCR in this environment
    drain()
    by_item = {i["item_key"]: i for i in world.evidence()["items"]}
    assert {f["issue_type"] for f in by_item["doc-extra"]["findings"]} == {"unsupported_or_corrupt_file"}
    assert "text_extraction_failed" in {f["issue_type"] for f in by_item["doc-main"]["findings"]}
    world.a.delete(f"{world.url}/documents/{by_item['doc-main']['current']['id']}")
    drain()
    d = world.submit() if False else None
    assert d is None  # (a draft with no file is simply "no file": it only becomes a finding once the application is submitted)
    assert not [f for f in world.evidence()["items"][0]["findings"] if f["issue_type"] == "required_file_not_supplied"]


# ---- state vocabulary ------------------------------------------------------------------------------------------------


def test_states_use_plain_labels_and_never_claim_verification(world):
    seen = {}
    def snap(tag):
        seen[tag] = world.refresh()["documents"][-1]["state"]
    world.upload()
    snap("queued")
    from app import jobs
    with SessionLocal() as db:
        jobs.claim_next(db, "w", Settings(environment="test"))
    snap("running")
    from datetime import timedelta
    from app.models import utcnow
    step(now=utcnow() + timedelta(hours=1))  # reap + finish
    drain()
    snap("complete")
    world.upload(data=digital_pdf([["no labels here at all, just text content"]]), name="v2.pdf")
    drain()
    snap("review")
    world.upload(data=corrupt_pdf(), name="v3.pdf")
    drain()
    snap("technical")
    assert seen["queued"]["code"] in ("received", "extraction_queued") and not seen["queued"]["terminal"]
    assert seen["running"]["code"] == "extraction_running" and seen["running"]["label"] == "Technical checks in progress" and not seen["running"]["terminal"]
    assert seen["complete"]["code"] == "extraction_complete" and seen["complete"]["label"] == "Extraction complete" and seen["complete"]["terminal"]
    assert seen["review"]["code"] == "review_recommended" and seen["review"]["tone"] == "warning"
    assert seen["technical"]["code"] == "technical_failed" and seen["technical"]["tone"] == "error"
    for s in seen.values():
        text = (s["code"] + " " + s["label"]).lower()
        assert "verified" not in text and "accepted" not in text and "approved" not in text and "valid" not in text.replace("invalid", "")
        assert s["label"] and s["explanation"] and not re.search(r"[A-Z]{3,}_[A-Z]{3,}", s["label"])  # no raw enum strings as labels


def test_documents_are_never_marked_verified_anywhere_in_any_payload(world):
    d = submitted(world)
    payloads = [world.refresh(), world.evidence(), world.scrutiny.get(f"{world.case_url}/evidence").json(),
                world.a.get(f"/api/documents/{d}").json(), world.a.get(f"/api/documents/{d}/extraction").json(), world.a.get(f"{world.url}/documents").json()]
    blob = json.dumps(payloads)
    assert '"VERIFIED"' not in blob
    # every *document* version, wherever it appears, is NOT_VERIFIED (rule *sources* have their own, unrelated, verification status)
    doc_payloads = [payloads[0]["documents"], payloads[5], [payloads[3]["version"]], payloads[3]["versions"]]
    statuses = {d["verification_status"] for group in doc_payloads for d in group if "verification_status" in d}
    assert statuses == {"NOT_VERIFIED"}


def test_document_processing_and_evidence_endpoints_cannot_issue_notices_or_decide(world):
    """Phase 3's boundary, still true in Phase 4: evidence/processing never issues a notice. Only the explicit, permission-gated
    scrutiny endpoint does, and no processing module can reach the notice tables."""
    from pathlib import Path

    all_paths = list(fastapi_app.openapi()["paths"])
    # `/api/decisions/...` is the Phase 6 decision workspace: its read-only evidence view sits behind decision:queue (test_decision_stage covers it); it records nothing
    evidence = [p.lower() for p in all_paths if (p.startswith("/api/documents") or p.endswith("/evidence") or re.search(r"^/api/applications/\{[a-z_]+\}/documents", p)) and not p.startswith("/api/decisions/")]
    assert evidence and not [p for p in evidence if re.search(r"deficien|notice|approv|reject|decision|verif|accept", p)]
    # the only issuing endpoints are the explicit, permission-gated ones: a deficiency notice (Phase 4) and a recorded decision outcome (Phase 6); neither is reachable from processing
    assert sorted(p for p in all_paths if p.endswith("/issue")) == ["/api/decisions/cases/{case_id}/issue", "/api/scrutiny/notices/{notice_id}/issue"]
    app_dir = Path(__file__).resolve().parents[1] / "app"
    for module in [*(app_dir / "processing").glob("*.py"), app_dir / "worker.py", app_dir / "jobs.py", app_dir / "evidence.py"]:
        src = module.read_text(encoding="utf-8")
        assert "models_scrutiny" not in src and "DeficiencyNotice" not in src, f"{module.name} must not be able to issue or touch notices"
    submitted(world)
    with SessionLocal() as db:
        from app.models_processing import PreliminaryFinding
        assert {f.status for f in db.scalars(select(PreliminaryFinding))} <= {"PRELIMINARY_ISSUE", "SUPERSEDED"}


# ---- previews --------------------------------------------------------------------------------------------------------


def test_previews_are_rasterised_pages_with_safe_headers(world):
    world.upload(data=digital_pdf([["page one"], ["page two"]]))
    d = doc_id(world)
    r = world.a.get(f"/api/documents/{d}/pages/2/preview")
    assert r.status_code == 200 and r.content.startswith(b"\x89PNG") and r.headers["content-type"] == "image/png" and "no-store" in r.headers["cache-control"]
    assert world.a.get(f"/api/documents/{d}/pages/3/preview").status_code == 404
    assert world.a.get(f"/api/documents/{d}/pages/0/preview").status_code == 404


def test_unreadable_files_cannot_be_previewed(world):
    world.upload(data=corrupt_pdf())
    drain()
    r = world.a.get(f"/api/documents/{doc_id(world)}/pages/1/preview")
    assert r.status_code == 409 and r.json()["code"] == "file_unreadable"


# ---- malware scanning -----------------------------------------------------------------------------------------------


def test_an_infected_file_is_quarantined_not_processed_and_not_downloadable(world):
    world.upload()
    step(scanner=Infected())
    step()  # cross-check
    d = doc_id(world)
    doc = world.refresh()["documents"][0]
    assert doc["state"]["code"] == "quarantined" and doc["state"]["scan_status"] == "INFECTED" and doc["state"]["tone"] == "error"
    assert world.a.get(f"/api/documents/{d}/download").json()["code"] == "quarantined"
    assert world.a.get(f"/api/documents/{d}/pages/1/preview").status_code == 409
    assert world.a.post(f"/api/documents/{d}/retry").json()["code"] == "not_retryable"  # quarantine is not a transient fault
    ex = world.a.get(f"/api/documents/{d}/extraction").json()
    assert ex["pages"] == [] and ex["fields"] == []
    assert "malware_detected" in {f["issue_type"] for f in world.evidence()["items"][0]["findings"]}


def test_when_scanning_is_required_but_unavailable_the_file_is_held_and_retry_works_once_a_scanner_exists(world):
    strict = Settings(environment="test", require_malware_scan=True, ocr_engine="none")
    world.upload()
    drain(settings=strict)
    d = doc_id(world)
    st = world.refresh()["documents"][0]["state"]
    assert st["code"] == "held" and st["can_retry"] and "scanning is configured" in st["explanation"] and st["extraction_status"] == "BLOCKED"
    assert world.a.get(f"/api/documents/{d}/extraction").json()["pages"] == []
    assert world.a.post(f"/api/documents/{d}/retry").status_code == 202
    step(settings=strict, scanner=Clean())
    st = world.refresh()["documents"][0]["state"]
    assert st["code"] == "extraction_complete" and st["scan_status"] == "CLEAN" and "no threat reported" in st["scan_note"]


def test_scanner_unavailable_is_stated_plainly_never_as_clean(world):
    submitted(world)
    st = world.refresh()["documents"][0]["state"]
    assert st["scan_status"] == "SCANNER_UNAVAILABLE" and "NOT performed" in st["scan_note"] and "clean" not in st["scan_note"].lower()


# ---- roles and retries -----------------------------------------------------------------------------------------------


def test_scrutiny_can_request_a_retry_on_a_submitted_applications_failed_processing_and_it_is_audited(world):
    world.upload(data=scanned_pdf([["Name of the Institution: Test Institute"]]))
    drain()
    world.submit()
    drain()
    d = doc_id(world)
    assert world.scrutiny.post(f"/api/documents/{d}/retry").status_code == 202
    step(ocr=FakeOcr([ocr_page(("Name of the Institution: Test Institute", 94.0))]))
    assert world.scrutiny.get(f"/api/documents/{d}/extraction").json()["state"]["code"] in ("extraction_complete", "review_recommended")
    with SessionLocal() as db:
        assert db.scalar(select(AuditEvent).where(AuditEvent.action == "document.retry_requested", AuditEvent.actor_label == "scrutiny@test.example.org")) is not None
    hist = [h["action"] for h in world.scrutiny.get(f"/api/documents/{d}/history").json()]
    assert hist[0] == "uploaded" and "retry_requested" in hist and hist[-1] == "processing_finished"


def test_retry_of_a_successful_or_pending_job_is_refused(world):
    world.upload()
    d = doc_id(world)
    assert world.a.post(f"/api/documents/{d}/retry").json()["code"] == "already_pending"
    drain()
    assert world.a.post(f"/api/documents/{d}/retry").json()["code"] == "already_succeeded"


def test_version_list_endpoint_and_document_detail_show_the_chain(world):
    world.upload(data=digital_pdf([["Name of the Institution: One"]]))
    world.upload(data=digital_pdf([["Name of the Institution: Two"]]), name="two.pdf")
    vs = world.a.get(f"{world.url}/documents").json()
    assert [(v["version_number"], v["is_current"], v["original_filename"]) for v in vs] == [(1, False, "main.pdf"), (2, True, "two.pdf")]
    assert vs[1]["previous_version_id"] == vs[0]["id"] and vs[0]["state"]["code"] == "superseded"
    detail = world.a.get(f"/api/documents/{vs[0]['id']}").json()
    assert detail["version"]["version_number"] == 1 and [v["version_number"] for v in detail["versions"]] == [1, 2] and detail["item_label"] == "Main document"
