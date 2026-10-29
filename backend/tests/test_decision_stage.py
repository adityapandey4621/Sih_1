"""The Executive Committee decision stage: queue entry, authorisation, recording, correction, issue, Council ratification and closing."""

import io
import uuid

import pytest
from sqlalchemy import select, update

from app.db import SessionLocal
from app.guards import ImmutableRecordError
from app.models import AuditEvent
from app.models_applications import Application
from app.models_decisions import BureauObservation, CouncilDecision, DecisionCase, EcDecision, OfficialIssuance, PostDecisionHandoff
from app.models_scrutiny import Notification
from app.rbac import Role
from app.services import decisions as dsvc
from app.services import stages
from tests.conftest import Api, make_user
from tests.decision_world import make_dworld
from tests.docs_helpers import digital_pdf


def rows(model):
    with SessionLocal() as db:
        return list(db.scalars(select(model)))


# ---- 1. entering the queue ---------------------------------------------------------------------------------


def test_a_case_enters_the_decision_queue_only_when_every_required_stage_is_complete():
    d = make_dworld("evc_only")
    d.c.finish_scrutiny()
    assert d.case_row() is None and d.bureau.get("/api/decisions/cases").json()["items"] == []  # EVC is required and not yet done
    d.c.ready_visit()
    d.c.evc_to_report()
    assert d.case_row() is None  # a submitted report is not a completed stage
    d.c.complete_evc()
    q = d.bureau.get("/api/decisions/cases").json()
    assert q["total"] == 1 and q["items"][0]["state"] == "AWAITING_OBSERVATIONS" and q["items"][0]["application"]["reference"] == d.w.app["reference"]
    c = d.case()
    assert [s for s in c["route"]["stages"]] == ["scrutiny", "evc"] and c["policy"]["classification"] == "DEMO" and c["policy"]["banner"].startswith("DEMO ONLY")


def test_a_route_with_an_undetermined_conditional_stage_does_not_reach_the_queue():
    d = make_dworld("evc_conditional")
    d.c.finish_scrutiny()  # EVC is conditional and nobody has determined it
    assert d.case_row() is None
    d.w.coordinator.post(f"{d.w.case_url}/handoff/determine", {"stage_key": None, "reason": "EVC does not apply to this application."})
    assert d.case_row() is not None


def test_a_route_without_committee_stages_goes_straight_to_the_queue():
    d = make_dworld("scrutiny_only")
    d.c.finish_scrutiny()
    assert d.case_row() is not None and d.case()["state"] == "AWAITING_OBSERVATIONS"


def test_the_decision_is_blocked_and_explained_if_a_required_stage_turns_out_to_be_incomplete(monkeypatch):
    d = make_dworld()
    d.to_queue()
    d.observe()
    monkeypatch.setattr(stages, "_blocked_reason", lambda db, app: "The SHC stage is not complete.")
    c = d.case()
    assert c["state"] == "READY_FOR_DECISION" and c["blockers"][0]["code"] == "stage_incomplete" and "SHC stage is not complete" in c["blockers"][0]["message"] and "record_decision" not in c["allowed_actions"]
    r = d.ec.post(f"{d.url()}/decision", {"outcome": "LOA", "reasons": "A decision that must not be recorded.", "considered": ["scrutiny"], "idempotency_key": "blocked-dec-001", "row_version": d.rv()})
    assert r.status_code == 409 and r.json()["code"] == "decision_blocked" and {i["code"] for i in r.json()["issues"]} == {"stage_incomplete"}
    assert rows(EcDecision) == []
    assert d.bureau.get("/api/decisions/cases").json()["items"][0]["blocked"] is True


def test_the_decision_stage_waits_for_the_bureau_observations():
    d = make_dworld()
    d.to_queue()
    c = d.case()
    assert c["blockers"][0]["code"] == "bureau_observation_missing" and "record_decision" not in c["allowed_actions"]
    r = d.ec.post(f"{d.url()}/decision", {"outcome": "LOA", "reasons": "Not yet allowed: no Bureau observations.", "considered": ["scrutiny"], "idempotency_key": "early-dec-0001", "row_version": d.rv()})
    assert r.status_code == 409 and r.json()["code"] == "invalid_transition"  # the case is not READY_FOR_DECISION
    d.observe()
    assert d.case()["state"] == "READY_FOR_DECISION" and "record_decision" in d.case(d.ec)["allowed_actions"]


def test_a_non_demo_case_without_verified_decision_configuration_is_blocked_visibly():
    from app.domain import Classification

    d = make_dworld(classification=Classification.OFFICIAL)
    d.c.finish_scrutiny(official=True)
    c = d.case()
    assert c["policy"] is None and c["blockers"][0]["code"] == "no_decision_policy"
    d.observe()
    assert d.case()["blockers"][0]["code"] == "no_decision_policy"
    r = d.ec.post(f"{d.url()}/decision", {"outcome": "LOA", "reasons": "Cannot be recorded without configuration.", "considered": ["scrutiny"], "idempotency_key": "official-dec-001", "row_version": d.rv()})
    assert r.status_code == 409 and r.json()["code"] == "decision_blocked" and rows(EcDecision) == []


# ---- 3. reports and recommendations decide nothing ----------------------------------------------------------


def test_committee_reports_and_recommendations_never_produce_a_decision():
    d = make_dworld("evc_then_shc_conditional")
    d.c.finish_scrutiny()
    d.c.ready_visit()
    d.c.evc_to_report()
    d.c.complete_evc()
    d.c.evc_coord.post(f"{d.c.url('EVC')}/route/determine", {"stage_key": "shc", "reason": "The report calls for the compliance process."})
    d.c.shc_to_review()
    d.c.shc_finding()
    d.c.recommend(option="RECOMMEND_NEXT_STAGE")
    d.c.submit_recommendation()
    d.c.complete_shc()
    c = d.case()
    assert c["state"] == "AWAITING_OBSERVATIONS" and c["decisions"] == [] and c["current_decision"] is None and c["live_issuance"] is None
    assert c["supporting"]["evc"]["record"]["kind"] == "EVC_REPORT" and c["supporting"]["shc"]["record"]["option"] == "RECOMMEND_NEXT_STAGE"
    assert rows(EcDecision) == [] and d.w.refresh()["status"] == "SUBMITTED"
    assert d.status()["decision"] is None and d.status()["official_status"]["state"] == "AWAITING_DECISION"


# ---- 2. only authorised people -------------------------------------------------------------------------------


def test_only_the_executive_committee_can_record_a_decision_and_administrators_cannot():
    d = make_dworld()
    d.to_queue()
    d.observe()
    others = {}
    for role in (Role.PLATFORM_ADMIN, Role.RULES_ADMIN, Role.RULES_APPROVER, Role.AUDITOR, Role.SCRUTINY_OFFICER, Role.SCRUTINY_COORDINATOR, Role.EVC_COORDINATOR, Role.SHC_COORDINATOR, Role.SAC_COORDINATOR, Role.SAC_MEMBER):
        make_user(role, f"{role.value}@x.example.org")
        a = Api()
        a.login(f"{role.value}@x.example.org")
        others[role.value] = a
    body = {"outcome": "LOA", "reasons": "Attempted by someone without the authority.", "considered": ["scrutiny", "bureau"], "idempotency_key": "intruder-dec-1", "row_version": d.rv()}
    for name, client in {**others, "bureau": d.bureau, "council": d.council, "institution": d.w.a}.items():
        assert client.post(f"{d.url()}/decision", body).status_code == 403, name
    assert rows(EcDecision) == []
    with SessionLocal() as db:
        assert db.scalar(select(AuditEvent.id).where(AuditEvent.action == "security.permission_denied")) is not None
    assert d.decide("LOA", speaking_order=None)["state"] == "DECIDED"


def test_each_step_belongs_to_its_own_body():
    d = make_dworld()
    d.to_queue()
    assert d.ec.post(f"{d.url()}/observations", {"text": "The EC may not record Bureau observations.", "idempotency_key": "ec-obs-0001"}).status_code == 403
    assert d.council.post(f"{d.url()}/observations", {"text": "The Council may not record Bureau observations.", "idempotency_key": "co-obs-0001"}).status_code == 403
    d.observe()
    d.decide("LOR")
    assert d.ec.http.post(f"{d.url()}/issue", data={"idempotency_key": "ec-issue-001", "row_version": str(d.rv()), "document_reference": "X"}, headers=d.ec._csrf()).status_code == 403
    assert d.bureau.post(f"{d.url()}/ratification", {"outcome": "RATIFIED", "reasons": "The Bureau may not ratify.", "idempotency_key": "bu-rat-0001"}).status_code == 403
    assert d.ec.post(f"{d.url()}/ratification", {"outcome": "RATIFIED", "reasons": "The EC may not ratify its own decision.", "idempotency_key": "ec-rat-0001"}).status_code == 403
    assert d.bureau.post(f"{d.url()}/decision/correct", {"outcome": "LOD", "reasons": "Not the Bureau's to correct.", "considered": ["scrutiny"], "correction_reason": "Attempted correction.", "idempotency_key": "bu-cor-0001", "row_version": d.rv()}).status_code == 403
    assert d.ec.post(f"{d.url()}/close", {"row_version": d.rv()}).status_code == 403


# ---- 4. recording, auditing, immutability ------------------------------------------------------------------------


def test_a_recorded_decision_names_who_when_why_and_the_exact_records_considered():
    d = make_dworld("evc_only")
    d.c.finish_scrutiny()
    d.c.ready_visit()
    d.c.evc_to_report()
    d.c.complete_evc()
    d.observe()
    out = d.decide("LOR")
    dec = out["current_decision"]
    assert out["state"] == "DECIDED" and dec["outcome"] == "LOR" and dec["version_number"] == 1 and dec["kind"] == "ORIGINAL" and dec["decided_by"] == "Test executive_committee" and dec["decided_at"]
    refs = dec["supporting_refs"]
    assert refs["evc"]["record"]["kind"] == "EVC_REPORT" and refs["evc"]["record"]["content_hash"] and refs["scrutiny"]["completion"]["approved"] is False and refs["bureau"]["latest"]["sequence"] == 1
    assert refs["documents"][0]["item_key"] == "doc-main" and set(refs["considered"]) == {"scrutiny", "evc", "bureau"}
    assert dec["rule_version"]["classification"] == "DEMO" and dec["policy"]["classification"] == "DEMO" and len(dec["content_hash"]) == 64
    with SessionLocal() as db:
        acts = {a for (a,) in db.execute(select(AuditEvent.action).where(AuditEvent.action.like("committee.%")))}
        ev = db.scalar(select(AuditEvent).where(AuditEvent.action == "committee.decision_recorded"))
        assert "committee.decision_case_opened" in acts and "committee.bureau_observation_recorded" in acts and ev.detail["recorded_by_a_person"] is True and ev.detail["outcome"] == "LOR"
    assert [e["action"] for e in out["events"]] == ["decision_case_opened", "bureau_observation_recorded", "decision_recorded"]


@pytest.mark.parametrize(
    "kw",
    [
        {"outcome": "XYZ"},  # not a configured outcome
        {"reasons": "short"},
        {"outcome": "LOR", "speaking_order": None},  # a Letter of Rejection needs its Speaking Order (cl. 1.10(b))
        {"outcome": "LOR", "speaking_order": "too short"},
        {"considered": ["scrutiny"]},  # the Bureau's observations were not confirmed as considered
        {"considered": []},
    ],
)
def test_a_decision_must_be_complete_and_name_what_was_considered(kw):
    d = make_dworld()
    d.to_queue()
    d.observe()
    d.decide(expect=422, **kw)
    assert rows(EcDecision) == [] and d.case()["state"] == "READY_FOR_DECISION"


def test_a_decision_request_is_idempotent_and_stale_or_repeated_decisions_are_refused():
    d = make_dworld()
    d.to_queue()
    d.observe()
    body = {"outcome": "LOA", "reasons": "The Executive Committee decided to grant approval.", "considered": ["scrutiny", "bureau"], "idempotency_key": "dec-once-00001", "row_version": d.rv()}
    first = d.ec.post(f"{d.url()}/decision", body)
    again = d.ec.post(f"{d.url()}/decision", body)
    assert first.status_code == 201 and again.status_code == 200 and again.headers.get("X-Idempotent-Replay") == "true" and len(rows(EcDecision)) == 1
    other = d.ec.post(f"{d.url()}/decision", {**body, "idempotency_key": "dec-twice-0002", "outcome": "LOR", "speaking_order": "A speaking order of sufficient length."})
    assert other.status_code in (409,) and other.json()["code"] in ("invalid_transition", "stale_version") and len(rows(EcDecision)) == 1
    d2 = make_dworld.__wrapped__ if False else None
    assert d2 is None


def test_a_recorded_decision_cannot_be_changed_or_deleted():
    d = make_dworld()
    d.to_queue()
    d.observe()
    d.decide("LOA", speaking_order=None)
    with SessionLocal() as db:
        dec = db.scalar(select(EcDecision))
        dec.outcome = "LOR"
        with pytest.raises(ImmutableRecordError):
            db.flush()
        db.rollback()
        db.delete(db.scalar(select(EcDecision)))
        with pytest.raises(ImmutableRecordError):
            db.flush()
        db.rollback()
        obs = db.scalar(select(BureauObservation))
        obs.text = "Rewritten."
        with pytest.raises(ImmutableRecordError):
            db.flush()
        db.rollback()
        case = db.scalar(select(DecisionCase))
        case.state = "CLOSED"
        with pytest.raises(ImmutableRecordError):
            db.flush()
        db.rollback()
    assert d.case()["current_decision"]["outcome"] == "LOA"


def test_observations_are_append_only_and_idempotent():
    d = make_dworld()
    d.to_queue()
    d.observe(key="obs-key-000001")
    d.observe(key="obs-key-000001")
    d.observe("A further observation from the Bureau after more review.")
    assert [o["sequence"] for o in d.case()["observations"]] == [1, 2]
    d.observe("Too short", expect=422)
    d.decide("LOA", speaking_order=None)
    d.observe("Observations after a decision are refused.", expect=409)


# ---- corrections ---------------------------------------------------------------------------------------------


def test_an_authorised_correction_is_a_new_version_and_the_original_stays():
    d = make_dworld()
    d.to_queue()
    d.observe()
    d.decide("LOR")
    original = d.case()["current_decision"]
    d.correct("LOD", expect=201)
    c = d.case()
    vs = c["decisions"]
    assert [v["version_number"] for v in vs] == [1, 2] and vs[0]["outcome"] == "LOR" and vs[0]["content_hash"] == original["content_hash"] and vs[1]["kind"] == "CORRECTION" and vs[1]["supersedes_id"] == vs[0]["id"]
    assert vs[1]["correction_reason"].startswith("A clerical error") and c["current_decision"]["outcome"] == "LOD" and c["state"] == "DECIDED"
    d.correct("LOA", reason="short", expect=422)
    d.correct("LOA", reason="Needs a reason of at least ten characters.", expect=201)
    assert len(rows(EcDecision)) == 3


def test_correcting_an_issued_decision_supersedes_its_issue_and_keeps_the_appeal_window_anchor():
    d = make_dworld()
    d.to_queue()
    d.observe()
    d.decide("LOR")
    d.issue()
    first_issued = d.case()["first_issued_at"]
    assert d.status()["decision"]["outcome"] == "LOR"
    d.correct("LOD")
    c = d.case()
    assert c["state"] == "DECIDED" and c["live_issuance"] is None and [i["status"] for i in c["issuances"]] == ["SUPERSEDED"] and c["first_issued_at"] == first_issued
    st = d.status()
    assert st["decision"] is None and "corrected decision is being issued" in st["correction_pending"]
    d.issue("LOD/2026/0002")
    c = d.case()
    assert c["state"] == "ISSUED" and [i["status"] for i in c["issuances"]] == ["SUPERSEDED", "ISSUED"] and c["first_issued_at"] == first_issued  # the window never moves
    st = d.status()
    assert st["decision"]["outcome"] == "LOD" and st["decision"]["corrected"] is True and st["decision"]["correction_reason"] and len(st["decision"]["earlier_issues"]) == 1


# ---- issuing the official outcome ----------------------------------------------------------------------------


def test_issuing_needs_a_recorded_decision_and_a_reference_or_a_file_and_generates_no_letter():
    d = make_dworld()
    d.to_queue()
    d.observe()
    d.issue(expect=409)  # nothing recorded yet
    d.decide("LOR")
    d.issue(reference=None, expect=422)
    out = d.issue("LOR/2026/0001")
    assert out["state"] == "ISSUED" and out["live_issuance"]["document_reference"] == "LOR/2026/0001" and out["live_issuance"]["file"] is None
    assert out["first_issued_at"] is not None
    st = d.status()["decision"]
    assert st["outcome_label"] == "Letter of Rejection (LoR)" and st["document_reference"] == "LOR/2026/0001" and st["reasons"] and st["speaking_order"]
    assert "template" not in str(st).lower() and "signature" not in str(st).lower()
    with SessionLocal() as db:
        assert db.scalar(select(Notification.id).where(Notification.event_type == "OFFICIAL_OUTCOME_ISSUED", Notification.recipient_kind == "institution")) is not None


def test_an_issue_request_is_idempotent_and_the_official_file_is_private_to_the_institution_and_staff():
    d = make_dworld()
    d.to_queue()
    d.observe()
    d.decide("LOR")
    pdf = digital_pdf(["Official letter of rejection, as issued by the authority."])
    key = "issue-once-0001"
    d.issue(reference=None, key=key, file=("lor.pdf", pdf, "application/pdf"))
    again = d.bureau.http.post(f"{d.url()}/issue", data={"idempotency_key": key, "row_version": "1", "document_reference": "ignored"}, headers=d.bureau._csrf())
    assert again.status_code == 200 and again.headers.get("X-Idempotent-Replay") == "true" and len(rows(OfficialIssuance)) == 1
    f = d.status()["decision"]["file"]
    assert f["filename"] == "lor.pdf" and f["download"].startswith("/api/decisions/issuances/")
    assert d.w.a.get(f["download"]).status_code == 200 and d.bureau.get(f["download"]).status_code == 200 and d.ec.get(f["download"]).status_code == 200
    assert d.w.b.get(f["download"]).status_code in (403, 404) and d.c.shc1.get(f["download"]).status_code in (403, 404)
    bad = d.bureau.http.post(f"{d.url()}/issue", data={"idempotency_key": "issue-twice-002", "row_version": str(d.rv()), "document_reference": "X"}, headers=d.bureau._csrf())
    assert bad.status_code == 409  # already issued
    with SessionLocal() as db:
        assert db.scalar(select(OfficialIssuance.storage_key)) and "/" not in db.scalar(select(OfficialIssuance.storage_key))


def test_a_file_that_is_not_pdf_or_image_is_refused_and_nothing_is_issued():
    d = make_dworld()
    d.to_queue()
    d.observe()
    d.decide("LOR")
    d.issue(reference=None, file=("letter.txt", b"plain text", "text/plain"), expect=415)
    assert rows(OfficialIssuance) == [] and d.case()["state"] == "DECIDED"


def test_the_institution_sees_only_what_was_officially_issued():
    d = make_dworld()
    d.to_queue()
    d.observe("INTERNAL-BUREAU-OBSERVATION: sensitive working view of the case.")
    d.decide("LOR", reasons="Reasons stated for the institution to read in full.")
    before = d.status()
    assert before["decision"] is None and before["official_status"]["state"] == "AWAITING_DECISION"  # a recorded decision is not yet official
    d.issue()
    st = d.status()
    text = str(st)
    assert st["official_status"]["state"] == "EXECUTIVE_COMMITTEE_DECISION_ISSUED" and st["decision"]["reasons"].startswith("Reasons stated")
    assert "INTERNAL-BUREAU-OBSERVATION" not in text and "supporting_refs" not in text and "considered" not in text and "content_hash" not in text
    assert d.w.b.get(f"/api/applications/{d.w.app['id']}/decision").status_code == 404  # another institution
    assert d.bureau.get(f"/api/applications/{d.w.app['id']}/decision").status_code == 403  # staff use the staff endpoints


# ---- Council ratification ----------------------------------------------------------------------------------


def test_the_council_records_ratification_separately_with_a_configured_vocabulary():
    d = make_dworld()
    d.to_queue()
    d.observe()
    d.ratify(expect=409)  # nothing to ratify yet
    d.decide("LOA", speaking_order=None)
    d.ratify("MAYBE", expect=422)
    out = d.ratify("RATIFIED")
    assert out["live_ratification"]["outcome"] == "RATIFIED" and out["live_ratification"]["recorded_by"] == "Test council_member" and out["state"] == "DECIDED"  # ratification does not issue or close
    d.ratify(expect=409)  # already recorded
    out = d.ratify("NOT_RATIFIED", correction_reason="The earlier entry named the wrong decision version.")
    assert out["live_ratification"]["outcome"] == "NOT_RATIFIED" and [r["outcome"] for r in out["ratifications"]] == ["RATIFIED", "NOT_RATIFIED"] and out["ratifications"][1]["supersedes_id"] == out["ratifications"][0]["id"]
    with SessionLocal() as db:
        live = db.scalars(select(CouncilDecision).where(CouncilDecision.is_live.is_(True))).all()
        assert len(live) == 1 and db.scalar(select(Notification.id).where(Notification.event_type == "RATIFICATION_RECORDED")) is not None


def test_issue_can_be_configured_to_wait_for_ratification():
    from app.models_committee import CommitteePolicy

    d = make_dworld()
    with SessionLocal() as db:
        p = db.scalar(select(CommitteePolicy).where(CommitteePolicy.stage == "DEC"))
        import copy

        c = copy.deepcopy(p.content)
        c["ratification"]["issue_requires_ratification"] = True
        p.content = c
        db.commit()
    d.to_queue()
    d.observe()
    d.decide("LOA", speaking_order=None)
    r = d.bureau.http.post(f"{d.url()}/issue", data={"idempotency_key": "wait-rat-0001", "row_version": str(d.rv()), "document_reference": "LOA/1"}, headers=d.bureau._csrf())
    assert r.status_code == 409 and r.json()["code"] == "ratification_required"
    d.ratify("RATIFIED")
    assert d.issue("LOA/1")["state"] == "ISSUED"


# ---- closing -----------------------------------------------------------------------------------------------


def test_the_case_closes_only_when_ratification_is_in_and_the_appeal_window_has_run_out():
    d = make_dworld()
    d.to_queue()
    d.observe()
    d.decide("LOR")
    d.issue()
    c = d.case()
    assert {b["code"] for b in c["closure_blockers"]} == {"ratification_pending", "appeal_window_open"} and "close_case" not in c["allowed_actions"]
    r = d.close(expect=409)
    assert r["code"] == "closure_blocked"
    d.ratify("RATIFIED")
    assert {b["code"] for b in d.case()["closure_blockers"]} == {"appeal_window_open"}
    with SessionLocal() as db:  # time passes: the first issue is now older than the 7-day window
        from datetime import timedelta

        from app.models import utcnow

        db.execute(update(DecisionCase).values(first_issued_at=utcnow() - timedelta(days=9)))
        db.commit()
    c = d.case()
    assert c["closure_blockers"] == [] and "close_case" in c["allowed_actions"]
    out = d.close()
    assert out["state"] == "CLOSED" and out["post_decision"] is True and out["closure"]["official_outcome"] == "LOR" and out["closure"]["appeal"] is None
    with SessionLocal() as db:
        assert db.scalar(select(PostDecisionHandoff.id)) is not None
    assert d.status()["closed"] is True


def test_a_decision_that_is_not_appealable_closes_after_ratification_without_any_appeal_step():
    d = make_dworld()
    d.to_queue()
    d.observe()
    d.decide("LOA", speaking_order=None)
    d.issue("LOA/2026/0001")
    d.ratify("RATIFIED")
    assert d.case()["closure_blockers"] == []
    assert d.close()["state"] == "CLOSED"
    assert d.appeal_row() is None


def test_a_not_ratified_decision_keeps_the_case_open():
    d = make_dworld()
    d.to_queue()
    d.observe()
    d.decide("LOA", speaking_order=None)
    d.issue("LOA/2026/0001")
    d.ratify("NOT_RATIFIED")
    assert {b["code"] for b in d.case()["closure_blockers"]} == {"not_ratified"}


def test_reconciliation_closes_cases_whose_only_remaining_condition_was_time():
    from datetime import timedelta

    from app.config import get_settings
    from app.models import utcnow

    d = make_dworld()
    d.to_queue()
    d.observe()
    d.decide("LOR")
    d.issue()
    d.ratify("RATIFIED")
    with SessionLocal() as db:
        assert dsvc.finalise_expired(db, get_settings()) == 0  # the window is still open
        db.execute(update(DecisionCase).values(first_issued_at=utcnow() - timedelta(days=8)))
        db.commit()
    with SessionLocal() as db:
        assert dsvc.finalise_expired(db, get_settings()) == 1
        assert dsvc.finalise_expired(db, get_settings()) == 0  # idempotent
    assert d.case()["state"] == "CLOSED"


def test_the_application_itself_is_never_approved_or_rejected_by_any_of_this():
    d = make_dworld()
    d.to_queue()
    d.observe()
    d.decide("LOA", speaking_order=None)
    d.issue("LOA/2026/0001")
    with SessionLocal() as db:
        assert db.scalar(select(Application.status)) == "SUBMITTED"  # the application record is untouched: the outcome is a decision record
    assert d.ec.get(f"/api/applications/{d.w.app['id']}").status_code in (403, 404)


def test_decision_records_can_be_read_with_the_documents_behind_them_but_only_by_decision_roles():
    d = make_dworld()
    d.to_queue()
    ev = d.ec.get(f"{d.url()}/evidence")
    assert ev.status_code == 200 and ev.json()["items"][0]["item_key"] == "doc-main"
    doc_id = d.w.a.get(f"/api/applications/{d.w.app['id']}/documents").json()[0]["id"]
    assert d.council.get(f"/api/documents/{doc_id}").status_code == 200 and d.bureau.get(f"/api/documents/{doc_id}/download").status_code == 200
    assert d.c.shc1.get(f"/api/documents/{doc_id}").status_code == 404
    assert d.c.shc1.get(f"{d.url()}/evidence").status_code == 404  # holds evidence:read but is not a decision role: the case does not exist for them
    assert d.w.b.get(f"{d.url()}/evidence").status_code == 403
    _ = (io, uuid)
