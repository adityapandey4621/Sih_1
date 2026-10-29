"""SAC appeals: eligibility and deadlines from the frozen configuration, submission, the SAC stage, and the Council's decision, in order."""

from datetime import timedelta

import pytest
from sqlalchemy import select, update

from app.config import Settings
from app.db import SessionLocal
from app.domain import Classification
from app.models import AuditEvent, utcnow
from app.models_committee import CommitteeCase
from app.models_decisions import Appeal, AppealDocument, CouncilDecision, DecisionCase, PostDecisionHandoff
from app.models_scrutiny import Notification
from app.services import appeal_rules
from tests.conftest import Api
from tests.decision_world import make_dworld
from tests.docs_helpers import digital_pdf


def rows(model):
    with SessionLocal() as db:
        return list(db.scalars(select(model)))


def issued(outcome="LOR", **kw):
    d = make_dworld(**kw)
    d.to_queue()
    d.observe()
    d.decide(outcome, **({"speaking_order": None} if outcome != "LOR" else {}))
    d.issue()
    return d


def backdate_issue(days: int) -> None:
    with SessionLocal() as db:
        db.execute(update(DecisionCase).values(first_issued_at=utcnow() - timedelta(days=days)))
        db.commit()


# ---- 5. eligibility and deadlines come from the configured rules ----------------------------------------------


def test_the_deadline_is_computed_from_the_rule_versions_own_policy_and_trigger():
    d = issued(appeal_days=3)
    e = d.status()["appeal"]["eligibility"]
    assert e["state"] == "ELIGIBLE" and e["outcome"] == "LOR" and e["clause_ref"] == "test"
    from datetime import datetime

    first = datetime.fromisoformat(e["trigger_at"])
    assert datetime.fromisoformat(e["deadline_at"]) - first == timedelta(days=3)  # not 7: nothing is hard-coded
    w = d.status()["appeal"]["window"]
    assert w["policy_key"] == "appeal" and w["trigger_event"] == "executive_committee_decision_uploaded" and w["duration_value"] == 3 and w["state"] == "DEMO_COMPUTED"
    assert d.case()["appeal_eligibility"]["state"] == "ELIGIBLE"


def test_with_no_appeal_policy_on_the_rule_version_appeals_are_blocked_and_no_deadline_is_invented():
    d = issued(appeal_days=None)
    e = d.status()["appeal"]["eligibility"]
    assert e["state"] == "BLOCKED_BY_CONFIGURATION" and "no appeal-window policy" in e["message"] and e["deadline_at"] is None
    d.appeal(expect=409)
    assert rows(Appeal) == []
    with SessionLocal() as db:
        assert db.scalar(select(AuditEvent.id).where(AuditEvent.action == "appeal.refused")) is not None


def _cfg(policies, classification="DEMO", sources=None):
    return {"rule_set": {"classification": classification}, "deadlines": policies, "sources": sources or [{"id": "s1", "title": "T", "verification_status": "VERIFIED_OFFICIAL"}]}


def _pol(**kw):
    return {"policy_key": "p", "trigger_event": "executive_committee_decision_uploaded", "duration_value": 7, "duration_unit": "days", "calendar_convention": "CALENDAR", "source_id": "s1", "clause_ref": "Ch. II cl. 2.24(a)", **kw}


@pytest.mark.parametrize(
    "config,outcome,state",
    [
        (_cfg([]), "LOR", "MISSING"),
        (_cfg([_pol(), _pol(policy_key="q")]), "LOR", "AMBIGUOUS"),
        (_cfg([_pol(duration_value=None, duration_unit=None)]), "LOR", "NO_DURATION"),
        (_cfg([_pol(calendar_convention="WORKING")]), "LOR", "UNSUPPORTED"),  # there is no verified calendar to count working days with
        (_cfg([_pol()], sources=[{"id": "s1", "title": "T", "verification_status": "SECONDARY_UNVERIFIED"}]), "LOR", "UNVERIFIED"),
        (_cfg([_pol()]), "LOR", "COMPUTED"),
        (_cfg([_pol(calendar_convention=None)]), "LOR", "COMPUTED"),  # the handbook states no convention: used, and said to be unverified
        (_cfg([_pol(trigger_event="letter_of_deficiency_issued", policy_key="lod"), _pol()]), "LOD", "COMPUTED"),  # Ch. I cl. 1.11(a) applies to an LoD
        (_cfg([_pol(trigger_event="letter_of_deficiency_issued", policy_key="lod"), _pol()]), "LOR", "COMPUTED"),
    ],
)
def test_the_appeal_window_resolver_never_guesses(config, outcome, state):
    w = appeal_rules.resolve_window(config, Settings(allow_demo_rules=True, environment="development"), outcome)
    assert w.state.value == state
    issued_at = utcnow()
    assert (w.deadline(issued_at) == issued_at + timedelta(days=7)) == (state == "COMPUTED")


def test_the_lod_trigger_is_preferred_for_a_letter_of_deficiency_and_the_decision_upload_trigger_otherwise():
    cfg = _cfg([_pol(trigger_event="letter_of_deficiency_issued", policy_key="lod", duration_value=7), _pol(policy_key="up", duration_value=9)])
    s = Settings(allow_demo_rules=True, environment="development")
    assert appeal_rules.resolve_window(cfg, s, "LOD").policy_key == "lod"
    assert appeal_rules.resolve_window(cfg, s, "LOR").policy_key == "up"


def test_an_unstated_counting_convention_is_flagged_not_hidden():
    w = appeal_rules.resolve_window(_cfg([_pol(calendar_convention=None)]), Settings(), "LOR")
    assert w.state.value == "COMPUTED" and "NOT verified" in w.message and w.note and "cut-off time" in w.note


def test_a_demo_policy_does_not_count_on_a_non_demo_case_or_when_demo_rules_are_off():
    demo_src = [{"id": "s1", "title": "T", "verification_status": "DEMO_ONLY"}]
    assert appeal_rules.resolve_window(_cfg([_pol()], "OFFICIAL", demo_src), Settings(allow_demo_rules=True), "LOR").state.value == "UNVERIFIED"
    assert appeal_rules.resolve_window(_cfg([_pol()], "DEMO", demo_src), Settings(allow_demo_rules=False), "LOR").state.value == "UNVERIFIED"
    assert appeal_rules.resolve_window(_cfg([_pol()], "DEMO", demo_src), Settings(allow_demo_rules=True, environment="development"), "LOR").state.value == "DEMO_COMPUTED"


def test_a_non_demo_case_needs_verified_decision_and_appeal_configuration():
    d = make_dworld(classification=Classification.OFFICIAL, appeal_days=7)
    d.c.finish_scrutiny(official=True)
    assert d.case()["blockers"][0]["code"] == "no_decision_policy"
    assert d.status()["appeal"]["eligibility"]["state"] == "NOT_ISSUED"  # nothing can be decided or issued without configuration, so nothing can be appealed


# ---- eligibility refusals ---------------------------------------------------------------------------------------


def test_an_appeal_after_the_window_has_closed_is_refused_with_the_clause_and_the_audit_record():
    d = issued()
    backdate_issue(10)
    e = d.status()["appeal"]["eligibility"]
    assert e["state"] == "WINDOW_CLOSED" and e["clause_ref"] == "test" and e["deadline_at"]
    r = d.w.a.post(f"/api/applications/{d.w.app['id']}/appeal", {"grounds": "Grounds that arrive after the period has ended.", "idempotency_key": "late-appeal-001"})
    assert r.status_code == 409 and r.json()["code"] == "appeal_not_eligible" and "appeal period has ended" in r.json()["detail"]
    assert rows(Appeal) == [] and rows(CommitteeCase) == []
    with SessionLocal() as db:
        ev = db.scalar(select(AuditEvent).where(AuditEvent.action == "appeal.refused"))
        assert ev.detail["state"] == "WINDOW_CLOSED"


def test_an_outcome_the_configuration_does_not_list_as_appealable_cannot_be_appealed_and_no_sac_case_exists():
    d = issued("LOA")
    assert d.status()["appeal"]["eligibility"]["state"] == "OUTCOME_NOT_APPEALABLE"
    d.appeal(expect=409)
    assert rows(Appeal) == [] and [c for c in rows(CommitteeCase) if c.stage == "SAC"] == []


def test_nothing_can_be_appealed_before_an_official_outcome_is_issued():
    d = make_dworld()
    d.to_queue()
    d.observe()
    d.decide("LOR")  # recorded, not yet issued
    assert d.status()["appeal"]["eligibility"]["state"] == "NOT_ISSUED"
    d.appeal(expect=409)
    assert rows(Appeal) == []


def test_an_appeal_is_against_the_executive_committee_decision_not_against_committee_records_in_isolation():
    d = make_dworld("evc_only")
    d.c.finish_scrutiny()
    d.c.ready_visit()
    d.c.evc_to_report()
    d.c.complete_evc()
    d.appeal(expect=409)  # an EVC report exists, but no Executive Committee decision has been issued
    d.observe()
    d.decide("LOR")
    d.issue()
    ap = d.appeal()["appeal"]["submitted"]
    row = d.appeal_row()
    with SessionLocal() as db:
        from app.models_decisions import EcDecision

        assert db.get(EcDecision, row.against_decision_id).outcome == "LOR"
    assert ap["status"] == "UNDER_SAC_REVIEW"


def test_only_the_owning_institution_can_appeal_and_only_with_the_permission():
    d = issued()
    assert d.w.b.post(f"/api/applications/{d.w.app['id']}/appeal", {"grounds": "Another institution tries to appeal.", "idempotency_key": "other-inst-0001"}).status_code == 404
    for name, client in (("bureau", d.bureau), ("ec", d.ec), ("council", d.council), ("sac_coord", d.sac_coord), ("scrutiny", d.w.scrutiny), ("evc", d.c.evc_coord)):
        assert client.post(f"/api/applications/{d.w.app['id']}/appeal", {"grounds": "Staff cannot appeal for an institution.", "idempotency_key": f"staff-{name}-0001"}).status_code == 403, name
    assert rows(Appeal) == []
    assert d.w.a.post(f"/api/applications/{d.w.app['id']}/appeal", {"grounds": "short", "idempotency_key": "short-grounds-01"}).status_code == 422


# ---- submitting and duplicate prevention ---------------------------------------------------------------------


def test_an_eligible_appeal_is_recorded_once_with_its_eligibility_evaluation_and_opens_the_sac_case():
    d = issued()
    out = d.appeal()
    ap = out["appeal"]["submitted"]
    assert ap["status"] == "UNDER_SAC_REVIEW" and ap["status_label"].startswith("Under review by the Standing Appellate Committee") and "promise any outcome" in ap["note"]
    row = d.appeal_row()
    assert row.eligibility["state"] == "ELIGIBLE" and row.eligibility["clause_ref"] and row.eligibility["deadline_at"] and row.sac_case_id
    sac = d.sac_case()
    assert sac["stage"] == "SAC" and sac["state"] == "SELECTION_PENDING" and sac["trigger"]["source"] == "appeal" and sac["sac"]["appeal"]["grounds"].startswith("The institution is aggrieved")
    assert sac["sac"]["against_decision"]["outcome"] == "LOR" and sac["policy"]["classification"] == "DEMO"
    assert d.status()["appeal"]["eligibility"]["state"] == "ALREADY_APPEALED"
    with SessionLocal() as db:
        kinds = {(n.event_type, n.recipient_kind) for n in db.scalars(select(Notification))}
    assert ("APPEAL_SUBMITTED", "coordinator") in kinds and ("APPEAL_SUBMITTED", "institution") in kinds


def test_a_repeated_appeal_request_returns_the_same_appeal_and_a_second_appeal_is_refused():
    d = issued()
    key = "appeal-once-0001"
    first = d.w.a.post(f"/api/applications/{d.w.app['id']}/appeal", {"grounds": "The grounds of the appeal, stated once and in full.", "idempotency_key": key})
    again = d.w.a.post(f"/api/applications/{d.w.app['id']}/appeal", {"grounds": "The grounds of the appeal, stated once and in full.", "idempotency_key": key})
    assert first.status_code == 201 and again.status_code == 200 and again.headers.get("X-Idempotent-Replay") == "true"
    second = d.w.a.post(f"/api/applications/{d.w.app['id']}/appeal", {"grounds": "A second appeal, which the handbook does not allow.", "idempotency_key": "appeal-twice-002"})
    assert second.status_code == 409 and second.json()["code"] == "already_appealed"
    assert len(rows(Appeal)) == 1 and len([c for c in rows(CommitteeCase) if c.stage == "SAC"]) == 1


def test_the_database_itself_refuses_a_second_appeal_for_the_same_application():
    from sqlalchemy.exc import IntegrityError

    d = issued()
    d.appeal()
    with SessionLocal() as db:
        first = db.scalar(select(Appeal))
        db.add(Appeal(application_id=first.application_id, institution_id=first.institution_id, decision_case_id=first.decision_case_id, against_decision_id=first.against_decision_id, grounds="x" * 30,
                      eligibility={}, submitted_by_id=first.submitted_by_id))
        with pytest.raises(IntegrityError):
            db.flush()
        db.rollback()


# ---- supporting evidence -------------------------------------------------------------------------------------


def test_supporting_evidence_is_kept_private_and_changes_nothing_about_the_status():
    d = issued()
    ap = d.appeal()["appeal"]["submitted"]
    r = d.w.a.upload(f"/api/appeals/{ap['id']}/documents", "file", "grounds.pdf", digital_pdf(["Supporting evidence for the appeal."]))
    assert r.status_code == 201, r.text
    docs = r.json()["appeal"]["submitted"]["documents"]
    assert docs[0]["filename"] == "grounds.pdf" and docs[0]["download"].startswith("/api/appeals/documents/") and r.json()["appeal"]["submitted"]["status"] == "UNDER_SAC_REVIEW"
    link = d.w.a.post(f"/api/appeals/{ap['id']}/documents/link", {"url": "https://trust.example.org/evidence", "description": "Published material"})
    assert link.status_code == 201 and len(link.json()["appeal"]["submitted"]["documents"]) == 2
    assert d.w.a.post(f"/api/appeals/{ap['id']}/documents/link", {"url": "javascript:alert(1)", "description": "x"}).status_code == 422
    bad = d.w.a.http.post(f"/api/appeals/{ap['id']}/documents", files={"file": ("a.txt", b"hello", "text/plain")}, data={"description": "x"}, headers=d.w.a._csrf())
    assert bad.status_code == 415
    dl = docs[0]["download"]
    assert d.w.a.get(dl).status_code == 200
    assert d.w.b.get(dl).status_code == 404 and d.bureau.get(dl).status_code in (403, 404)
    assert d.w.b.post(f"/api/appeals/{ap['id']}/documents/link", {"url": "https://x.example.org/a", "description": "intruder"}).status_code == 404
    with SessionLocal() as db:
        assert len(db.scalars(select(AppealDocument)).all()) == 2


def test_sac_staff_read_appeal_documents_only_within_their_scope_and_evidence_closes_when_the_sac_stage_ends():
    d = issued()
    ap = d.appeal()["appeal"]["submitted"]
    d.w.a.upload(f"/api/appeals/{ap['id']}/documents", "file", "grounds.pdf", digital_pdf(["Supporting evidence."]))
    doc = d.sac_case()["sac"]["documents"][0]
    assert d.sac_coord.get(doc["download"]).status_code == 200  # a coordinator of the SAC stage
    assert d.sac1.get(doc["download"]).status_code == 404  # a member who is not assigned
    d.sac_select()
    assert d.sac1.get(doc["download"]).status_code == 200  # assigned now
    assert d.c.shc1.get(doc["download"]).status_code == 403
    d.sac_recommend()
    d.sac_submit()
    d.sac_complete()
    late = d.w.a.post(f"/api/appeals/{ap['id']}/documents/link", {"url": "https://x.example.org/late", "description": "after the SAC stage"})
    assert late.status_code == 409 and late.json()["code"] == "appeal_evidence_closed"


# ---- 6. ineligible users cannot review appeals ---------------------------------------------------------------


def test_only_sac_roles_review_appeals_and_only_assigned_members_work_the_case():
    d = issued()
    d.appeal()
    assert d.w.a.get(d.sac_url()).status_code == 403 and d.bureau.get(d.sac_url()).status_code == 403 and d.ec.get(d.sac_url()).status_code == 403 and d.council.get(d.sac_url()).status_code == 403
    assert d.c.evc_coord.get(d.sac_url()).status_code == 403
    assert d.sac1.get(d.sac_url()).status_code == 404  # the SAC member role alone shows no case
    assert d.sac1.post(f"{d.sac_url()}/selection", {"user_ids": [], "idempotency_key": "member-sel-0001"}).status_code == 403
    d.sac_select()
    assert d.sac1.get(d.sac_url()).status_code == 200
    assert d.sac1.post(f"{d.sac_url()}/complete", {"summary": "A member tries to complete.", "row_version": 1}).status_code == 403
    assert d.sac_coord.put(f"{d.sac_url()}/recommendation", {"option": None, "sections": {}, "row_version": 1}).status_code == 403
    assert d.sac_case(d.sac_coord)["scope"] == ["coordinator"] and d.sac_case(d.sac1)["scope"] == ["member"]
    assert d.sac_coord.get("/api/sac/cases").json()["total"] == 1 and d.sac1.get("/api/sac/cases").json()["total"] == 1


def test_sac_selection_is_a_labelled_demonstration_and_never_the_official_process():
    d = issued()
    d.appeal()
    s = d.sac_case()["selection"]
    assert s["availability"]["demo"] is True and "NOT the official AICTE" in s["availability"]["notice"]
    out = d.sac_select()
    assert out["state"] == "UNDER_REVIEW" and out["selection"]["assignees"][0]["demo"] is True and out["sac"]["draft"]["version_number"] == 1


# ---- 8. the order: SAC, then the Council ---------------------------------------------------------------------


def test_the_sac_recommends_and_only_then_can_the_council_decide_in_that_order():
    d = issued()
    ap = d.appeal()["appeal"]["submitted"]
    d.council_decide_appeal(expect=409)  # the SAC has not reviewed it
    d.sac_select()
    d.council_decide_appeal(expect=409)
    d.sac_recommend(option="NOT_RECOMMEND")
    r = d.sac1.post(f"{d.sac_url()}/recommendation/submit", {"idempotency_key": "sac-early-0001", "row_version": d.sac_case(d.sac1)["sac"]["draft"]["row_version"] + 9})
    assert r.status_code == 409
    d.sac_submit()
    d.council_decide_appeal(expect=409)  # submitted, but the stage is not complete
    assert d.status()["appeal"]["submitted"]["status"] == "UNDER_SAC_REVIEW"
    c = d.sac_complete()
    assert c["state"] == "COMPLETED" and c["completion"]["decided"] is False and c["state_label"] == "SAC stage completed (the Council decides)"
    case = d.case()
    assert case["appeal"]["status"] == "AWAITING_COUNCIL_DECISION" and case["appeal"]["sac_recommendation"]["option"] == "NOT_RECOMMEND"
    assert d.status()["official_status"]["state"] == "EXECUTIVE_COMMITTEE_DECISION_ISSUED"  # the SAC's recommendation changed nothing official
    out = d.council_decide_appeal("LOA")
    assert out["appeal"]["status"] == "COUNCIL_DECIDED" and out["appeal"]["live_council_decision"]["outcome"] == "LOA"
    st = d.status()
    assert st["official_status"]["state"] == "COUNCIL_DECISION_ON_APPEAL" and "final under the handbook" in st["official_status"]["label"]
    assert st["appeal"]["submitted"]["council"]["outcome_label"] == "Letter of Approval (LoA)" and st["appeal"]["submitted"]["council"]["reasons"]
    acts = [e["action"] for e in out["events"]]
    assert acts.index("appeal_submitted") < acts.index("sac_completed") < acts.index("appeal_decided")
    assert ap["id"] == str(d.appeal_row().id)
    with SessionLocal() as db:
        assert db.scalar(select(Notification.id).where(Notification.event_type == "APPEAL_DECIDED", Notification.recipient_kind == "institution")) is not None
        assert db.scalar(select(Notification.id).where(Notification.event_type == "SAC_RECOMMENDATION_READY")) is not None


def test_the_sac_recommendation_is_not_the_councils_decision_even_if_it_recommends():
    d = issued()
    d.appeal()
    d.sac_select()
    d.sac_recommend(option="RECOMMEND")
    d.sac_submit()
    d.sac_complete()
    assert d.status()["appeal"]["submitted"]["council"] is None and d.status()["official_status"]["outcome"] == "LOR"
    assert d.case()["appeal"]["live_council_decision"] is None


def test_the_council_decision_is_recorded_by_the_council_with_configured_outcomes_and_reasons():
    d = issued()
    d.appeal()
    d.sac_select()
    d.sac_recommend()
    d.sac_submit()
    d.sac_complete()
    for who in (d.ec, d.bureau, d.sac_coord, d.w.a):
        assert who.post(f"/api/decisions/appeals/{d.appeal_row().id}/council-decision", {"outcome": "LOA", "reasons": "Not the Council.", "idempotency_key": f"x-{id(who)}-000"}).status_code == 403
    d.council_decide_appeal("LOD", expect=422)  # not one of the Council's configured appeal outcomes
    d.council_decide_appeal("LOA", reasons="short", expect=422)
    d.council_decide_appeal("LOR", speaking_order=None, expect=422)  # a rejection needs its Speaking Order
    assert rows(CouncilDecision) == []
    d.council_decide_appeal("LOR")
    assert d.status()["appeal"]["submitted"]["council"]["outcome"] == "LOR"


def test_a_council_correction_supersedes_the_earlier_decision_and_keeps_it():
    d = issued()
    d.appeal()
    d.sac_select()
    d.sac_recommend()
    d.sac_submit()
    d.sac_complete()
    d.council_decide_appeal("LOR")
    d.council_decide_appeal("LOA", expect=409)  # already decided
    out = d.council_decide_appeal("LOA", correction_reason="The minute named the wrong outcome.")
    cds = out["appeal"]["council_decisions"]
    assert [c["outcome"] for c in cds] == ["LOR", "LOA"] and cds[1]["supersedes_id"] == cds[0]["id"] and out["appeal"]["live_council_decision"]["outcome"] == "LOA"
    with SessionLocal() as db:
        assert len([c for c in db.scalars(select(CouncilDecision)) if c.is_live]) == 1


def test_a_replayed_council_decision_creates_one_record():
    d = issued()
    d.appeal()
    d.sac_select()
    d.sac_recommend()
    d.sac_submit()
    d.sac_complete()
    ap = d.appeal_row()
    body = {"outcome": "LOA", "reasons": "The Council considered the appeal in full.", "idempotency_key": "council-once-001"}
    assert d.council.post(f"/api/decisions/appeals/{ap.id}/council-decision", body).status_code == 201
    again = d.council.post(f"/api/decisions/appeals/{ap.id}/council-decision", body)
    assert again.status_code == 200 and again.headers.get("X-Idempotent-Replay") == "true" and len(rows(CouncilDecision)) == 1


def test_a_recorded_sac_recommendation_is_immutable_and_amended_only_by_the_sac_coordinator():
    from app.guards import ImmutableRecordError
    from app.models_committee import CommitteeReport

    d = issued()
    d.appeal()
    d.sac_select()
    d.sac_recommend()
    d.sac_submit()
    v = d.sac_case()["sac"]["versions"][0]
    assert v["version_number"] == 1 and v["source_snapshot"]["against_decision"]["outcome"] == "LOR" and v["source_snapshot"]["not_a_council_decision"] is True and v["source_snapshot"]["appeal"]["grounds"]
    with SessionLocal() as db:
        r = db.scalar(select(CommitteeReport).where(CommitteeReport.kind == "SAC_RECOMMENDATION"))
        r.content = {"option": "RECOMMEND", "sections": {}}
        with pytest.raises(ImmutableRecordError):
            db.flush()
        db.rollback()
    assert d.sac1.post(f"{d.sac_url()}/recommendation/amend", {"reason": "A member amends.", "row_version": 1}).status_code == 403
    out = d.sac_coord.post(f"{d.sac_url()}/recommendation/amend", {"reason": "The rationale cited the wrong clause.", "row_version": d.sac_case()["row_version"]}).json()
    assert out["state"] == "UNDER_REVIEW" and out["sac"]["draft"]["version_number"] == 2 and out["sac"]["versions"][0]["content_hash"] == v["content_hash"]


def test_a_hearing_pending_blocks_the_sac_recommendation_until_it_is_recorded():
    from tests.committee_world import future, past

    d = issued()
    d.appeal()
    d.sac_select()
    r = d.sac_coord.post(f"{d.sac_url()}/schedule", {"start_local": future(), "timezone": "Asia/Kolkata", "mode": "ONLINE", "access_details": "Joining details will follow.", "idempotency_key": "sac-hearing-001"})
    assert r.status_code == 201 and r.json()["schedule"]["active"]["kind"] == "HEARING"
    sid = r.json()["schedule"]["active"]["id"]
    s = d.sac_case()["schedule"]["active"]
    assert d.sac_coord.post(f"{d.sac_url()}/schedule/{sid}/confirm", {"row_version": s["row_version"]}).status_code == 200
    d.sac_recommend()
    d.sac1.post(f"{d.sac_url()}/recommendation/submit", {"idempotency_key": "sac-hold-0001", "row_version": d.sac_case(d.sac1)["sac"]["draft"]["row_version"]}).json()["issues"]
    out = d.sac1.post(f"{d.sac_url()}/proceedings", {"held_local": past(2), "timezone": "UTC", "participants": "Members and the institution's representatives.", "text": "The institution presented its grounds.", "schedule_id": sid})
    assert out.status_code == 201 and out.json()["sac"]["proceedings"][0]["text"].startswith("The institution presented")
    assert d.sac_submit()["state"] == "RECOMMENDATION_SUBMITTED"


# ---- 7. the NO branch bypasses SAC ----------------------------------------------------------------------------


def test_with_no_appeal_the_sac_is_bypassed_and_the_case_closes_into_the_post_decision_state():
    d = issued("LOR")
    d.ratify("RATIFIED")
    backdate_issue(9)  # the window runs out without an appeal
    assert d.status()["appeal"]["eligibility"]["state"] == "WINDOW_CLOSED"
    out = d.close()
    assert out["state"] == "CLOSED" and out["appeal"] is None and out["closure"]["appeal"] is None
    assert [c for c in rows(CommitteeCase) if c.stage == "SAC"] == [] and rows(Appeal) == []
    with SessionLocal() as db:
        h = db.scalar(select(PostDecisionHandoff))
        assert h is not None and h.basis["official_outcome"] == "LOR" and "not part of this system yet" in h.basis["note"]
    assert d.status()["official_status"]["outcome"] == "LOR" and d.status()["closed"] is True


def test_with_an_appeal_the_case_cannot_close_until_the_council_has_decided():
    d = issued("LOR")
    d.ratify("RATIFIED")
    d.appeal()
    assert {b["code"] for b in d.case()["closure_blockers"]} == {"appeal_open"}
    d.close(expect=409)
    d.sac_select()
    d.sac_recommend()
    d.sac_submit()
    d.sac_complete()
    d.close(expect=409)
    d.council_decide_appeal("LOA")
    out = d.close()
    assert out["state"] == "CLOSED" and out["closure"]["appeal"]["council_outcome"] == "LOA" and out["closure"]["official_outcome"] == "LOA"


def test_the_window_anchor_is_the_first_issue_even_after_a_correction():
    d = issued("LOR")
    anchor = d.case()["first_issued_at"]
    d.correct("LOD")
    d.issue("LOD/2026/0002")
    assert d.case()["first_issued_at"] == anchor
    e = d.status()["appeal"]["eligibility"]
    assert e["trigger_at"].startswith(anchor[:19]) and e["outcome"] == "LOD"


def test_a_closed_window_is_never_reopened_by_a_late_correction():
    d = issued("LOR")
    backdate_issue(10)
    d.correct("LOD")
    d.issue("LOD/2026/0002")
    assert d.status()["appeal"]["eligibility"]["state"] == "WINDOW_CLOSED"
    _ = Api
