"""Scrutiny queue, scope, the authorised-assignment boundary, and route-driven hand-off (acceptance 1-4, 28-31)."""

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.domain import Classification
from app.models import AuditEvent, User
from app.models_scrutiny import Notification, ScrutinyAssignment, ScrutinyCase
from app.rbac import Role
from app.schemas_catalogue import RouteStageIn
from tests.conftest import Api, make_user
from tests.docs_helpers import digital_pdf
from tests.catalogue_helpers import MAIN_TEXT
from tests.evidence_world import REVIEWER, REVIEWER2, drain, make_world

world = pytest.fixture(make_world)


def submitted(w, assign=True):
    w.upload()
    drain()
    w.submit(assign=assign)
    drain()
    return w


def second_application(w) -> dict:
    """Another submitted application by the same institution (same category and year)."""
    app = w.a.post("/api/applications", {"category_id": str(w.cat.category_id), "academic_year": "2026-27"}).json()
    url = f"/api/applications/{app['id']}"
    assert w.a.upload(f"{url}/documents", "doc-main", "main.pdf", digital_pdf([MAIN_TEXT])).status_code == 201
    d = w.a.get(url).json()
    d = w.a.patch(url, {"form_data": {"contact": "R", "kind": "A", "decl": True}, "row_version": d["row_version"]}).json()
    out = w.a.post(f"{url}/submit", {"row_version": d["row_version"]}).json()
    assert out["status"] == "SUBMITTED"
    return out


def user_id(email: str) -> str:
    with SessionLocal() as db:
        return str(db.scalar(select(User.id).where(User.email == email)))


# ---- 1. entering the queue ----------------------------------------------------------------------------------------


def test_submission_creates_exactly_one_case_and_resubmission_cannot_create_another(world):
    submitted(world, assign=False)
    assert world.a.post(f"{world.url}/submit", {"row_version": 99}).status_code == 409
    with SessionLocal() as db:
        assert len(db.scalars(select(ScrutinyCase)).all()) == 1


def test_a_draft_is_not_in_any_queue(world):
    world.upload()
    assert world.coordinator.get("/api/scrutiny/cases").json()["total"] == 0


def test_coordinators_are_notified_that_a_case_entered_the_queue(world):
    submitted(world, assign=False)
    with SessionLocal() as db:
        n = db.scalars(select(Notification).where(Notification.event_type == "CASE_ENTERED_QUEUE")).all()
    assert len(n) == 1 and n[0].delivery_state == "STORED_IN_APP" and n[0].external_delivery == "NOT_CONFIGURED"
    got = world.coordinator.get("/api/notifications").json()
    assert got["unread"] == 1 and world.app["reference"] in got["items"][0]["title"]


# ---- 2. who sees which case ---------------------------------------------------------------------------------------


def test_each_role_sees_only_what_its_scope_allows(world):
    submitted(world, assign=False)
    assert world.coordinator.get("/api/scrutiny/cases").json()["total"] == 1
    assert world.scrutiny.get("/api/scrutiny/cases").json()["total"] == 0  # not assigned
    assert world.issuer.get("/api/scrutiny/cases").json()["total"] == 0  # nothing prepared for issuance
    world.assign()
    assert world.scrutiny.get("/api/scrutiny/cases").json()["total"] == 1
    assert world.scrutiny2.get("/api/scrutiny/cases").json()["total"] == 0  # another reviewer is still outside the scope


def test_roles_without_scrutiny_permissions_are_refused_with_403(world):
    submitted(world)
    for role, email in ((Role.EVC_COORDINATOR, "evcc@t.example.org"), (Role.EVC_MEMBER, "evcm@t.example.org"), (Role.SHC_MEMBER, "shc@t.example.org"),
                        (Role.EXECUTIVE_COMMITTEE, "ec@t.example.org"), (Role.SAC_MEMBER, "sac@t.example.org"), (Role.RULES_ADMIN, "ra@t.example.org"),
                        (Role.RULES_APPROVER, "rap@t.example.org"), (Role.PLATFORM_ADMIN, "pa@t.example.org")):
        make_user(role, email)
        c = Api()
        c.login(email)
        assert c.get("/api/scrutiny/cases").status_code == 403, role
        assert c.get(world.case_url).status_code == 403, role
    for c in (world.a, world.auditor):
        assert c.get("/api/scrutiny/cases").status_code == 403
    assert Api().get("/api/scrutiny/cases").status_code == 401


def test_a_reviewer_cannot_see_or_act_on_an_unassigned_case_and_the_attempt_is_audited(world):
    submitted(world)  # assigned to REVIEWER only
    other = world.scrutiny2
    url = world.case_url
    assert other.get(url).status_code == 404
    assert other.get(f"{url}/evidence").status_code == 404
    assert other.post(f"{url}/start", {"row_version": 2}).status_code == 404
    assert other.post(f"{url}/findings", {"issue_type": "other", "description": "Not my case at all"}).status_code == 404
    assert other.put(f"{url}/notes", {"notes": "x", "row_version": 1}).status_code == 404
    assert other.post(f"{url}/complete", {"row_version": 2, "summary": "Trying to finish someone else's case"}).status_code == 404
    doc_id = world.a.get(f"{world.url}/documents").json()[0]["id"]
    for suffix in ("", "/download", "/pages/1/preview", "/extraction", "/history"):
        assert other.get(f"/api/documents/{doc_id}{suffix}").status_code == 404, suffix
    with SessionLocal() as db:
        denied = [e for e in db.scalars(select(AuditEvent).where(AuditEvent.action == "scrutiny.access_denied", AuditEvent.actor_label == REVIEWER2))]
    assert len(denied) >= 5


def test_the_coordinator_manages_the_queue_but_sees_no_document_content_findings_or_notices(world):
    submitted(world)
    world.start()
    world.eligible_finding()
    d = world.coordinator.get(world.case_url).json()
    assert d["scope"] == ["coordinator"] and d["findings"] == [] and d["notices"] == [] and all(r["review_notes"] is None for r in d["rounds"])
    assert world.coordinator.get(f"{world.case_url}/evidence").status_code == 403
    doc_id = world.a.get(f"{world.url}/documents").json()[0]["id"]
    assert world.coordinator.get(f"/api/documents/{doc_id}/download").status_code == 404
    assert world.coordinator.post(f"{world.case_url}/findings", {"issue_type": "other", "description": "A coordinator is not a reviewer"}).status_code == 403


def test_an_auditor_has_no_document_access_but_the_audit_trail_records_scrutiny_actions(world):
    submitted(world)
    world.start()
    doc_id = world.a.get(f"{world.url}/documents").json()[0]["id"]
    assert world.auditor.get(f"/api/documents/{doc_id}").status_code == 404
    assert world.auditor.get(world.case_url).status_code == 403
    audit = world.auditor.get("/api/audit/events?page_size=50")
    assert audit.status_code == 200
    actions = {e["action"] for e in audit.json()["items"]}
    assert {"scrutiny.case_opened", "scrutiny.assigned", "scrutiny.round_started"} <= actions


# ---- 3. the authorised-assignment boundary ----------------------------------------------------------------------


def test_demo_assignment_is_labelled_recorded_and_attributed(world):
    submitted(world, assign=False)
    d = world.case(world.coordinator)
    assert d["selection"]["mode"] == "DEMO_MANUAL" and d["selection"]["available"] and "NOT the official AICTE" in d["selection"]["notice"]
    out = world.assign()
    a = out["assignments"][0]
    assert a["is_demo"] is True and a["source"] == "DEMO_MANUAL" and a["made_by"] == "coordinator@test.example.org"
    assert a["selection_ref"]["classification"] == "DEMO" and a["selection_ref"]["rule_set"] == "s1" and a["selection_ref"]["rule_version"] == 1
    with SessionLocal() as db:
        n = db.scalar(select(Notification).where(Notification.event_type == "CASE_ASSIGNED"))
    assert "DEMONSTRATION" in n.body and str(n.recipient_user_id) == user_id(REVIEWER)


def test_only_the_coordinator_may_assign_and_only_a_scrutiny_officer_may_be_assigned(world):
    submitted(world, assign=False)
    v = world.case_row().row_version
    body = {"user_id": user_id(REVIEWER), "row_version": v}
    for c in (world.scrutiny, world.issuer, world.a, world.auditor):
        assert c.post(f"{world.case_url}/assign", body).status_code in (403, 404)
    assert world.case_row().state == "AWAITING_ASSIGNMENT"
    assert world.coordinator.get("/api/scrutiny/assignable").json()  # the coordinator may list candidates
    assert world.scrutiny.get("/api/scrutiny/assignable").status_code == 403
    r = world.coordinator.post(f"{world.case_url}/assign", {"user_id": user_id("a@test.example.org"), "row_version": v})
    assert r.status_code == 422  # an institution user cannot be put on a scrutiny case
    assert world.coordinator.post(f"{world.case_url}/assign", {"user_id": user_id(REVIEWER), "row_version": v + 5}).status_code == 409


def test_a_case_that_is_not_demo_classified_cannot_be_assigned_here_because_official_selection_has_no_interface():
    w = make_world(classification=Classification.OFFICIAL)
    submitted(w, assign=False)
    d = w.case(w.coordinator)
    assert d["selection"]["mode"] == "OFFICIAL_SELECTION" and d["selection"]["available"] is False
    r = w.coordinator.post(f"{w.case_url}/assign", {"user_id": user_id(REVIEWER), "row_version": d["queue"]["row_version"]})
    assert r.status_code == 409 and r.json()["code"] == "selection_unavailable"
    assert w.case_row().state == "AWAITING_ASSIGNMENT"
    with SessionLocal() as db:
        assert db.scalars(select(ScrutinyAssignment)).all() == []
        assert db.scalar(select(AuditEvent).where(AuditEvent.action == "scrutiny.assignment_unavailable")) is not None
    assert any("no interface" in a for a in w.case(w.coordinator)["queue"]["outstanding_actions"])


def test_reassignment_needs_a_reason_keeps_the_history_and_moves_access(world):
    submitted(world)
    assert world.scrutiny.get(world.case_url).status_code == 200
    v = world.case_row().row_version
    new = {"user_id": user_id(REVIEWER2), "row_version": v}
    assert world.coordinator.post(f"{world.case_url}/assign", new).status_code == 422  # no reason
    assert world.coordinator.post(f"{world.case_url}/assign", {**new, "reason": "Reviewer is unavailable this week"}).status_code == 200
    assert world.scrutiny.get(world.case_url).status_code == 404 and world.scrutiny2.get(world.case_url).status_code == 200
    rows = world.coordinator.get(world.case_url).json()["assignments"]
    assert [(a["user_name"], a["revoked_at"] is not None) for a in rows] == [("Test scrutiny_officer", True), ("Test scrutiny_officer", False)]
    assert rows[0]["revoked_reason"] == "Reassigned" or "unavailable" in rows[0]["revoked_reason"]


# ---- 4. exceptions instead of guessed routes ---------------------------------------------------------------------


def route(*stages: RouteStageIn):
    return list(stages)


def test_a_route_without_a_scrutiny_stage_creates_an_exception_not_a_guess():
    w = make_world(route_stages=route(RouteStageIn(stage_key="self-disclosure", label="Self-disclosure", display_order=0, next_stage_keys=[])))
    submitted(w, assign=False)
    row = w.case_row()
    assert row.state == "CONFIGURATION_EXCEPTION" and row.exception_code == "no_scrutiny_stage"
    q = w.coordinator.get("/api/scrutiny/cases?state=CONFIGURATION_EXCEPTION").json()
    assert q["total"] == 1 and q["items"][0]["exception"] and any("Configuration review needed" in a for a in q["items"][0]["outstanding_actions"])
    r = w.coordinator.post(f"{w.case_url}/assign", {"user_id": user_id(REVIEWER), "row_version": row.row_version})
    assert r.status_code == 409 and r.json()["code"] == "invalid_transition"
    assert w.scrutiny.get(w.case_url).status_code == 404


def stage(key, label, nxt=(), conditional=False, order=0):
    return RouteStageIn(stage_key=key, label=label, display_order=order, next_stage_keys=list(nxt), is_conditional=conditional,
                        condition_description="Only where the earlier outcome requires it." if conditional else None)


def finish_scrutiny(w):
    w.start()
    r = w.scrutiny.post(f"{w.case_url}/complete", {"row_version": w.case_row().row_version, "summary": "No findings to record."})
    assert r.status_code == 200, r.text
    return r.json()


def test_the_next_stage_comes_from_the_applications_own_route_not_a_hardcoded_destination():
    w = make_world(route_stages=route(stage("scrutiny", "Scrutiny", ["site-check"]), stage("site-check", "Site check (test stage)", [], order=1)))
    submitted(w)
    c = finish_scrutiny(w)
    assert c["queue"]["state"] == "SCRUTINY_COMPLETED"
    assert c["handoff"]["status"] == "QUEUED" and c["handoff"]["to_stage_key"] == "site-check" and c["handoff"]["to_stage_label"] == "Site check (test stage)"
    assert c["handoff"]["module_available"] is False  # nothing downstream is activated in this phase
    assert w.case_row().state == "SCRUTINY_COMPLETED" and w.a.get(w.url).json()["status"] == "SUBMITTED"
    assert [h["to_stage_label"] for h in w.coordinator.get("/api/scrutiny/handoffs").json()] == ["Site check (test stage)"]


def test_a_route_with_nothing_after_scrutiny_records_no_further_stage_not_evc():
    w = make_world(route_stages=route(stage("scrutiny", "Scrutiny")))
    submitted(w)
    c = finish_scrutiny(w)
    assert c["handoff"]["status"] == "NO_FURTHER_STAGE" and c["handoff"]["to_stage_key"] is None


def test_a_conditional_or_ambiguous_next_stage_awaits_an_explicit_recorded_determination():
    w = make_world(route_stages=route(stage("scrutiny", "Scrutiny", ["evc", "other"]), stage("evc", "EVC", [], order=1), stage("other", "Other", [], order=2)))
    submitted(w)
    c = finish_scrutiny(w)
    assert c["handoff"]["status"] == "AWAITING_DETERMINATION" and {o["stage_key"] for o in c["handoff"]["options"]} == {"evc", "other"}
    assert c["handoff"]["to_stage_key"] is None  # nothing was chosen on anyone's behalf
    # only the coordinator, only among the route's own options, only with a reason
    assert w.scrutiny.post(f"{w.case_url}/handoff/determine", {"stage_key": "evc", "reason": "A reviewer must not choose"}).status_code == 403
    assert w.coordinator.post(f"{w.case_url}/handoff/determine", {"stage_key": "shc", "reason": "Not in this route at all"}).status_code == 422
    assert w.coordinator.post(f"{w.case_url}/handoff/determine", {"stage_key": "evc", "reason": "no"}).status_code == 422
    assert w.coordinator.post(f"{w.case_url}/handoff/determine", {"stage_key": None, "reason": "Declaring none applies"}).status_code == 422  # two options: one must be chosen
    ok = w.coordinator.post(f"{w.case_url}/handoff/determine", {"stage_key": "other", "reason": "Outcome of scrutiny points to this stage."})
    assert ok.status_code == 200 and ok.json()["handoff"]["status"] == "QUEUED" and ok.json()["handoff"]["to_stage_key"] == "other"
    assert ok.json()["handoff"]["determined_by"] == "Test scrutiny_coordinator"
    assert w.coordinator.post(f"{w.case_url}/handoff/determine", {"stage_key": "evc", "reason": "Trying to change it again"}).status_code == 409


def test_a_conditional_single_next_stage_can_be_declared_not_applicable():
    w = make_world(route_stages=route(stage("scrutiny", "Scrutiny", ["evc"]), stage("evc", "EVC", [], conditional=True, order=1)))
    submitted(w)
    c = finish_scrutiny(w)
    assert c["handoff"]["status"] == "AWAITING_DETERMINATION" and "condition" in c["handoff"]["reason"]
    ok = w.coordinator.post(f"{w.case_url}/handoff/determine", {"stage_key": None, "reason": "The condition is not met for this application."})
    assert ok.json()["handoff"]["status"] == "NO_FURTHER_STAGE" and ok.json()["handoff"]["determination_reason"]


def test_a_route_that_cannot_be_applied_at_completion_goes_to_the_exception_queue():
    from app.services import routing

    res = routing.resolve_next({"route": [{"stage_key": "scrutiny", "label": "S", "next_stage_keys": ["ghost"]}], "rule_set": {"key": "x", "classification": "DEMO"}, "version": {"number": 1}})
    assert res.status.value == "EXCEPTION" and "does not exist" in res.reason
    res2 = routing.resolve_next({"route": [], "rule_set": {"key": "x", "classification": "DEMO"}, "version": {"number": 1}})
    assert res2.status.value == "EXCEPTION" and res2.to_stage_key is None


# ---- queue features --------------------------------------------------------------------------------------------


def test_queue_filtering_search_assignment_filters_and_pagination(world):
    submitted(world, assign=False)
    second = second_application(world)
    q = world.coordinator.get("/api/scrutiny/cases").json()
    assert q["total"] == 2 and q["counts"]["AWAITING_ASSIGNMENT"] == 2
    ref2 = second["reference"]
    assert [i["reference"] for i in world.coordinator.get(f"/api/scrutiny/cases?q={ref2.lower()}").json()["items"]] == [ref2]
    assert world.coordinator.get("/api/scrutiny/cases?q=Test%20Institute").json()["total"] == 2
    assert world.coordinator.get("/api/scrutiny/cases?q=nonexistent").json()["total"] == 0
    world.assign()
    assert world.coordinator.get("/api/scrutiny/cases?assigned=unassigned").json()["total"] == 1
    assert world.coordinator.get("/api/scrutiny/cases?state=READY_FOR_SCRUTINY").json()["total"] == 1
    assert world.scrutiny.get("/api/scrutiny/cases?assigned=me").json()["total"] == 1
    page = world.coordinator.get("/api/scrutiny/cases?page=2&page_size=1").json()
    assert page["total"] == 2 and len(page["items"]) == 1 and page["page"] == 2
    assert world.coordinator.get("/api/scrutiny/cases?state=bad-state!").status_code == 422
    assert world.coordinator.get("/api/scrutiny/cases?page_size=500").status_code == 422


def test_documents_that_could_not_be_processed_surface_as_a_processing_exception_in_the_queue(world):
    from tests.docs_helpers import corrupt_pdf

    world.upload(data=corrupt_pdf())
    drain()
    world.submit()
    drain()
    q = world.coordinator.get("/api/scrutiny/cases?processing_exceptions=true").json()
    assert q["processing_exceptions"] == 1 and q["total"] == 1 and q["items"][0]["processing"]["failed"] == 1
    assert any("could not be processed" in a for a in q["items"][0]["outstanding_actions"])
    assert world.case_row().state == "READY_FOR_SCRUTINY"  # a processing problem is surfaced, not turned into a finding or a notice
