"""The whole path, end to end through the real API: submit -> queue -> assign -> scrutiny -> notice -> issue -> response ->
re-scrutiny -> completion -> hand-off. Each step checks the state it must leave behind."""

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.models import AuditEvent
from app.models_scrutiny import DeficiencyNotice, Notification, ScrutinyEvent
from tests.evidence_world import drain, make_world

world = pytest.fixture(make_world)


def submitted_world(w, assign=True):
    w.upload()
    drain()
    w.submit(assign=assign)
    drain()
    return w


def test_a_submitted_application_enters_the_scrutiny_queue_awaiting_assignment(world):
    w = submitted_world(world, assign=False)
    row = w.case_row()
    assert row.state == "AWAITING_ASSIGNMENT"
    q = w.coordinator.get("/api/scrutiny/cases").json()
    assert q["scope"] == "coordinator" and q["total"] == 1 and q["counts"]["AWAITING_ASSIGNMENT"] == 1
    item = q["items"][0]
    assert item["reference"] == w.app["reference"] and item["institution_name"] == "Test Institute" and item["category_name"]
    assert item["academic_year"] == "2026-27" and item["state_label"] == "Awaiting assignment" and item["assignees"] == []
    assert item["rule_version"]["classification"] == "DEMO" and "NOT VERIFIED" in item["rule_version"]["demo_banner"]
    assert item["processing"]["documents"] == 1 and item["due_at"] is None
    assert any("Assign a reviewer" in a for a in item["outstanding_actions"])


def test_the_whole_workflow_from_submission_to_handoff(world):
    w = submitted_world(world)
    assert w.case()["queue"]["state"] == "READY_FOR_SCRUTINY"
    assert w.case()["queue"]["assignees"] == ["Test scrutiny_officer"]

    w.start()
    f = w.eligible_finding()
    assert f["state"] == "OPEN" and f["document_version"] is None and f["visibility"] == "ELIGIBLE_FOR_NOTICE"
    notice = w.make_notice([f["id"]])
    assert notice["status"] == "DRAFT" and notice["sequence"] == 1 and notice["items"][0]["finding_id"] == f["id"]
    # a draft is not an official notice: the institution cannot see it
    assert w.a.get(f"/api/notices/{notice['id']}").status_code == 404
    assert w.a.get("/api/notices").json() == []
    notice = w.submit_notice(notice)
    assert notice["status"] == "IN_REVIEW" and w.case()["queue"]["state"] == "AWAITING_NOTICE_DECISION"
    assert w.a.get(f"/api/notices/{notice['id']}").status_code == 404  # still not issued

    w.issue(notice["id"])
    issued = next(n for n in w.case()["notices"] if n["id"] == notice["id"])
    assert issued["status"] == "ISSUED" and issued["issued_by"] == "Test notice_issuer" and issued["response_due_at"] is None
    assert issued["policy_resolution"]["state"] == "NO_DURATION" and issued["template"]["demo"] is True
    assert w.case()["queue"]["state"] == "AWAITING_RESPONSE"

    seen = w.a.get(f"/api/notices/{notice['id']}").json()
    assert seen["status"] == "ISSUED" and seen["can_respond"] is True and seen["response_due_at"] is None and "No response deadline" in seen["due_note"]
    assert seen["items"][0]["requested_correction"].startswith("Upload a letter") and "DEMO ONLY" in seen["demo_notice"]

    after = w.respond(notice["id"])
    assert after["status"] == "RESPONSE_RECEIVED" and after["can_respond"] is False
    assert after["items"][0]["response_state"] == "SUBMITTED" and after["items"][0]["response_documents"][0]["version_number"] == 2
    case = w.case()
    assert case["queue"]["state"] == "AWAITING_RESCRUTINY" and [r["kind"] for r in case["rounds"]] == ["INITIAL", "RE_SCRUTINY"]
    # the response did not close anything
    assert next(x for x in case["findings"] if x["id"] == f["id"])["state"] == "NOTICED"
    drain()

    w.start()
    r = w.scrutiny.post(f"{w.case_url}/findings/{f['id']}/disposition", {"state": "RESOLVED", "reason": "The corrected letter shows the validity period.", "row_version": w.case()["findings"][0]["row_version"]})
    assert r.status_code == 200, r.text
    case = r.json()
    assert case["notices"][0]["status"] == "CLOSED" and case["notices"][0]["items"][0]["response_state"] == "REVIEWED"
    done = w.scrutiny.post(f"{w.case_url}/complete", {"row_version": w.case_row().row_version, "summary": "All findings concluded after re-scrutiny."})
    assert done.status_code == 200, done.text
    c = done.json()
    assert c["queue"]["state"] == "SCRUTINY_COMPLETED" and c["completion"]["approved"] is False and c["completion"]["counts"]["RESOLVED"] == 1
    assert c["handoff"]["status"] == "AWAITING_DETERMINATION" and c["handoff"]["module_available"] is False  # the demo route's next stage (EVC) is conditional
    assert "not an approval" in c["not_an_approval"]

    with SessionLocal() as db:
        assert db.scalar(select(DeficiencyNotice).where(DeficiencyNotice.status == "CLOSED")) is not None
        kinds = [e.action for e in db.scalars(select(ScrutinyEvent).order_by(ScrutinyEvent.at))]
        assert kinds[0] == "case_opened" and "notice_issued" in kinds and "response_submitted" in kinds and kinds[-1] == "handoff_recorded"
        assert db.scalar(select(AuditEvent).where(AuditEvent.action == "scrutiny.notice_issued")) is not None
        assert {n.event_type for n in db.scalars(select(Notification))} >= {"CASE_ENTERED_QUEUE", "CASE_ASSIGNED", "NOTICE_AWAITING_ISSUANCE", "NOTICE_ISSUED", "RESPONSE_SUBMITTED", "READY_FOR_RESCRUTINY", "SCRUTINY_COMPLETED", "HANDED_OFF"}
    assert w.a.get(w.url).json()["status"] == "SUBMITTED"  # the application itself is untouched by all of this
