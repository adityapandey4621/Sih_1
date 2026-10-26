"""The SHC compliance / hearing process: requirements, the institution's response, review, hearings, the versioned recommendation. Not a decision."""

import copy
import uuid

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.guards import ImmutableRecordError
from app.models import AuditEvent
from app.models_committee import CommitteeCase, CommitteePolicy, CommitteeReport, DecisionHandoff, ShcFinding, ShcProceeding, ShcRequirement, ShcResponse, ShcRound
from app.models_scrutiny import Notification
from tests.committee_world import SHC_M1, SHC_M2, future, make_cworld, past
from tests.evidence_world import doc_rows


def set_shc_policy(**overrides):
    with SessionLocal() as db:
        p = db.scalar(select(CommitteePolicy).where(CommitteePolicy.stage == "SHC"))
        content = copy.deepcopy(p.content)
        content["shc"].update(overrides)
        p.content = content
        db.commit()


@pytest.fixture
def c():
    c = make_cworld("shc_only")
    c.finish_scrutiny()
    return c


# ---- preparation and the request to the institution ---------------------------------------------------------


def test_shc_activates_in_preparation_with_the_route_and_policy_on_record(c):
    d = c.case("SHC")
    assert d["state"] == "PREPARATION" and d["trigger"]["from_stage_key"] == "scrutiny" and d["policy"]["classification"] == "DEMO" and d["policy"]["shc"]["compliance_submission"] == "PERMITTED"
    assert d["policy"]["shc"]["compliance_clause_ref"].startswith("APH 2024-27 Ch. II cl. 2.23.3") and d["shc"]["rounds"] == []
    assert "request_compliance" in d["allowed_actions"] and "record_finding" not in d["allowed_actions"]
    assert c.row("EVC") is None


def test_the_coordinator_prepares_requirements_with_sources_chosen_from_the_frozen_rule_version(c):
    opts = c.w.coordinator.get(f"{c.w.case_url}").json()  # scrutiny case still readable by its coordinator
    assert opts["queue"]["state"] == "SCRUTINY_COMPLETED"
    d = c.requirement()
    r = d["shc"]["prepared"][0]
    assert r["title"] == "Show the validity period" and r["item_key"] == "doc-main" and r["source_ref_status"] == "NEEDS_VERIFICATION" and r["origin"] == {"type": "manual"} and r["round_id"] is None
    src = c.w.scrutiny.get(f"{c.w.case_url}").json()["source_options"][0]["key"]
    d = c.requirement(title="With a source", source_ref_key=src)
    assert d["shc"]["prepared"][1]["source_ref_status"] == "ESTABLISHED" and d["shc"]["prepared"][1]["source_ref"]["key"] == src
    c.requirement(title="Bad key", source_ref_key="source:" + str(uuid.uuid4()), expect=422)  # not part of this application's rule version
    c.requirement(title="Bad file slot", item_key="no-such-item", expect=422)
    c.requirement(title="x", description="", expect=422)
    c.requirement(title="Unknown origin", origin={"type": "scrutiny_finding", "id": str(uuid.uuid4())}, expect=422)


def test_prepared_requirements_are_editable_and_removable_and_invisible_to_the_institution(c):
    d = c.requirement()
    rid = d["shc"]["prepared"][0]["id"]
    assert c.w.a.get("/api/compliance").json() == [] and c.w.a.get(f"/api/applications/{c.w.app['id']}/committee").json()["shc"]["rounds"] == []
    e = c.shc_coord.patch(f"{c.url('SHC')}/requirements/{rid}", {"title": "Show the validity period (revised)", "row_version": 1})
    assert e.status_code == 200 and e.json()["shc"]["prepared"][0]["title"].endswith("(revised)")
    assert c.shc_coord.patch(f"{c.url('SHC')}/requirements/{rid}", {"title": "Stale", "row_version": 1}).json()["code"] == "stale_version"
    d = c.shc_coord.delete(f"{c.url('SHC')}/requirements/{rid}").json()
    assert d["shc"]["prepared"] == []
    with SessionLocal() as db:
        assert db.get(ShcRequirement, uuid.UUID(rid)).removed_at is not None  # removed from preparation, still on record


def test_compliance_is_requested_only_with_requirements_and_instructions_and_freezes_them(c):
    c.request_compliance(expect=409)  # nothing to ask yet
    c.requirement()
    assert c.shc_coord.post(f"{c.url('SHC')}/compliance/request", {"instructions": " ", "row_version": c.rv("SHC")}).status_code == 422
    d = c.request_compliance()
    rnd = d["shc"]["rounds"][0]
    assert d["state"] == "AWAITING_COMPLIANCE" and rnd["number"] == 1 and rnd["status"] == "OPEN" and rnd["requirements"][0]["position"] == 1 and rnd["policy_resolution"]["state"] == "NO_DURATION"
    assert d["shc"]["prepared"] == []
    rid = rnd["requirements"][0]["id"]
    assert c.shc_coord.patch(f"{c.url('SHC')}/requirements/{rid}", {"title": "Changed after issue", "row_version": 1}).status_code == 409
    assert c.shc_coord.delete(f"{c.url('SHC')}/requirements/{rid}").status_code == 409
    with SessionLocal() as db:  # the ORM guard agrees
        from app.guards import ImmutableRecordError as Err

        r = db.get(ShcRequirement, uuid.UUID(rid))
        r.title = "Tampered"
        with pytest.raises(Err):
            db.flush()
        db.rollback()
        assert db.scalar(select(Notification.id).where(Notification.event_type == "COMPLIANCE_REQUESTED", Notification.recipient_kind == "institution")) is not None


def test_the_institution_sees_the_request_with_no_invented_deadline(c):
    c.requirement()
    c.request_compliance(instructions="Please respond to each point below.")
    lst = c.w.a.get("/api/compliance").json()
    assert len(lst) == 1 and lst[0]["can_respond"] is True and lst[0]["number"] == 1
    d = c.w.a.get(f"/api/compliance/{lst[0]['id']}").json()
    assert d["response_due_at"] is None and "No response deadline" in d["due_note"] and d["instructions"].startswith("Please respond") and d["demo_notice"].startswith("DEMO ONLY")
    assert d["requirements"][0]["title"] and "origin" not in d["requirements"][0] and "internal" not in str(d).lower()
    assert c.w.b.get(f"/api/compliance/{lst[0]['id']}").status_code == 404 and c.w.b.get("/api/compliance").json() == []


# ---- the institution's compliance response -----------------------------------------------------------------


def test_the_institution_responds_with_evidence_as_new_versions_and_it_is_received_not_accepted(c):
    c.requirement()
    c.request_compliance()
    before = [(d.version_number, d.sha256) for d in doc_rows(c.w.app["id"])]
    out = c.respond()
    assert [d.version_number for d in doc_rows(c.w.app["id"])] == [1, 2] and before[0] == (1, doc_rows(c.w.app["id"])[0].sha256)  # the original is kept
    assert out["response"]["status"] == "SUBMITTED" and out["status"] == "RESPONSE_RECEIVED" and "not accepted" in out["received_note"]
    assert out["requirements"][0]["documents"][0]["version_number"] == 2 and out["can_respond"] is False
    d = c.case("SHC")
    assert d["state"] == "COMPLIANCE_RECEIVED" and d["shc"]["findings"] == [] and d["shc"]["rounds"][0]["response"]["status"] == "SUBMITTED"
    assert d["shc"]["rounds"][0]["response"]["items"][0]["documents"][0]["version_number"] == 2
    with SessionLocal() as db:
        assert db.scalar(select(Notification.id).where(Notification.event_type == "COMPLIANCE_SUBMITTED", Notification.recipient_kind == "coordinator")) is not None
        assert db.scalar(select(ShcFinding.id)) is None  # uploading evidence assessed nothing
        assert db.scalar(select(DecisionHandoff.id)) is None


def test_a_compliance_response_can_only_be_made_while_the_process_permits_it(c):
    c.requirement()
    rid = None
    # before compliance is requested there is nothing to answer
    assert c.w.a.put(f"/api/compliance/{uuid.uuid4()}/response", {"explanation": "x", "items": [], "row_version": None}).status_code == 404
    c.request_compliance()
    rid = c.round_id()
    req = c.w.a.get(f"/api/compliance/{rid}").json()["requirements"][0]
    c.respond()
    # once received, the response is closed
    assert c.w.a.put(f"/api/compliance/{rid}/response", {"explanation": "Late change", "items": [], "row_version": 1}).json()["code"] == "compliance_not_open"
    up = c.w.a.upload(f"/api/compliance/{rid}/requirements/{req['id']}/documents", "file", "late.pdf", b"%PDF-1.4 x")
    assert up.status_code == 409 and up.json()["code"] == "compliance_not_open"
    assert [d.version_number for d in doc_rows(c.w.app["id"])] == [1, 2]
    # and a repeated submit is idempotent, a different one is refused
    again = c.w.a.post(f"/api/compliance/{rid}/response/submit", {"idempotency_key": "comp-key-0001", "row_version": 99})
    assert again.status_code == 200 and again.headers.get("X-Idempotent-Replay") == "true"
    other = c.w.a.post(f"/api/compliance/{rid}/response/submit", {"idempotency_key": "comp-key-0002", "row_version": 99})
    assert other.status_code == 409 and other.json()["code"] == "response_already_submitted"
    with SessionLocal() as db:
        assert len(db.scalars(select(ShcResponse)).all()) == 1


def test_compliance_for_the_wrong_request_or_institution_is_refused(c):
    c.requirement()
    c.request_compliance()
    rid = c.round_id()
    req = c.w.a.get(f"/api/compliance/{rid}").json()["requirements"][0]
    assert c.w.b.put(f"/api/compliance/{rid}/response", {"explanation": "x", "items": [], "row_version": None}).status_code in (403, 404)
    assert c.w.b.post(f"/api/compliance/{rid}/response/submit", {"idempotency_key": "other-inst-key", "row_version": 1}).status_code in (403, 404)
    wrong = c.w.a.put(f"/api/compliance/{rid}/response", {"explanation": "x", "items": [{"requirement_id": str(uuid.uuid4()), "explanation": "not mine"}], "row_version": None})
    assert wrong.status_code == 422 and wrong.json()["issues"][0]["code"] == "unknown_item"
    nothing = c.w.a.post(f"/api/compliance/{rid}/response/submit", {"idempotency_key": "empty-resp-key", "row_version": 1})
    assert nothing.status_code == 422 and nothing.json()["code"] == "empty_response"
    saved = c.w.a.put(f"/api/compliance/{rid}/response", {"explanation": "Draft", "items": [{"requirement_id": req["id"], "explanation": "Text"}], "row_version": None}).json()
    stale = c.w.a.put(f"/api/compliance/{rid}/response", {"explanation": "Draft 2", "items": [], "row_version": saved["response"]["row_version"] + 4})
    assert stale.json()["code"] == "stale_version"
    with SessionLocal() as db:
        assert db.scalar(select(AuditEvent.id).where(AuditEvent.action == "compliance.access_denied")) is not None


def test_an_institution_cannot_use_staff_endpoints_or_edit_committee_records(c):
    c.requirement()
    c.request_compliance()
    for method, path, body in (("post", f"{c.url('SHC')}/compliance/request", {"instructions": "x", "row_version": 1}), ("post", f"{c.url('SHC')}/findings", {"assessment": "INCONCLUSIVE", "text": "x y z"}),
                              ("put", f"{c.url('SHC')}/recommendation", {"option": None, "sections": {}, "row_version": 1}), ("post", f"{c.url('SHC')}/complete", {"summary": "x y z", "row_version": 1})):
        assert getattr(c.w.a, method)(path, body).status_code == 403


# ---- review ---------------------------------------------------------------------------------------------------


def test_review_needs_members_and_the_institutions_chance_to_comply(c):
    assert c.shc_coord.post(f"{c.url('SHC')}/review/start", {"row_version": c.rv("SHC")}).json()["code"] == "compliance_first"
    c.requirement()
    c.request_compliance()
    c.respond()
    r = c.shc_coord.post(f"{c.url('SHC')}/review/start", {"row_version": c.rv("SHC")})
    assert r.status_code == 409 and r.json()["code"] == "no_members_assigned"
    c.shc_select()
    d = c.start_review()
    assert d["state"] == "UNDER_REVIEW" and d["shc"]["draft"]["version_number"] == 1 and d["shc"]["draft"]["status"] == "DRAFT"


def test_only_assigned_members_record_findings_and_a_finding_is_not_a_decision(c):
    c.shc_to_review()
    assert c.shc2.post(f"{c.url('SHC')}/findings", {"assessment": "INCONCLUSIVE", "text": "Not assigned to this case."}).status_code == 404
    assert c.shc_coord.post(f"{c.url('SHC')}/findings", {"assessment": "INCONCLUSIVE", "text": "A coordinator is not a member."}).status_code == 403
    d = c.shc_finding()
    f = d["shc"]["findings"][0]
    assert f["assessment"] == "EVIDENCE_SUPPORTS" and f["sequence"] == 1 and f["by"] == "Test shc_member"
    c.shc_finding(assessment="ACCEPTED", expect=422)  # there is no "accepted" assessment
    c.shc_finding(text="x", expect=422)
    c.shc_finding(requirement_id=str(uuid.uuid4()), expect=422)
    upd = c.shc1.patch(f"{c.url('SHC')}/findings/{f['id']}", {"assessment": "INCONCLUSIVE", "text": "On reflection the letter is unclear.", "row_version": f["row_version"]})
    assert upd.status_code == 200 and upd.json()["shc"]["findings"][0]["assessment"] == "INCONCLUSIVE"
    assert c.shc1.patch(f"{c.url('SHC')}/findings/{f['id']}", {"text": "Stale edit of the finding.", "row_version": f["row_version"]}).json()["code"] == "stale_version"


def test_a_recommendation_needs_the_configured_content_and_an_assessment_of_every_requirement(c):
    c.shc_to_review()
    d = c.case("SHC", c.shc1)
    r = c.shc1.post(f"{c.url('SHC')}/recommendation/submit", {"idempotency_key": "rec-early-key-1", "row_version": d["shc"]["draft"]["row_version"]})
    assert r.status_code == 409 and r.json()["code"] == "recommendation_incomplete"
    assert {i["code"] for i in r.json()["issues"]} == {"option_missing", "section_missing", "requirement_unassessed"}
    c.recommend(option="APPROVE_THE_APPLICATION", expect=422)  # not a configured option
    c.shc_finding()
    c.recommend()
    d = c.submit_recommendation()
    assert d["state"] == "RECOMMENDATION_SUBMITTED"


def test_a_submitted_recommendation_is_preserved_immutable_and_separate_from_any_decision(c):
    c.shc_to_review()
    c.shc_finding()
    c.recommend()
    d = c.submit_recommendation(key="rec-once-key-01")
    v = d["shc"]["versions"][0]
    assert v["version_number"] == 1 and v["status"] == "SUBMITTED" and v["content"]["option"] == "RECOMMEND_NEXT_STAGE" and v["content_hash"]
    snap = v["source_snapshot"]
    assert snap["responses"][0]["round"] == 1 and snap["documents"][0]["version_number"] == 2 and snap["findings"][0]["assessment"] == "EVIDENCE_SUPPORTS" and snap["not_a_decision"] is True and snap["policy"]["classification"] == "DEMO"
    assert d["shc"]["draft"] is None and c.w.refresh()["status"] == "SUBMITTED" and "not an approval" in d["not_an_approval"]
    # a retry returns the same record; the author cannot reopen or edit it
    again = c.shc1.post(f"{c.url('SHC')}/recommendation/submit", {"idempotency_key": "rec-once-key-01", "row_version": 99})
    assert again.status_code == 200 and again.headers.get("X-Idempotent-Replay") == "true"
    with SessionLocal() as db:
        recs = db.scalars(select(CommitteeReport)).all()
        assert [r.status for r in recs] == ["SUBMITTED"]
        recs[0].content = {"option": "DO_NOT_RECOMMEND", "sections": {}}
        with pytest.raises(ImmutableRecordError):
            db.flush()
        db.rollback()
    assert c.shc1.put(f"{c.url('SHC')}/recommendation", {"option": "DO_NOT_RECOMMEND", "sections": {"summary": "Rewritten."}, "row_version": 2}).status_code == 409
    assert c.shc2.put(f"{c.url('SHC')}/recommendation", {"option": None, "sections": {}, "row_version": 2}).status_code == 404


def test_only_a_coordinator_amends_a_recommendation_and_every_version_stays(c):
    c.shc_to_review()
    c.shc_finding()
    c.recommend()
    c.submit_recommendation()
    original = c.case("SHC")["shc"]["versions"][0]
    body = {"reason": "The rationale cited the wrong round.", "row_version": c.rv("SHC")}
    assert c.shc1.post(f"{c.url('SHC')}/recommendation/amend", body).status_code == 403
    d = c.shc_coord.post(f"{c.url('SHC')}/recommendation/amend", body).json()
    assert d["state"] == "UNDER_REVIEW" and d["shc"]["draft"]["version_number"] == 2 and d["shc"]["draft"]["content"]["option"] == "RECOMMEND_NEXT_STAGE"
    c.recommend(option="FURTHER_COMPLIANCE", rationale="Further evidence is wanted.")
    d = c.submit_recommendation()
    vs = d["shc"]["versions"]
    assert [v["version_number"] for v in vs] == [1, 2] and vs[0]["content_hash"] == original["content_hash"] and vs[0]["content"] == original["content"] and vs[1]["content"]["option"] == "FURTHER_COMPLIANCE"


def test_completing_the_shc_stage_records_a_handoff_to_the_decision_stage_and_decides_nothing(c):
    c.shc_to_review()
    c.shc_finding()
    c.recommend()
    c.submit_recommendation()
    assert c.shc1.post(f"{c.url('SHC')}/complete", {"summary": "A member tries to complete.", "row_version": c.rv("SHC")}).status_code == 403
    d = c.complete_shc()
    assert d["state"] == "COMPLETED" and d["completion"]["decided"] is False and d["completion"]["option"] == "RECOMMEND_NEXT_STAGE" and d["next"]["status"] == "NO_FURTHER_STAGE"
    assert d["decision_handoff"]["status"] == "READY_FOR_DECISION_STAGE" and d["decision_handoff"]["basis"]["decided"] is False
    stages = [s["stage_key"] for s in d["decision_handoff"]["basis"]["stages"]]
    assert stages == ["scrutiny", "shc"] and d["decision_handoff"]["basis"]["stages"][1]["record"]["kind"] == "SHC_RECOMMENDATION"
    assert c.w.refresh()["status"] == "SUBMITTED"
    assert c.w.a.get(f"/api/applications/{c.w.app['id']}/committee").json()["ready_for_decision_stage"] is True


def test_the_institution_never_sees_the_recommendation_or_internal_findings(c):
    c.shc_to_review()
    c.shc_finding(text="Internal reasoning that the institution must not see.")
    c.recommend()
    c.submit_recommendation()
    c.complete_shc()
    s = c.w.a.get(f"/api/applications/{c.w.app['id']}/committee").json()["shc"]
    assert s["label"] == "Review complete; the case moves to the next stage"
    assert "Internal reasoning" not in str(s) and "recommend" not in str(s).lower().replace("recommendation goes", "")
    assert "findings" not in s and "versions" not in s


# ---- hearings ------------------------------------------------------------------------------------------------


def test_a_hearing_is_scheduled_confirmed_and_recorded_and_blocks_the_recommendation_until_it_is(c):
    c.shc_to_review()
    d = c.schedule("SHC")
    assert d["schedule"]["active"]["kind"] == "HEARING" and d["schedule"]["active"]["status"] == "PROPOSED"
    d = c.confirm("SHC")
    sid = d["schedule"]["active"]["id"]
    h = c.w.a.get(f"/api/applications/{c.w.app['id']}/committee").json()["shc"]["hearing"]
    assert h["status"] == "CONFIRMED" and h["mode"] == "ONLINE" and "Joining instructions" in h["access_details"]
    c.shc_finding()
    c.recommend()
    r = c.shc1.post(f"{c.url('SHC')}/recommendation/submit", {"idempotency_key": "hearing-block-1", "row_version": c.case("SHC", c.shc1)["shc"]["draft"]["row_version"]})
    assert r.status_code == 409 and {i["code"] for i in r.json()["issues"]} == {"hearing_pending"}
    body = {"held_local": past(2), "timezone": "UTC", "participants": "Two representatives of the institution; two members.", "text": "The institution presented the corrected letter.", "schedule_id": sid}
    assert c.shc2.post(f"{c.url('SHC')}/proceedings", body).status_code == 404
    assert c.shc1.post(f"{c.url('SHC')}/proceedings", {**body, "held_local": future()}).status_code == 422  # a sitting cannot be recorded in advance
    d = c.shc1.post(f"{c.url('SHC')}/proceedings", body).json()
    assert d["shc"]["proceedings"][0]["text"].startswith("The institution presented") and d["schedule"]["history"][0]["status"] == "COMPLETED" and d["schedule"]["active"] is None
    assert c.submit_recommendation()["state"] == "RECOMMENDATION_SUBMITTED"
    with SessionLocal() as db:  # proceedings are append-only
        p = db.scalar(select(ShcProceeding))
        p.text = "Rewritten."
        with pytest.raises(ImmutableRecordError):
            db.flush()
        db.rollback()


def test_a_hearing_cannot_be_scheduled_when_the_policy_does_not_establish_one():
    c = make_cworld("shc_only")
    set_shc_policy(hearing=None)
    c.finish_scrutiny()
    c.shc_select()
    r = c.shc_coord.post(f"{c.url('SHC')}/schedule", {"start_local": future(), "timezone": "Asia/Kolkata", "mode": "ONLINE", "access_details": "x", "idempotency_key": "no-hearing-key"})
    assert r.status_code == 409 and r.json()["code"] == "hearing_not_permitted"
    assert "propose_schedule" not in c.case("SHC")["allowed_actions"]


def test_a_hearing_cannot_be_scheduled_before_members_are_assigned(c):
    r = c.shc_coord.post(f"{c.url('SHC')}/schedule", {"start_local": future(), "timezone": "Asia/Kolkata", "mode": "ONLINE", "access_details": "x", "idempotency_key": "no-members-key"})
    assert r.status_code == 409 and r.json()["code"] == "no_members_assigned"


def test_a_required_hearing_must_have_taken_place_before_a_recommendation():
    c = make_cworld("shc_only")
    set_shc_policy(hearing="REQUIRED")
    c.finish_scrutiny()
    c.shc_to_review()
    c.shc_finding()
    c.recommend()
    r = c.shc1.post(f"{c.url('SHC')}/recommendation/submit", {"idempotency_key": "hearing-req-key", "row_version": c.case("SHC", c.shc1)["shc"]["draft"]["row_version"]})
    assert {i["code"] for i in r.json()["issues"]} == {"hearing_required"}


def test_where_compliance_is_not_permitted_the_process_goes_straight_to_review_and_the_institution_is_not_asked():
    c = make_cworld("shc_only")
    set_shc_policy(compliance_submission="NOT_PERMITTED")
    c.finish_scrutiny()
    c.shc_select()
    c.requirement()
    r = c.shc_coord.post(f"{c.url('SHC')}/compliance/request", {"instructions": "x", "row_version": c.rv("SHC")})
    assert r.status_code == 409 and r.json()["code"] == "compliance_not_permitted"
    d = c.start_review()
    assert d["state"] == "UNDER_REVIEW" and c.w.a.get("/api/compliance").json() == []


def test_where_the_configuration_does_not_say_whether_compliance_is_permitted_nothing_proceeds():
    c = make_cworld("shc_only")
    set_shc_policy(compliance_submission=None)
    c.finish_scrutiny()
    d = c.case("SHC")
    assert d["blockers"][0]["code"] == "compliance_rule_unverified" and "request_compliance" not in d["allowed_actions"]
    c.requirement()
    r = c.shc_coord.post(f"{c.url('SHC')}/compliance/request", {"instructions": "x", "row_version": c.rv("SHC")})
    assert r.status_code == 409 and r.json()["code"] == "configuration_blocked"


def test_a_further_compliance_round_is_a_new_round_and_the_first_stays_intact(c):
    c.shc_to_review()
    first = c.case("SHC")["shc"]["rounds"][0]
    c.requirement(title="A second point")
    r = c.shc_coord.post(f"{c.url('SHC')}/compliance/request", {"instructions": "One more point, please.", "row_version": c.rv("SHC")})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["state"] == "AWAITING_COMPLIANCE" and [x["number"] for x in d["shc"]["rounds"]] == [1, 2] and d["shc"]["rounds"][0]["requirements"] == first["requirements"] and d["shc"]["draft"]["version_number"] == 1
    assert d["shc"]["rounds"][0]["response"]["status"] == "SUBMITTED"
    c.respond(key="comp-key-0002")
    assert c.case("SHC")["state"] == "COMPLIANCE_RECEIVED"
    with SessionLocal() as db:
        assert len(db.scalars(select(ShcRound)).all()) == 2


def test_requirements_not_yet_assessed_in_a_later_round_block_the_recommendation(c):
    c.shc_to_review()
    c.shc_finding()
    c.recommend()
    c.requirement(title="A second point")
    c.request_compliance(instructions="One more point.")
    c.respond(key="comp-key-0003")
    c.start_review()
    r = c.shc1.post(f"{c.url('SHC')}/recommendation/submit", {"idempotency_key": "later-round-key", "row_version": c.case("SHC", c.shc1)["shc"]["draft"]["row_version"]})
    assert {i["code"] for i in r.json()["issues"]} == {"requirement_unassessed"}


def test_the_whole_shc_path_is_audited(c):
    c.shc_to_review()
    c.shc_finding()
    c.recommend()
    c.submit_recommendation()
    c.complete_shc()
    with SessionLocal() as db:
        actions = {a for (a,) in db.execute(select(AuditEvent.action).where(AuditEvent.action.like("committee.%")))}
    assert {"committee.requirement_added", "committee.compliance_requested", "committee.compliance_submitted", "committee.review_started", "committee.finding_recorded", "committee.recommendation_draft_saved",
            "committee.recommendation_submitted", "committee.stage_completed", "committee.decision_handoff_ready"} <= actions
    assert c.case("SHC")["events"][0]["action"] == "stage_activated"
    with SessionLocal() as db:
        case = db.scalar(select(CommitteeCase).where(CommitteeCase.stage == "SHC"))
        assert case.state == "COMPLETED" and case.completed_by_id is not None
    assert SHC_M1 != SHC_M2
