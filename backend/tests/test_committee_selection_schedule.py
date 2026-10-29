"""Selection (the provider boundary) and scheduling for committee cases."""

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.domain import Classification, SelectionStatus
from app.models import AuditEvent
from app.models_committee import CommitteeAssignment, CommitteeCase, CommitteePolicy, CommitteeSchedule, SelectionRequest
from app.models_catalogue import ConfigurationGap
from app.models_scrutiny import Notification
from app.services import committee_policy, selection
from tests.committee_world import EVC_M1, EVC_M2, SHC_M1, future, make_cworld, past


def rows(model, **where):
    with SessionLocal() as db:
        q = select(model)
        for k, v in where.items():
            q = q.where(getattr(model, k) == v)
        return list(db.scalars(q))


class Scripted(selection.SelectionProvider):
    """A test double for an authorised external selection interface. Nothing like it is connected in the product."""

    mode = "EXTERNAL_PORTAL"
    label = "Scripted external provider (test double)"

    def __init__(self, submit=None, poll=None):
        self.submit_result = submit or selection.ProviderResult(SelectionStatus.REQUESTED, external_ref="EXT-1")
        self.poll_result = poll
        self.polls = 0

    def availability(self, config, settings):
        return selection.Availability(True, self.mode, self.label, "Scripted.")

    def submit(self, db, case, requested_ids, actor):
        return self.submit_result

    def poll(self, db, req):
        self.polls += 1
        return self.poll_result


def official_world(route="evc_only", with_policy=True):
    """A NON-demo case: DEMO committee policy and demo selection must not apply to it."""
    c = make_cworld(route, classification=Classification.OFFICIAL)
    if with_policy:  # what a verified official policy would look like (the content here is only a stand-in)
        with SessionLocal() as db:
            for stage, (key, title, content) in committee_policy.DEMO_SPECS.items():
                p = db.scalar(select(CommitteePolicy).where(CommitteePolicy.stage == stage.value))
                db.add(CommitteePolicy(stage=stage.value, key=key + "-official-stand-in", version_number=1, classification="OFFICIAL", status="published", title="stand-in", content=content, sources=p.sources, content_hash="y" * 64))
            db.commit()
    c.finish_scrutiny(official=True)
    return c


# ---- the provider boundary ---------------------------------------------------------------------------


def test_the_real_selection_provider_is_not_connected_and_says_so():
    c = official_world()
    d = c.case("EVC")
    assert d["selection"]["availability"] == {"available": False, "mode": "EXTERNAL_PORTAL", "label": "Official selection (not connected)", "notice": selection.NOT_CONNECTED_NOTICE, "demo": False}
    assert "request_selection" not in d["allowed_actions"]
    r = c.evc_coord.post(f"{c.url('EVC')}/selection", {"user_ids": [], "idempotency_key": "official-key-1"})
    assert r.status_code == 409 and r.json()["code"] == "selection_unavailable"
    assert rows(SelectionRequest) == [] and rows(CommitteeAssignment) == []  # no call was made, none is claimed
    with SessionLocal() as db:
        assert db.scalar(select(AuditEvent).where(AuditEvent.action == "committee.selection_unavailable")) is not None


def test_the_demonstration_selection_cannot_be_used_on_a_non_demo_case():
    c = official_world()
    r = c.evc_coord.post(f"{c.url('EVC')}/selection", {"user_ids": [c.user_id(EVC_M1)], "idempotency_key": "demo-on-official"})
    assert r.status_code == 409 and rows(CommitteeAssignment) == []
    assert c.evc_coord.get("/api/evc/assignable").json()["demo_only"] is True


def test_a_demonstration_selection_is_labelled_demo_everywhere():
    c = make_cworld("evc_only")
    c.finish_scrutiny()
    d = c.evc_select()
    assert d["state"] == "SCHEDULE_PENDING"
    req = d["selection"]["requests"][0]
    assert req["provider"] == "DEMO_MANUAL" and req["demo"] is True and req["status"] == "COMPLETED" and req["request_ref"].startswith("SEL-")
    assert d["selection"]["assignees"][0]["demo"] is True and d["selection"]["availability"]["notice"].startswith("Demonstration selection")
    assert "NOT the official AICTE" in d["selection"]["availability"]["notice"] and d["policy"]["banner"].startswith("DEMO ONLY")
    row = rows(SelectionRequest)[0]
    assert row.is_demo and row.result_source and row.config_ref["policy"]["classification"] == "DEMO" and row.config_ref["rule_version_id"]
    with SessionLocal() as db:
        n = db.scalar(select(Notification).where(Notification.event_type == "SELECTION_RECORDED", Notification.recipient_kind == "assignee"))
        assert "DEMONSTRATION" in n.body


def test_a_demo_selection_of_people_who_are_not_active_members_is_refused_and_assigns_nobody():
    c = make_cworld("evc_only")
    c.finish_scrutiny()
    r = c.evc_coord.post(f"{c.url('EVC')}/selection", {"user_ids": [c.user_id(SHC_M1)], "idempotency_key": "wrong-role-key"})
    assert r.status_code == 422 and rows(CommitteeAssignment) == [] and rows(SelectionRequest) == []
    assert c.case("EVC")["state"] == "SELECTION_PENDING"


def test_a_repeated_selection_request_returns_the_same_request():
    c = make_cworld("evc_only")
    c.finish_scrutiny()
    c.evc_select(key="same-key-123")
    r = c.evc_coord.post(f"{c.url('EVC')}/selection", {"user_ids": [c.user_id(EVC_M1)], "idempotency_key": "same-key-123"})
    assert r.status_code == 200 and r.headers.get("X-Idempotent-Replay") == "true"
    assert len(rows(SelectionRequest)) == 1 and len(rows(CommitteeAssignment)) == 1


def test_an_external_selection_waits_then_applies_a_verified_result(monkeypatch):
    c = official_world()
    prov = Scripted()
    monkeypatch.setattr(selection, "PROVIDER_OVERRIDE", prov)
    r = c.evc_coord.post(f"{c.url('EVC')}/selection", {"user_ids": [], "idempotency_key": "ext-key-001"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["state"] == "SELECTION_REQUESTED" and d["selection"]["requests"][0]["status"] == "REQUESTED" and d["selection"]["requests"][0]["external_ref"] == "EXT-1"
    assert d["selection"]["assignees"] == [] and "refresh_selection" in d["allowed_actions"]
    # nothing has arrived yet: still waiting, and visibly so
    d = c.evc_coord.post(f"{c.url('EVC')}/selection/refresh", {}).json()
    assert d["state"] == "SELECTION_REQUESTED" and prov.polls == 1
    # the result arrives
    prov.poll_result = selection.ProviderResult(SelectionStatus.COMPLETED, [uuid.UUID(c.user_id(EVC_M1)), uuid.UUID(c.user_id(EVC_M2))], source="External portal (test double)")
    d = c.evc_coord.post(f"{c.url('EVC')}/selection/refresh", {}).json()
    assert d["state"] == "SCHEDULE_PENDING" and len(d["selection"]["assignees"]) == 2 and d["selection"]["requests"][0]["result_source"] == "External portal (test double)"
    assert all(a["demo"] is False for a in d["selection"]["assignees"])


@pytest.mark.parametrize(
    "result,code",
    [
        (selection.ProviderResult(SelectionStatus.FAILED, error_code="provider_error", error_detail="The portal returned an error."), "provider_error"),
        (selection.ProviderResult(SelectionStatus.COMPLETED, [uuid.uuid4()]), "member_not_verified"),  # an identifier that is not a known member
        (selection.ProviderResult(SelectionStatus.COMPLETED, []), "too_few_members"),  # an incomplete result
    ],
)
def test_a_failed_or_unusable_selection_never_creates_an_assignment(monkeypatch, result, code):
    c = official_world()
    monkeypatch.setattr(selection, "PROVIDER_OVERRIDE", Scripted(submit=result))
    d = c.evc_coord.post(f"{c.url('EVC')}/selection", {"user_ids": [], "idempotency_key": "ext-key-fail"}).json()
    assert d["state"] == "SELECTION_PENDING" and d["selection"]["assignees"] == [] and rows(CommitteeAssignment) == []
    req = d["selection"]["requests"][0]
    assert req["status"] in ("FAILED", "INCOMPLETE") and req["error_code"] == code
    assert "request_selection" in d["allowed_actions"]  # it can be requested again
    with SessionLocal() as db:
        assert db.scalar(select(Notification.id).where(Notification.event_type == "SELECTION_FAILED")) is not None


def test_an_open_external_request_can_be_cancelled_with_a_reason(monkeypatch):
    c = official_world()
    monkeypatch.setattr(selection, "PROVIDER_OVERRIDE", Scripted())
    c.evc_coord.post(f"{c.url('EVC')}/selection", {"user_ids": [], "idempotency_key": "ext-key-cancel"})
    assert c.evc_coord.post(f"{c.url('EVC')}/selection/cancel", {"reason": "x"}).status_code == 422
    d = c.evc_coord.post(f"{c.url('EVC')}/selection/cancel", {"reason": "The portal request was raised in error."}).json()
    assert d["state"] == "SELECTION_PENDING" and d["selection"]["requests"][0]["status"] == "CANCELLED"


def test_reconciliation_applies_results_that_arrived_while_nobody_was_looking(monkeypatch):
    from app.config import get_settings

    c = official_world()
    prov = Scripted()
    monkeypatch.setattr(selection, "PROVIDER_OVERRIDE", prov)
    c.evc_coord.post(f"{c.url('EVC')}/selection", {"user_ids": [], "idempotency_key": "ext-key-recon"})
    with SessionLocal() as db:
        assert selection.reconcile(db, get_settings()) == 0  # nothing yet
    prov.poll_result = selection.ProviderResult(SelectionStatus.COMPLETED, [uuid.UUID(c.user_id(EVC_M1))], source="portal")
    with SessionLocal() as db:
        assert selection.reconcile(db, get_settings()) == 1
        assert selection.reconcile(db, get_settings()) == 0  # idempotent
    assert c.case("EVC")["state"] == "SCHEDULE_PENDING" and len(rows(CommitteeAssignment)) == 1


def test_with_no_connected_provider_reconciliation_leaves_requests_pending_and_visible():
    from app.config import get_settings

    c = make_cworld("evc_only")
    c.finish_scrutiny()
    with SessionLocal() as db:
        assert selection.reconcile(db, get_settings()) == 0


def test_a_non_demo_case_without_verified_committee_configuration_is_blocked_visibly_and_a_gap_is_recorded():
    c = official_world(with_policy=False)
    d = c.case("EVC")
    assert d["policy"] is None and d["blockers"][0]["code"] == "no_committee_policy"
    assert any("Blocked by configuration" in a for a in d["outstanding_actions"])
    assert c.case("EVC")["selection"]["availability"]["available"] is False
    with SessionLocal() as db:
        gap = db.scalar(select(ConfigurationGap).where(ConfigurationGap.reason == "incomplete"))
        assert gap is not None and gap.detail["missing"] == "committee_policy:EVC"
        assert db.scalar(select(Notification.id).where(Notification.event_type == "STAGE_BLOCKED")) is not None
    row = c.row("EVC")
    assert row.blockers and row.policy_snapshot is None
    # the blocker stops scheduling even if somebody were selected
    r = c.evc_coord.post(f"{c.url('EVC')}/schedule", {"start_local": future(), "timezone": "Asia/Kolkata", "mode": "ONLINE", "access_details": "x", "idempotency_key": "blocked-sched-1"})
    assert r.status_code == 409


# ---- scheduling ---------------------------------------------------------------------------------------


def make_ready():
    c = make_cworld("evc_only")
    c.finish_scrutiny()
    c.evc_select()
    return c


def test_a_schedule_needs_a_completed_selection():
    c = make_cworld("evc_only")
    c.finish_scrutiny()
    r = c.evc_coord.post(f"{c.url('EVC')}/schedule", {"start_local": future(), "timezone": "Asia/Kolkata", "mode": "ONLINE", "access_details": "x", "idempotency_key": "nosel-sched-1"})
    assert r.status_code == 409 and r.json()["code"] == "invalid_transition" and rows(CommitteeSchedule) == []


def test_proposing_then_confirming_a_schedule_records_who_when_and_the_time_zone():
    c = make_ready()
    d = c.schedule(start_local="2030-03-04T10:00")
    s = d["schedule"]["active"]
    assert s["status"] == "PROPOSED" and s["start_local"] == "2030-03-04T10:00" and s["timezone"] == "Asia/Kolkata" and s["mode"] == "ONLINE" and d["state"] == "SCHEDULE_PENDING"
    assert datetime.fromisoformat(s["start_at"].replace("Z", "+00:00")).astimezone(UTC).strftime("%H:%M") == "04:30"  # 10:00 in Asia/Kolkata is 04:30 UTC
    assert s["policy"]["classification"] == "DEMO"
    d = c.confirm()
    assert d["state"] == "SCHEDULED" and d["schedule"]["active"]["status"] == "CONFIRMED" and d["schedule"]["active"]["confirmed_at"]
    again = c.evc_coord.post(f"{c.url('EVC')}/schedule/{d['schedule']['active']['id']}/confirm", {"row_version": 1})
    assert again.status_code == 200  # a repeated confirmation changes nothing
    with SessionLocal() as db:
        assert db.scalar(select(Notification.id).where(Notification.event_type == "SCHEDULE_CONFIRMED", Notification.recipient_kind == "institution")) is not None


@pytest.mark.parametrize(
    "kw,status",
    [
        ({"start_local": past()}, 422),  # in the past
        ({"timezone": "Mars/Olympus"}, 422),  # unknown time zone
        ({"start_local": "2030-03-04T10:00+05:30"}, 422),  # an offset hidden in the string
        ({"start_local": "not a date"}, 422),
        ({"mode": "HYBRID"}, 422),  # reported in an unverified corrigendum: not permitted
        ({"mode": "TELEPATHY"}, 422),
        ({"mode": "PHYSICAL", "location": "Campus main gate"}, 422),  # a physical visit needs its justification
        ({"mode": "PHYSICAL", "mode_reason": "A court direction.", "location": None}, 422),  # and a place
        ({"mode": "ONLINE", "access_details": ""}, 422),
    ],
)
def test_schedule_validation_refuses_what_the_configuration_does_not_permit(kw, status):
    c = make_ready()
    c.schedule(expect=status, **kw)
    assert rows(CommitteeSchedule) == []


def test_hybrid_is_refused_with_the_reason_it_is_unverified():
    c = make_ready()
    r = c.evc_coord.post(f"{c.url('EVC')}/schedule", {"start_local": future(), "timezone": "Asia/Kolkata", "mode": "HYBRID", "idempotency_key": "hybrid-key-1"})
    assert "corrigendum" in r.json()["detail"] and "not permitted until it is verified" in r.json()["detail"]
    d = c.case("EVC")
    assert [m["key"] for m in d["policy"]["modes"]] == ["ONLINE", "PHYSICAL"] and d["policy"]["unverified_modes"][0]["key"] == "HYBRID"


def test_a_physical_visit_is_accepted_only_with_its_circumstance_and_location():
    c = make_ready()
    d = c.schedule(mode="PHYSICAL", mode_reason="Court direction for an in-person visit.", location="Main campus, administrative block", access_details=None)
    assert d["schedule"]["active"]["mode"] == "PHYSICAL" and d["schedule"]["active"]["mode_reason"].startswith("Court")


def test_a_duplicate_scheduling_request_creates_one_schedule():
    c = make_ready()
    c.schedule(idempotency_key="dup-sched-key-1")
    r = c.evc_coord.post(f"{c.url('EVC')}/schedule", {"start_local": future(), "timezone": "Asia/Kolkata", "mode": "ONLINE", "access_details": "x", "idempotency_key": "dup-sched-key-1"})
    assert r.status_code == 200 and r.headers.get("X-Idempotent-Replay") == "true"
    assert len(rows(CommitteeSchedule)) == 1
    other = c.evc_coord.post(f"{c.url('EVC')}/schedule", {"start_local": future(5), "timezone": "Asia/Kolkata", "mode": "ONLINE", "access_details": "x", "idempotency_key": "another-key-123"})
    assert other.status_code == 409 and other.json()["code"] == "schedule_exists"  # a second live schedule is impossible
    assert len(rows(CommitteeSchedule)) == 1


def test_rescheduling_preserves_the_previous_schedule_and_its_reason():
    c = make_ready()
    c.schedule(start_local="2030-03-04T10:00")
    c.confirm()
    r = c.evc_coord.post(f"{c.url('EVC')}/schedule/reschedule", {"start_local": "2030-03-06T11:30", "timezone": "Asia/Kolkata", "mode": "ONLINE", "access_details": "x", "idempotency_key": "resched-key-1", "reason": "The institution asked for a later date."})
    assert r.status_code == 201, r.text
    d = r.json()
    assert d["state"] == "SCHEDULE_PENDING" and d["schedule"]["active"]["status"] == "PROPOSED" and d["schedule"]["active"]["change_reason"].startswith("The institution")
    hist = d["schedule"]["history"]
    assert [h["status"] for h in hist] == ["SUPERSEDED", "PROPOSED"] and hist[0]["start_local"] == "2030-03-04T10:00" and hist[0]["closed_reason"].startswith("The institution")
    assert hist[1]["supersedes_id"] == hist[0]["id"]
    assert len(rows(CommitteeSchedule)) == 2
    c.confirm()
    assert c.case("EVC")["state"] == "SCHEDULED"
    with SessionLocal() as db:
        assert db.scalar(select(Notification.id).where(Notification.event_type == "SCHEDULE_CHANGED")) is not None


def test_a_reschedule_needs_a_reason_and_a_live_schedule():
    c = make_ready()
    body = {"start_local": future(4), "timezone": "Asia/Kolkata", "mode": "ONLINE", "access_details": "x", "idempotency_key": "resched-key-2", "reason": "A valid reason."}
    assert c.evc_coord.post(f"{c.url('EVC')}/schedule/reschedule", body).status_code == 409  # nothing to reschedule yet
    c.schedule()
    assert c.evc_coord.post(f"{c.url('EVC')}/schedule/reschedule", {**body, "reason": "x"}).status_code == 422


def test_cancelling_a_confirmed_schedule_returns_the_case_to_scheduling_and_keeps_the_record():
    c = make_ready()
    c.schedule()
    d = c.confirm()
    sid = d["schedule"]["active"]["id"]
    assert c.evc_coord.post(f"{c.url('EVC')}/schedule/{sid}/cancel", {"reason": "x"}).status_code == 422
    d = c.evc_coord.post(f"{c.url('EVC')}/schedule/{sid}/cancel", {"reason": "The members are not available that day."}).json()
    assert d["state"] == "SCHEDULE_PENDING" and d["schedule"]["active"] is None and d["schedule"]["history"][0]["status"] == "CANCELLED" and d["schedule"]["history"][0]["closed_reason"]
    assert c.schedule()["schedule"]["history"][-1]["status"] == "PROPOSED"  # a new proposal is possible; the old row stays


def test_a_stale_confirmation_is_refused():
    c = make_ready()
    d = c.schedule()
    sch = d["schedule"]["active"]
    r = c.evc_coord.post(f"{c.url('EVC')}/schedule/{sch['id']}/confirm", {"row_version": sch["row_version"] + 5})
    assert r.status_code == 409 and r.json()["code"] == "stale_version"


def test_a_member_with_another_live_visit_at_the_same_instant_is_a_conflict_and_nothing_else_is_assumed():
    c = make_ready()
    app2 = c.w.b.post("/api/applications", {"category_id": str(c.w.cat.category_id), "academic_year": "2026-27"}).json()
    with SessionLocal() as db:  # another application's visit, with the same member, already confirmed for 10:00 IST on that day
        first = c.row("EVC")
        case2 = CommitteeCase(application_id=uuid.UUID(app2["id"]), stage="EVC", rule_version_id=first.rule_version_id, state="SCHEDULED", trigger={"stage_key": "evc"})
        db.add(case2)
        db.flush()
        req = SelectionRequest(case_id=case2.id, request_ref="SEL-OTHER", provider="DEMO_MANUAL", is_demo=True, status="COMPLETED")
        db.add(req)
        db.flush()
        db.add(CommitteeAssignment(case_id=case2.id, user_id=uuid.UUID(c.user_id(EVC_M1)), selection_request_id=req.id, source="DEMO_MANUAL", is_demo=True))
        db.add(CommitteeSchedule(case_id=case2.id, kind="VISIT", sequence=1, status="CONFIRMED", start_at=datetime(2030, 5, 6, 4, 30, tzinfo=UTC), timezone="Asia/Kolkata", mode="ONLINE",
                                 created_by_id=uuid.UUID(c.user_id("evc.coord@test.example.org"))))
        db.commit()
    r = c.evc_coord.post(f"{c.url('EVC')}/schedule", {"start_local": "2030-05-06T10:00", "timezone": "Asia/Kolkata", "mode": "ONLINE", "access_details": "x", "idempotency_key": "conflict-key-01"})
    assert r.status_code == 409 and r.json()["code"] == "member_conflict" and "not anyone's general availability" in r.json()["detail"]
    assert c.case("EVC")["schedule"]["history"] == []
    # the same day at another time is not a conflict this system can see: it never claims to know anyone is free
    assert c.evc_coord.post(f"{c.url('EVC')}/schedule", {"start_local": "2030-05-06T14:00", "timezone": "Asia/Kolkata", "mode": "ONLINE", "access_details": "x", "idempotency_key": "conflict-key-02"}).status_code == 201


def test_reselecting_withdraws_the_assignments_and_any_live_schedule_without_deleting_them():
    c = make_ready()
    c.schedule()
    d = c.confirm()
    r = c.evc_coord.post(f"{c.url('EVC')}/reselect", {"reason": "x", "row_version": d["row_version"]})
    assert r.status_code == 422
    d = c.evc_coord.post(f"{c.url('EVC')}/reselect", {"reason": "A member declared a conflict of interest.", "row_version": c.rv("EVC")}).json()
    assert d["state"] == "SELECTION_PENDING" and d["selection"]["assignees"] == [] and d["schedule"]["active"] is None and d["schedule"]["history"][0]["status"] == "CANCELLED"
    a = rows(CommitteeAssignment)
    assert len(a) == 1 and a[0].revoked_at is not None and a[0].revoked_reason.startswith("A member")  # the record stays, revoked
    c.evc_select((EVC_M2,))
    assert [x["name"] for x in c.case("EVC")["selection"]["assignees"]] == ["Test evc_member"]


def test_scheduling_actions_need_the_coordinator_role_and_scope():
    c = make_ready()
    body = {"start_local": future(), "timezone": "Asia/Kolkata", "mode": "ONLINE", "access_details": "x", "idempotency_key": "auth-sched-key-1"}
    assert c.evc1.post(f"{c.url('EVC')}/schedule", body).status_code == 403  # an assigned member is not a coordinator
    assert c.shc_coord.post(f"{c.url('EVC')}/schedule", body).status_code == 403
    assert c.w.a.post(f"{c.url('EVC')}/schedule", body).status_code == 403
    assert rows(CommitteeSchedule) == []
    with SessionLocal() as db:
        assert db.scalar(select(AuditEvent.id).where(AuditEvent.action == "security.permission_denied")) is not None
