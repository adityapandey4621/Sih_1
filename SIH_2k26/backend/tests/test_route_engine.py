"""The route engine: which stage follows is read from the frozen route. Never a universal EVC-plus-SHC sequence; never a guess."""

import uuid

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.models import AuditEvent
from app.models_applications import Application
from app.models_committee import CommitteeCase, CommitteePolicy, DecisionHandoff, RouteTransition
from app.models_scrutiny import Notification, ScrutinyCase, StageHandoff
from app.services import stages
from app.services.errors import ServiceError
from tests.committee_world import make_cworld, stage


def count(model) -> int:
    with SessionLocal() as db:
        return len(db.scalars(select(model)).all())


def handoff_row(c) -> DecisionHandoff | None:
    with SessionLocal() as db:
        return db.scalar(select(DecisionHandoff).where(DecisionHandoff.application_id == uuid.UUID(c.w.app["id"])))


def test_a_required_evc_stage_is_activated_when_scrutiny_completes_and_nothing_else_is():
    c = make_cworld("evc_only")
    c.finish_scrutiny()
    assert c.row("EVC") is not None and c.row("SHC") is None
    assert handoff_row(c) is None  # a required stage is still outstanding
    d = c.case("EVC")
    assert d["trigger"]["stage_key"] == "evc" and d["trigger"]["from_stage_key"] == "scrutiny" and d["trigger"]["source"] == "scrutiny_handoff"
    assert d["trigger"]["scrutiny_outcome"]["approved"] is False and d["trigger"]["rule_version"]["classification"] == "DEMO"
    assert d["trigger"]["route_clause"]["label"] == "EVC"


def test_a_route_with_no_committee_stage_bypasses_both_and_hands_off_to_the_decision_stage():
    c = make_cworld("scrutiny_only")
    c.finish_scrutiny()
    assert c.row("EVC") is None and c.row("SHC") is None
    h = handoff_row(c)
    assert h is not None and h.status == "READY_FOR_DECISION_STAGE" and h.basis["decided"] is False and [s["stage_key"] for s in h.basis["stages"]] == ["scrutiny"]
    assert Application is not None and c.w.refresh()["status"] == "SUBMITTED"  # nothing was approved or rejected


def test_a_conditional_evc_stage_waits_for_an_authorised_determination():
    c = make_cworld("evc_conditional")
    c.finish_scrutiny()
    assert c.row("EVC") is None and handoff_row(c) is None
    case = c.w.case()
    assert case["handoff"]["status"] == "AWAITING_DETERMINATION"
    # a person determines that EVC applies: the case is created, with the determination on record
    r = c.w.coordinator.post(f"{c.w.case_url}/handoff/determine", {"stage_key": "evc", "reason": "The category requires a visit."})
    assert r.status_code == 200
    d = c.case("EVC")
    assert d["trigger"]["source"] == "scrutiny_handoff" and d["trigger"]["determination"]["reason"] == "The category requires a visit."


def test_determining_that_a_conditional_evc_stage_does_not_apply_bypasses_it_cleanly():
    c = make_cworld("evc_conditional")
    c.finish_scrutiny(determine=None)
    assert c.row("EVC") is None and c.row("SHC") is None
    assert handoff_row(c) is not None


def test_scrutiny_can_lead_straight_to_shc_without_an_evc():
    c = make_cworld("shc_only")
    c.finish_scrutiny()
    assert c.row("EVC") is None and c.row("SHC") is not None
    assert c.case("SHC")["state"] == "PREPARATION"


def test_evc_required_does_not_make_shc_required():
    c = make_cworld("evc_only")
    c.finish_scrutiny()
    c.ready_visit()
    c.evc_to_report()
    d = c.complete_evc()
    assert d["next"]["status"] == "NO_FURTHER_STAGE" and c.row("SHC") is None
    assert handoff_row(c) is not None
    assert [s["stage_key"] for s in handoff_row(c).basis["stages"]] == ["scrutiny", "evc"]
    assert handoff_row(c).basis["stages"][1]["record"]["kind"] == "EVC_REPORT"


def test_after_evc_a_conditional_shc_is_activated_only_by_a_recorded_determination():
    c = make_cworld("evc_then_shc_conditional")
    c.finish_scrutiny()
    c.ready_visit()
    c.evc_to_report()
    d = c.complete_evc()
    assert d["next"]["status"] == "AWAITING_DETERMINATION" and c.row("SHC") is None and handoff_row(c) is None
    assert d["allowed_actions"].count("determine_next") == 1
    r = c.evc_coord.post(f"{c.url('EVC')}/route/determine", {"stage_key": "shc", "reason": "The visit report flagged items for compliance."})
    assert r.status_code == 200, r.text
    s = c.case("SHC")
    assert s["state"] == "PREPARATION" and s["trigger"]["source"] == "determination" and s["trigger"]["determination"]["reason"].startswith("The visit report")
    assert handoff_row(c) is None  # SHC is now required, so the case is not ready for decision


def test_after_evc_a_conditional_shc_can_be_recorded_as_not_applicable():
    c = make_cworld("evc_then_shc_conditional")
    c.finish_scrutiny()
    c.ready_visit()
    c.evc_to_report()
    c.complete_evc()
    r = c.evc_coord.post(f"{c.url('EVC')}/route/determine", {"stage_key": None, "reason": "Nothing in the report calls for a compliance process."})
    assert r.status_code == 200, r.text
    assert c.row("SHC") is None and handoff_row(c) is not None
    # a second determination is refused: the route moved on
    r = c.evc_coord.post(f"{c.url('EVC')}/route/determine", {"stage_key": "shc", "reason": "Changing my mind later."})
    assert r.status_code == 409 and r.json()["code"] == "not_awaiting_determination"


def test_a_determination_must_name_a_stage_in_the_applications_own_route_and_give_a_reason():
    c = make_cworld("evc_then_shc_conditional")
    c.finish_scrutiny()
    c.ready_visit()
    c.evc_to_report()
    c.complete_evc()
    assert c.evc_coord.post(f"{c.url('EVC')}/route/determine", {"stage_key": "visit-everyone", "reason": "Not in the route at all."}).status_code == 422
    assert c.evc_coord.post(f"{c.url('EVC')}/route/determine", {"stage_key": "shc", "reason": "x"}).status_code == 422
    assert c.row("SHC") is None


def test_the_combined_sac_shc_stage_activates_only_the_shc_process_never_an_appeal():
    c = make_cworld("evc_then_combined")
    c.finish_scrutiny()
    c.ready_visit()
    c.evc_to_report()
    c.complete_evc()
    r = c.evc_coord.post(f"{c.url('EVC')}/route/determine", {"stage_key": "sac-shc-aa", "reason": "Shortcomings remain after the visit."})
    assert r.status_code == 200, r.text
    d = c.case("SHC")
    assert d["trigger"]["stage_key"] == "sac-shc-aa" and d["state"] == "PREPARATION"


def test_a_ready_for_decision_handoff_cannot_be_created_while_a_required_stage_is_incomplete():
    c = make_cworld("evc_only")
    c.finish_scrutiny()
    with SessionLocal() as db:
        app = db.get(Application, uuid.UUID(c.w.app["id"]))
        with pytest.raises(ServiceError) as e:
            stages.create_decision_handoff(db, app, None, None)
        assert e.value.code == "required_stage_incomplete" and "EVC" in e.value.message
        db.rollback()
    assert handoff_row(c) is None


def _cfg(*stages_):
    return {"rule_set": {"key": "s", "classification": "DEMO"}, "version": {"number": 1}, "route": [dict(stage_key=k, label=k, next_stage_keys=n, is_conditional=cond, condition_description=None, clause_ref=None) for k, n, cond in stages_]}


@pytest.mark.parametrize(
    "config,from_key,status",
    [
        (_cfg(("scrutiny", ["evc"], False)), "evc", "EXCEPTION"),  # the stage is not in the route at all
        (_cfg(("evc", [], False), ("evc", [], False)), "evc", "EXCEPTION"),  # the stage appears twice
        (_cfg(("evc", ["shc"], False)), "evc", "EXCEPTION"),  # it names a next stage that does not exist
        (_cfg(("evc", [], False)), "evc", "NO_FURTHER_STAGE"),
        (_cfg(("evc", ["shc"], False), ("shc", [], False)), "evc", "QUEUED"),
        (_cfg(("evc", ["shc"], False), ("shc", [], True)), "evc", "AWAITING_DETERMINATION"),
        (_cfg(("evc", ["shc", "sac-shc-aa"], False), ("shc", [], False), ("sac-shc-aa", [], False)), "evc", "AWAITING_DETERMINATION"),
    ],
)
def test_the_route_resolver_never_guesses(config, from_key, status):
    from app.services import routing

    assert routing.resolve_after(config, from_key).status.value == status


def test_a_route_the_catalogue_would_publish_cannot_dangle_so_a_broken_route_never_reaches_a_case():
    with pytest.raises(ServiceError) as e:
        make_cworld([stage("scrutiny", "Scrutiny", ["evc"])])  # names a stage that does not exist: refused at review, long before any application
    assert e.value.code == "incomplete_configuration"


def test_when_the_route_cannot_be_applied_after_evc_the_case_waits_in_a_controlled_exception(monkeypatch):
    from app.domain import HandoffStatus
    from app.services import routing

    c = make_cworld("evc_only")
    c.finish_scrutiny()
    c.ready_visit()
    c.evc_to_report()
    monkeypatch.setattr(routing, "resolve_after", lambda config, key: routing.RouteResolution(HandoffStatus.EXCEPTION, "The approved route cannot be applied.", key, basis={}))
    d = c.complete_evc()
    assert d["state"] == "COMPLETED" and d["next"]["status"] == "EXCEPTION"
    assert c.row("SHC") is None and handoff_row(c) is None
    with SessionLocal() as db:
        assert db.scalar(select(RouteTransition.status)) == "EXCEPTION"
        assert db.scalar(select(Notification.id).where(Notification.event_type == "STAGE_BLOCKED")) is not None


def test_activation_is_idempotent_and_reconciliation_recovers_a_stage_that_was_never_created(monkeypatch):
    c = make_cworld("evc_only")
    real = stages.on_scrutiny_handoff
    monkeypatch.setattr(stages, "on_scrutiny_handoff", lambda *a, **k: None)  # the activation step is skipped (a restart, an older release)
    c.finish_scrutiny()
    assert c.row("EVC") is None
    with SessionLocal() as db:
        assert db.scalar(select(StageHandoff.status)) == "QUEUED"
    monkeypatch.setattr(stages, "on_scrutiny_handoff", real)
    with SessionLocal() as db:
        stages.reconcile(db)
    first = c.row("EVC")
    assert first is not None
    with SessionLocal() as db:
        stages.reconcile(db)
        app = db.get(Application, uuid.UUID(c.w.app["id"]))
        again = stages.activate(db, app, "evc", from_stage_key="scrutiny", source="test", actor=None, request=None)
        assert again.id == first.id
        db.commit()
    assert count(CommitteeCase) == 1  # one EVC case per application, however often activation is requested


def test_a_later_policy_never_changes_a_running_case():
    c = make_cworld("evc_only")
    c.finish_scrutiny()
    before = c.case("EVC")["policy"]
    with SessionLocal() as db:  # a new DEMO policy version is published after the case started
        old = db.scalar(select(CommitteePolicy).where(CommitteePolicy.stage == "EVC"))
        old.status = "retired"
        db.flush()
        db.add(CommitteePolicy(stage="EVC", key=old.key, version_number=2, classification="DEMO", status="published", title="DEMO v2", content={**old.content, "checklist": []}, sources=old.sources, content_hash="x" * 64))
        db.commit()
    after = c.case("EVC")["policy"]
    assert after["version"] == before["version"] == 1 and after["checklist"] == before["checklist"] and len(after["checklist"]) == 3
    assert c.case("EVC")["trigger"]["rule_version"]["number"] == 1


def test_stage_activation_is_audited_and_notifies_the_stage_coordinators():
    c = make_cworld("evc_only")
    c.finish_scrutiny()
    with SessionLocal() as db:
        assert db.scalar(select(AuditEvent).where(AuditEvent.action == "committee.stage_activated")) is not None
        kinds = {(n.event_type, n.recipient_kind) for n in db.scalars(select(Notification))}
    assert ("STAGE_ACTIVATED", "coordinator") in kinds and ("STAGE_ACTIVATED", "institution") in kinds
    assert c.w.a.get(f"/api/applications/{c.w.app['id']}/committee").json()["evc"]["label"] == "Being arranged"


def test_the_scrutiny_case_is_untouched_by_later_stages():
    c = make_cworld("evc_only")
    c.finish_scrutiny()
    c.ready_visit()
    with SessionLocal() as db:
        assert db.scalar(select(ScrutinyCase.state)) == "SCRUTINY_COMPLETED"
