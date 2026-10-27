"""Phase 7: persistent clocks, the durable scheduler and alerts.

Time is controlled by passing `now` to the scheduler (the way a restarted worker would see it); no test sleeps."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from app.config import Settings, get_settings
from app.db import SessionLocal, engine
from app.guards import ImmutableRecordError
from app.models import utcnow
from app.models_clocks import Clock, ClockSegment, ScheduledWork
from app.models_compliance import Obligation
from app.models_scrutiny import Notification
from app.schemas_catalogue import DeadlinePolicyIn
from app.services import clock_policy, notifications, scheduler
from tests.compliance_world import OFFICER1, PW, clocks_of, make_pworld, notifs, pdeadlines, set_clock_due, work_of
from tests.evidence_world import drain


def utc(dt: datetime) -> datetime:
    """The instant in UTC. PostgreSQL hands back timestamps in the session's zone (IST on the development machine), SQLite hands back naive ones: compare instants, not renderings."""
    return dt.astimezone(UTC) if dt.tzinfo else dt.replace(tzinfo=UTC)


@pytest.fixture
def pw() -> PW:
    return make_pworld()


@pytest.fixture
def issued(pw: PW) -> PW:
    pw.to_issued()
    return pw


# ---- policy resolution: no policy, no invented date -------------------------------------------------------------


def _config(src_status: str, *, classification: str = "DEMO", **policy) -> dict:
    base = {"policy_key": "p", "label": "P", "deadline_type": "INTERNAL_SLA", "trigger_event": "t", "duration_value": 5, "duration_unit": "days", "calendar_convention": "CALENDAR", "source_id": "s1", "escalation": None}
    return {"rule_set": {"classification": classification}, "sources": [{"id": "s1", "title": "S", "verification_status": src_status, "clause_ref": None}], "deadlines": [{**base, **policy}]}


def _res(config: dict, **settings):
    s = Settings(environment="test", database_url="sqlite://", **settings)
    return clock_policy.resolutions_for(config, s, "t")[0]


def test_a_date_exists_only_for_a_usable_verified_policy():
    assert _res(_config("VERIFIED_OFFICIAL", classification="OFFICIAL")).usable
    assert _res(_config("DEMO_ONLY")).usable and _res(_config("DEMO_ONLY")).demo  # a DEMO source on a DEMO case with demo rules enabled, and labelled as such
    for why, cfg, extra in (
        ("no duration", _config("VERIFIED_OFFICIAL", duration_value=None, duration_unit=None), {}),
        ("working days", _config("VERIFIED_OFFICIAL", calendar_convention="WORKING"), {}),
        ("unstated convention", _config("VERIFIED_OFFICIAL", calendar_convention=None), {}),
        ("unit not days or hours", _config("VERIFIED_OFFICIAL", duration_unit="weeks"), {}),
        ("secondary source", _config("SECONDARY_UNVERIFIED"), {}),
        ("proposed policy", _config("PROPOSED_POLICY"), {}),
        ("demo source on an official case", _config("DEMO_ONLY", classification="OFFICIAL"), {}),
        ("demo rules disabled", _config("DEMO_ONLY"), {"allow_demo_rules": False}),
    ):
        r = _res(cfg, **extra)
        assert not r.usable and r.due_from(datetime(2026, 1, 1, tzinfo=UTC)) is None, why


def test_alert_schedules_pause_rules_and_escalation_roles_are_validated_and_never_guessed():
    ok, errs = clock_policy.parse_extras({"alerts": {"pre_due_hours": [24, 48], "at_due": True, "overdue_hours": [24]}, "pause": {"allowed": True, "reasons": ["x"]}, "escalate_to": ["regulation_bureau"]})
    assert not errs and ok.pre_due_hours == [48, 24] and ok.pause_allowed and ok.escalate_to == ["regulation_bureau"]
    empty, errs = clock_policy.parse_extras(None)
    assert not errs and empty.pre_due_hours == [] and not empty.pause_allowed and empty.escalate_to == []  # absent means none: no alerts, no pause, nobody escalated to
    for bad in ({"alerts": {"pre_due_hours": [0]}}, {"alerts": {"pre_due_hours": [1] * 3}}, {"alerts": {"pre_due_hours": "soon"}}, {"escalate_to": ["platform_admin"]}, {"pause": {"allowed": "yes"}}, {"whatever": 1}, ["x"]):
        parsed, errs = clock_policy.parse_extras(bad)
        assert errs, bad
        assert not parsed.escalate_to or parsed.escalate_to == []  # nothing malformed is used
    # an administrator or a decision-maker can never be configured as an escalation target
    assert clock_policy.validate_escalation({"escalate_to": ["executive_committee"]})


def test_the_rule_catalogue_refuses_a_malformed_alert_schedule(pw: PW):
    with pytest.raises(ValueError):
        DeadlinePolicyIn(policy_key="x", label="X", deadline_type="INTERNAL_SLA", trigger_event="t", escalation={"alerts": {"pre_due_hours": [0]}})


def test_an_unestablished_clock_never_carries_a_date_and_raises_no_alerts():
    # the stored clock and the database both refuse a date on a NOT_ESTABLISHED clock
    w = make_pworld(deadlines=lambda src: [DeadlinePolicyIn(policy_key="scr-sla", label="No duration", deadline_type="INTERNAL_SLA", trigger_event="scrutiny_case_opened", source_id=src, escalation={"alerts": {"pre_due_hours": [24]}})])
    w.w.upload()
    drain()
    w.w.submit()
    w.sync()
    (c,) = [c for c in clocks_of(kind="OFFICER_STAGE")]
    assert c.status == "NOT_ESTABLISHED" and c.due_at is None and "no duration" in c.not_established_reason.lower()
    assert work_of(c.id) == []
    with SessionLocal() as db:
        with pytest.raises(IntegrityError):
            db.execute(update(Clock).where(Clock.id == c.id).values(due_at=utcnow()))
            db.commit()


# ---- deriving clocks ------------------------------------------------------------------------------------------------


def test_statutory_and_internal_clocks_on_the_same_trigger_are_separate_rows(pw: PW):
    pw.w.upload()
    drain()
    pw.w.submit()
    stats = pw.sync()
    assert stats["clocks_created"] >= 2
    rows = {c.policy_key: c for c in clocks_of(kind="OFFICER_STAGE")}
    assert set(rows) == {"scr-sla", "scr-stat"}
    assert rows["scr-sla"].deadline_type == "INTERNAL_SLA" and rows["scr-stat"].deadline_type == "STATUTORY"
    assert utc(rows["scr-stat"].due_at) - utc(rows["scr-stat"].trigger_at) == timedelta(days=30)
    assert utc(rows["scr-sla"].due_at) - utc(rows["scr-sla"].trigger_at) == timedelta(days=5)
    assert rows["scr-sla"].id != rows["scr-stat"].id and rows["scr-sla"].subject_id == rows["scr-stat"].subject_id
    # running it again creates nothing and changes nothing
    before = {k: (v.due_at, v.row_version) for k, v in rows.items()}
    assert pw.sync()["clocks_created"] == 0
    after = {c.policy_key: (c.due_at, c.row_version) for c in clocks_of(kind="OFFICER_STAGE")}
    assert before == after


def test_a_clock_finishes_when_what_it_timed_has_happened_and_its_alerts_stop(pw: PW):
    pw.w.upload()
    drain()
    pw.w.submit()
    pw.sync()
    pw.w.start()
    r = pw.w.scrutiny.post(f"{pw.w.case_url}/complete", {"row_version": pw.w.case_row().row_version, "summary": "Scrutiny finished without open findings."})
    assert r.status_code == 200
    stats = pw.sync()
    assert stats["clocks_completed"] == 2
    for c in clocks_of(kind="OFFICER_STAGE"):
        assert c.status == "COMPLETED" and c.completion_reason == "first_scrutiny_round_over"
        assert all(w.status in ("CANCELLED", "SKIPPED_STALE") for w in work_of(c.id))


def test_a_new_rule_version_or_a_changed_internal_service_level_never_moves_an_existing_clock(pw: PW):
    from app.models_catalogue import DeadlinePolicy, RuleVersion
    from app.services import rules as rules_svc
    from app.models import User

    pw.w.upload()
    drain()
    pw.w.submit()
    pw.sync()
    before = {c.policy_key: (c.due_at, c.trigger_at, c.rule_version_id, c.policy_snapshot["duration_value"]) for c in clocks_of(kind="OFFICER_STAGE")}
    with SessionLocal() as db:
        rv = db.get(RuleVersion, pw.w.cat.version_id)
        author = db.scalar(select(User).where(User.email == "author@test.example.org"))
        approver = db.scalar(select(User).where(User.email == "approver@test.example.org"))
        v2 = rules_svc.new_draft_version(db, rv.rule_set_id, author, None)
        v2.change_summary = "Shorter internal service level for the first scrutiny round."
        for p in db.scalars(select(DeadlinePolicy).where(DeadlinePolicy.rule_version_id == v2.id)):
            if p.policy_key == "scr-sla":
                p.duration_value = 1  # the internal service level is shortened in the new version
        db.commit()
        v2 = rules_svc.submit_for_review(db, v2.id, v2.row_version, None, author, None, get_settings())
        db.commit()
        rules_svc.publish(db, v2.id, True, "supersede", approver, None, get_settings())
        db.commit()
    pw.sync()
    after = {c.policy_key: (c.due_at, c.trigger_at, c.rule_version_id, c.policy_snapshot["duration_value"]) for c in clocks_of(kind="OFFICER_STAGE")}
    assert before == after  # the statutory clock did not move, and neither did the internal one; both keep the version they were created under
    assert after["scr-sla"][3] == 5


def test_a_clock_cannot_be_rewritten_through_the_application(pw: PW):
    pw.w.upload()
    drain()
    pw.w.submit()
    pw.sync()
    with SessionLocal() as db:
        c = db.scalar(select(Clock).where(Clock.policy_key == "scr-stat"))
        c.due_at = c.due_at + timedelta(days=30)
        with pytest.raises(ImmutableRecordError):
            db.flush()
        db.rollback()
        c = db.scalar(select(Clock).where(Clock.policy_key == "scr-stat"))
        c.policy_snapshot = {"tampered": True}
        with pytest.raises(ImmutableRecordError):
            db.flush()


def test_the_appeal_window_is_a_statutory_clock_anchored_at_the_first_issue_and_ends_with_an_appeal(pw: PW):
    pw.to_issued(outcome="LOR", reference="LOR/2026/0001")
    pw.sync()
    (c,) = [c for c in clocks_of(kind="APPEAL")]
    assert c.deadline_type == "STATUTORY" and c.status == "RUNNING"
    case = pw.d.case_row()
    assert utc(c.due_at) - utc(case.first_issued_at) == timedelta(days=7) and utc(c.trigger_at) == utc(case.first_issued_at)
    # the institution sees its appeal deadline; it never sees internal service levels
    mine = pw.inst.get("/api/clocks").json()
    assert {i["kind"] for i in mine["items"]} == {"APPEAL"}
    pw.d.appeal()
    pw.sync()
    assert clocks_of(kind="APPEAL")[0].status == "COMPLETED" and clocks_of(kind="APPEAL")[0].completion_reason == "appeal_submitted"


def test_the_institution_response_clock_uses_the_date_stored_on_the_notice_and_ends_with_the_response():
    def with_response(src):
        return [DeadlinePolicyIn(policy_key="resp", label="Response period", deadline_type="INTERNAL_SLA", trigger_event="deficiency_notice_issued", source_id=src, clause_ref="t", duration_value=4, duration_unit="days",
                                 calendar_convention="CALENDAR", escalation={"alerts": {"pre_due_hours": [24]}})]
    pw = make_pworld(deadlines=with_response)
    pw.w.upload()
    drain()
    pw.w.submit()
    notice = pw.w.issued_notice()
    pw.sync()
    (c,) = clocks_of(kind="INSTITUTION_RESPONSE")
    from app.models_scrutiny import DeficiencyNotice

    with SessionLocal() as db:
        n = db.get(DeficiencyNotice, uuid.UUID(notice["id"]))
        assert utc(c.due_at) == utc(n.response_due_at) and c.status == "RUNNING"
    assert pw.inst.get("/api/clocks").json()["items"][0]["kind"] == "INSTITUTION_RESPONSE"
    pw.w.respond(notice["id"])
    pw.sync()
    assert clocks_of(kind="INSTITUTION_RESPONSE")[0].status == "COMPLETED"


# ---- pause and resume ------------------------------------------------------------------------------------------------


def _scrutiny_clocks(pw: PW) -> dict[str, Clock]:
    pw.w.upload()
    drain()
    pw.w.submit()
    pw.sync()
    return {c.policy_key: c for c in clocks_of(kind="OFFICER_STAGE")}


def test_a_clock_pauses_only_where_its_policy_allows_and_the_original_date_never_changes(pw: PW):
    cs = _scrutiny_clocks(pw)
    sla, stat = cs["scr-sla"], cs["scr-stat"]
    # the statutory clock's policy does not permit pausing: nobody can stop it
    r = pw.bureau.post(f"/api/clocks/{stat.id}/pause", {"reason": "Waiting for the institution.", "row_version": stat.row_version})
    assert r.status_code == 409 and r.json()["code"] == "pause_not_permitted"
    # the internal one does, but only for a listed reason, only by clock:manage, and not on a stale version
    base = {"reason": "Waiting for a clarification from the institution.", "row_version": sla.row_version}
    assert pw.bureau.post(f"/api/clocks/{sla.id}/pause", {**base, "reason_code": "something_else"}).status_code == 422
    assert pw.officer.post(f"/api/clocks/{sla.id}/pause", {**base, "reason_code": "awaiting_clarification"}).status_code == 403
    assert pw.bureau.post(f"/api/clocks/{sla.id}/pause", {**base, "reason_code": "awaiting_clarification", "row_version": 99}).status_code == 409
    original_due = sla.due_at
    out = pw.bureau.post(f"/api/clocks/{sla.id}/pause", {**base, "reason_code": "awaiting_clarification"}).json()
    assert out["status"] == "PAUSED" and out["effective_due_at"] is None
    assert all(w.status == "CANCELLED" for w in work_of(sla.id))  # no alert fires against a date that is no longer fixed
    assert clocks_of(kind="OFFICER_STAGE")[0].status in ("PAUSED", "RUNNING")
    # three hours later it resumes: the paused time is added, the original date is untouched
    with SessionLocal() as db:
        seg = db.scalar(select(ClockSegment).where(ClockSegment.clock_id == sla.id))
        db.execute(update(ClockSegment).where(ClockSegment.id == seg.id).values(paused_at=seg.paused_at - timedelta(hours=3)))
        db.commit()
    paused = pw.bureau.get(f"/api/clocks/{sla.id}").json()
    done = pw.bureau.post(f"/api/clocks/{sla.id}/resume", {"row_version": paused["row_version"]}).json()
    assert done["status"] == "RUNNING" and utc(datetime.fromisoformat(done["due_at"])) == utc(original_due)
    assert abs(done["paused_seconds"] - 3 * 3600) < 60
    assert utc(datetime.fromisoformat(done["effective_due_at"])) == utc(original_due) + timedelta(seconds=done["paused_seconds"])
    live = [w for w in work_of(sla.id) if w.status == "PENDING"]
    assert live and all(utc(w.planned_for_due) == utc(datetime.fromisoformat(done["effective_due_at"])) for w in live)  # re-planned against the new effective date
    # the statutory clock is untouched by any of this
    assert clocks_of(kind="OFFICER_STAGE")[1].due_at == stat.due_at if clocks_of(kind="OFFICER_STAGE")[1].policy_key == "scr-stat" else True


# ---- alerts: planning, sending, staleness ---------------------------------------------------------------------------------


def test_only_the_configured_alerts_are_planned(pw: PW):
    cs = _scrutiny_clocks(pw)
    assert work_of(cs["scr-stat"].id) == []  # no alert schedule configured: none invented
    kinds = sorted((w.alert_kind, w.offset_hours) for w in work_of(cs["scr-sla"].id))
    assert kinds == [("OVERDUE", 24), ("PRE_DUE", 24)]  # the policy has no at_due, so no "due now"
    again = pw.sync()
    assert again["planned"] == 0 and len(work_of(cs["scr-sla"].id)) == 2


def _defined_with_due(pw: PW, days: int = 10):
    o = pw.define(due=pw.specified_due(days))
    (c,) = clocks_of(o["id"])
    return o, c


def test_due_alerts_are_sent_once_to_the_right_people_and_recorded(issued: PW):
    pw = issued
    o, c = _defined_with_due(pw)
    due = utc(c.due_at)
    pw.sync(now=due - timedelta(hours=100))
    sent = [n for n in notifs(c.id) if n.event_type == "CLOCK_PRE_DUE"]
    assert len(sent) == 2 and {n.recipient_kind for n in sent} == {"institution", "verifier"}  # the institution's user and the assigned verifier
    assert all(n.delivery_state == "STORED_IN_APP" and n.external_delivery == "NOT_CONFIGURED" and n.clock_id == c.id and n.obligation_id is not None for n in sent)
    assert "no external message was sent" in sent[0].body
    done = [w for w in work_of(c.id) if w.status == "DONE"]
    assert len(done) == 1 and done[0].alert_kind == "PRE_DUE" and done[0].delivery == "STORED_IN_APP" and len(done[0].recipients) == 2 and done[0].attempts == 1 and done[0].policy_ref["policy_key"] == "obligation-due"
    pw.sync(now=due - timedelta(hours=100))  # the scheduler runs again: nothing new
    assert len([n for n in notifs(c.id) if n.event_type == "CLOCK_PRE_DUE"]) == 2
    pw.sync(now=due - timedelta(hours=12))  # the nearer reminder is now due
    assert len([n for n in notifs(c.id) if n.event_type == "CLOCK_PRE_DUE"]) == 4


def test_a_verified_obligation_produces_no_obsolete_reminders(issued: PW):
    pw = issued
    o, c = _defined_with_due(pw)
    pw.evidence(o)
    pw.review(o, "VERIFIED")
    pw.sync(now=utc(c.due_at) + timedelta(hours=30))
    assert notifs(c.id) == []
    assert all(w.status in ("CANCELLED", "SKIPPED_STALE") for w in work_of(c.id)) and clocks_of(o["id"])[0].status == "COMPLETED"


def test_stale_work_is_skipped_even_if_nothing_cancelled_it(issued: PW):
    """The subject finishes but the clock was never told (a lost event, a crash between two writes). The scheduler re-reads the persisted state before it sends."""
    pw = issued
    o, c = _defined_with_due(pw)
    with SessionLocal() as db:
        db.execute(update(Obligation).where(Obligation.id == uuid.UUID(o["id"])).values(status="VERIFIED"))
        db.commit()
    assert clocks_of(o["id"])[0].status == "RUNNING"
    pw.sync(now=utc(c.due_at) - timedelta(hours=100))
    assert notifs(c.id) == []
    assert clocks_of(o["id"])[0].status == "COMPLETED"


def test_evidence_awaiting_verification_alerts_the_verifier_not_the_institution(issued: PW):
    pw = issued
    o, c = _defined_with_due(pw)
    pw.evidence(o)
    pw.sync(now=utc(c.due_at) - timedelta(hours=100))
    sent = notifs(c.id)
    assert sent and {n.recipient_kind for n in sent} == {"verifier"}


def test_overdue_alerts_escalate_to_the_configured_role_once(issued: PW):
    pw = issued
    o, c = _defined_with_due(pw)
    pw.sync(now=utc(c.due_at) + timedelta(hours=30))
    pw.sync(now=utc(c.due_at) + timedelta(hours=31))
    overdue = [n for n in notifs(c.id) if n.event_type == "CLOCK_OVERDUE"]
    esc = [n for n in notifs(c.id) if n.event_type == "CLOCK_ESCALATED"]
    assert len(overdue) == 2 and len(esc) == 1 and esc[0].recipient_kind == "escalation"
    with SessionLocal() as db:
        from app.models import User

        assert db.get(User, esc[0].recipient_user_id).role == "regulation_bureau"
    # after a long outage only the nearest alerts are sent - no flood of earlier reminders
    assert not [n for n in notifs(c.id) if n.event_type == "CLOCK_PRE_DUE"]


def test_after_an_outage_only_the_nearest_missed_alert_is_sent(issued: PW):
    pw = issued
    o, c = _defined_with_due(pw)
    pw.sync(now=utc(c.due_at) - timedelta(hours=10))  # the scheduler was down: the 7-day and the 24-hour reminders are both late
    pre = [w for w in work_of(c.id) if w.alert_kind == "PRE_DUE"]
    assert {w.offset_hours: w.status for w in pre} == {168: "SKIPPED_STALE", 24: "DONE"}
    assert [w.outcome for w in pre if w.offset_hours == 168] == ["skipped_superseded_by_nearer_alert"]


def test_a_worker_that_dies_mid_job_loses_nothing(issued: PW):
    pw = issued
    o, c = _defined_with_due(pw)
    t0 = utc(c.due_at) - timedelta(hours=100)
    pw.sync(now=t0 - timedelta(days=3))  # nothing is due yet
    settings = get_settings()
    with SessionLocal() as db:
        claimed = scheduler.claim_next(db, "worker-that-dies", settings, now=t0)
        assert claimed is not None and claimed.status == "RUNNING" and claimed.attempts == 1
    # the process vanished; the row stays RUNNING with its lease. A fresh scheduler, later, recovers it.
    assert [w.status for w in work_of(c.id) if w.id == claimed.id] == ["RUNNING"]
    stats = pw.sync(now=t0 + timedelta(minutes=10))
    assert stats["reaped"] == 1 and stats["sent"] >= 1
    row = next(w for w in work_of(c.id) if w.id == claimed.id)
    assert row.status == "DONE" and row.attempts == 2
    assert len([n for n in notifs(c.id) if n.event_type == "CLOCK_PRE_DUE"]) == 2  # told once, not twice


def test_running_the_same_job_twice_never_duplicates_a_notification(issued: PW):
    pw = issued
    o, c = _defined_with_due(pw)
    t0 = utc(c.due_at) - timedelta(hours=100)
    pw.sync(now=t0)
    first = len(notifs(c.id))
    with SessionLocal() as db:
        db.execute(update(ScheduledWork).where(ScheduledWork.status == "DONE").values(status="PENDING", attempts=0, run_at=t0))  # as if two schedulers had both claimed it
        db.commit()
    pw.sync(now=t0)
    assert len(notifs(c.id)) == first


def test_failures_are_retried_within_bounds_and_stay_visible(issued: PW, monkeypatch):
    pw = issued
    o, c = _defined_with_due(pw)
    t = utc(c.due_at) - timedelta(hours=100)

    def broken(db, row):  # the notification store is down
        raise RuntimeError("store unavailable")

    monkeypatch.setattr(notifications, "_insert", broken)
    statuses = []
    for i in range(7):
        pw.sync(now=t + timedelta(hours=i))
        w = next(x for x in work_of(c.id) if x.alert_kind == "PRE_DUE" and x.offset_hours == 168)
        statuses.append((w.status, w.attempts))
    assert statuses[0] == ("RETRY_WAIT", 1) and statuses[-1][0] == "FAILED_EXHAUSTED" and statuses[-1][1] == get_settings().work_max_attempts  # bounded: it stops
    assert notifs(c.id) == []  # and nothing was claimed to have been sent
    health = pw.bureau.get("/api/clocks/health").json()
    assert health["failed"] and health["failed"][0]["last_error"] == "DeliveryError"
    assert pw.officer.get("/api/clocks/health").status_code == 403
    monkeypatch.undo()
    pw.sync(now=t + timedelta(hours=10))
    assert notifs(c.id) == []  # an exhausted alert is not retried by itself ...
    with SessionLocal() as db:
        assert scheduler.requeue_failed(db) >= 1  # ... only an operator's decision gives it another bounded set of attempts
    pw.sync(now=t + timedelta(hours=11))
    assert len([n for n in notifs(c.id) if n.event_type == "CLOCK_PRE_DUE"]) == 2


# ---- visibility ---------------------------------------------------------------------------------------------------------


def test_clocks_are_shown_by_role_and_never_across_institutions(issued: PW):
    pw = issued
    o, c = _defined_with_due(pw)
    pw.sync()
    kinds = lambda client: {i["kind"] for i in client.get("/api/clocks").json()["items"]}  # noqa: E731
    assert kinds(pw.inst) == {"APPEAL", "COMPLIANCE"}
    assert {"APPEAL", "COMPLIANCE"} <= kinds(pw.bureau)
    assert kinds(pw.officer) == {"COMPLIANCE"}
    assert kinds(pw.d.w.b) == set()
    assert pw.d.w.b.get(f"/api/clocks/{c.id}").status_code == 404
    assert pw.officer.get(f"/api/clocks/{clocks_of(kind='APPEAL')[0].id}").status_code == 404  # outside the officer's kinds
    assert pw.d.ec.get("/api/clocks").status_code == 403  # a role with no deadline permission
    assert pw.d.w.auditor.get("/api/clocks").status_code == 403
    detail = pw.bureau.get(f"/api/clocks/{c.id}").json()
    assert detail["alert_work"] and detail["events"][0]["action"] == "clock_created" and detail["policy"]["authority"] == "LoA para 4(b)"
    inst_detail = pw.inst.get(f"/api/clocks/{c.id}").json()
    assert "alert_work" not in inst_detail and "segments" not in inst_detail and "escalate_to" not in inst_detail


def test_overdue_and_due_soon_are_computed_when_asked_not_stored(issued: PW):
    pw = issued
    o, c = _defined_with_due(pw, days=30)
    assert pw.bureau.get("/api/clocks?due=overdue").json()["total"] == 0
    set_clock_due(c.id, datetime.now(UTC) - timedelta(hours=2))
    res = pw.bureau.get("/api/clocks?due=overdue").json()
    assert res["total"] == 1 and res["items"][0]["timing"] == "OVERDUE" and res["counts"]["overdue"] == 1
    set_clock_due(c.id, datetime.now(UTC) + timedelta(hours=10))
    assert pw.bureau.get("/api/clocks?due=soon").json()["total"] == 1


def test_a_deadline_passing_changes_no_approval_or_decision(issued: PW):
    """Overdue is an alert. Nothing revokes, suspends or penalises."""
    pw = issued
    o, c = _defined_with_due(pw)
    before = pw.d.case()
    pw.sync(now=utc(c.due_at) + timedelta(days=60))
    after = pw.d.case()
    assert before["state"] == after["state"] == "ISSUED" and before["row_version"] == after["row_version"]  # the decision case was not touched at all
    assert clocks_of(o["id"])[0].status == "RUNNING"  # the clock stays running and overdue until the obligation is verified, not "failed"
    from app.domain import ObligationStatus

    assert not any("REVOK" in s.value or "PENAL" in s.value or "SUSPEND" in s.value for s in ObligationStatus)


@pytest.mark.skipif(engine.dialect.name == "sqlite", reason="needs real row locking and concurrency (PostgreSQL)")
def test_several_schedulers_running_at_once_process_each_alert_exactly_once(issued: PW):
    import threading

    pw = issued
    o, c = _defined_with_due(pw, days=10)
    far = utc(c.due_at) + timedelta(days=2)
    with SessionLocal() as db:
        scheduler.plan_all(db, get_settings())
        db.commit()
    rows_before = len(work_of(c.id))
    assert rows_before >= 4
    outcomes: list[str] = []
    lock = threading.Lock()

    def run(name: str) -> None:
        with SessionLocal() as db:
            while True:
                w = scheduler.claim_next(db, name, get_settings(), now=far)
                if w is None:
                    return
                out = scheduler.process(db, w, name, get_settings(), now=far)
                with lock:
                    outcomes.append(out)

    threads = [threading.Thread(target=run, args=(f"sched-{i}",)) for i in range(4)]
    [t.start() for t in threads]
    [t.join(60) for t in threads]
    done = work_of(c.id)
    assert len(done) == rows_before and all(w.attempts == 1 and w.status in ("DONE", "SKIPPED_STALE") for w in done)  # claimed once each, by exactly one scheduler
    keys = [n.dedupe_key for n in notifs(c.id)]
    assert len(keys) == len(set(keys))  # and nobody was told twice


_ = (Notification, pdeadlines, OFFICER1)
