"""Formal deficiency notices: who may issue, what must be confirmed, duplicates, response periods, atomicity (acceptance 11-18, 34)."""

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.config import Settings
from app.db import SessionLocal, engine
from app.domain import CaseAction, Classification
from app.models import AuditEvent, User
from app.models_catalogue import ConfigurationGap, RuleVersion
from app.models_scrutiny import DeficiencyNotice, Notification, ScrutinyAssignment, ScrutinyCase, ScrutinyEvent, ScrutinyFinding, ScrutinyRound
from app.services import notifications, policy
from app.services import scrutiny as sc
from app.services import scrutiny_texts as texts
from tests.conftest import Api
from tests.evidence_world import REVIEWER, drain, make_world

world = pytest.fixture(make_world)


def ready(w):
    w.upload()
    drain()
    w.submit()
    drain()
    w.start()
    return w


def prepared(w):
    ready(w)
    return w.submit_notice(w.make_notice())


# ---- 11/12/13. who may prepare and issue; what is required ---------------------------------------------------------


def test_the_reviewer_can_prepare_a_notice_but_cannot_issue_it(world):
    n = prepared(world)
    pv = world.scrutiny.get(f"/api/scrutiny/notices/{n['id']}/preview")
    assert pv.status_code == 200  # the preparing reviewer may preview
    body = {"content_hash": pv.json()["content_hash"], "issue_reason": "I would like to issue my own notice.", "acknowledge_unsourced": True, "idempotency_key": "reviewer-key-1", "row_version": n["row_version"]}
    r = world.scrutiny.post(f"/api/scrutiny/notices/{n['id']}/issue", body)
    assert r.status_code == 403
    assert world.scrutiny.post(f"/api/scrutiny/notices/{n['id']}/return", {"reason": "Not allowed to do this"}).status_code == 403
    with SessionLocal() as db:
        assert db.scalar(select(DeficiencyNotice.status)) == "IN_REVIEW"
        denied = [e for e in db.scalars(select(AuditEvent).where(AuditEvent.action == "security.permission_denied", AuditEvent.actor_label == REVIEWER))]
    assert denied and denied[-1].detail["required"] == "notice:issue"


def test_no_other_role_can_issue_a_notice(world):
    n = prepared(world)
    pv = world.issuer.get(f"/api/scrutiny/notices/{n['id']}/preview").json()
    body = {"content_hash": pv["content_hash"], "issue_reason": "Trying with the wrong role.", "acknowledge_unsourced": True, "idempotency_key": "wrong-role-1", "row_version": pv["row_version"]}
    for who in (world.coordinator, world.a, world.auditor, world.scrutiny2):
        assert who.post(f"/api/scrutiny/notices/{n['id']}/issue", body).status_code in (403, 404)
    assert Api().post(f"/api/scrutiny/notices/{n['id']}/issue", body).status_code in (401, 403)
    with SessionLocal() as db:
        assert db.scalar(select(DeficiencyNotice.status)) == "IN_REVIEW"


def test_the_issuer_sees_a_notice_only_once_it_has_been_prepared_for_them_and_cannot_draft(world):
    ready(world)
    assert world.issuer.get(world.case_url).status_code == 404  # nothing has been put before an issuer yet
    f = world.eligible_finding()
    draft = world.make_notice([f["id"]])
    assert world.issuer.get(f"/api/scrutiny/notices/{draft['id']}/preview").status_code == 404  # a draft is the reviewer's own work
    assert world.issuer.post(f"{world.case_url}/notices", {"finding_ids": [f["id"]], "summary": "An issuer is not a drafter"}).status_code == 403
    world.submit_notice(draft)
    d = world.issuer.get(world.case_url).json()
    assert d["scope"] == ["issuer"] and d["allowed_actions"] == ["issue_notice", "return_notice"] and d["notices"][0]["status"] == "IN_REVIEW"
    assert world.issuer.get(f"{world.case_url}/evidence").status_code == 200  # to inspect the evidence behind the notice
    assert world.issuer.post(f"{world.case_url}/findings", {"issue_type": "other", "description": "Issuers do not record findings"}).status_code == 403
    assert world.issuer.get("/api/scrutiny/cases").json()["total"] == 1


def test_issuing_requires_the_exact_content_a_reason_and_a_notice_in_review(world):
    n = prepared(world)
    pv = world.issuer.get(f"/api/scrutiny/notices/{n['id']}/preview").json()
    url = f"/api/scrutiny/notices/{n['id']}/issue"
    good = {"content_hash": pv["content_hash"], "issue_reason": "Inspected and confirmed.", "acknowledge_unsourced": True, "idempotency_key": "issue-key-aaaa", "row_version": pv["row_version"]}
    assert world.issuer.post(url, {**good, "issue_reason": ""}).status_code == 422
    assert world.issuer.post(url, {**good, "issue_reason": "ok"}).status_code == 422
    assert world.issuer.post(url, {**good, "idempotency_key": "short"}).status_code == 422
    stale = world.issuer.post(url, {**good, "content_hash": "0" * 64})
    assert stale.status_code == 409 and stale.json()["code"] == "content_changed"
    assert world.issuer.post(url, {**good, "row_version": pv["row_version"] + 3}).status_code == 409
    unsourced = world.issuer.post(url, {**good, "acknowledge_unsourced": False})
    assert unsourced.status_code == 422 and unsourced.json()["code"] == "unsourced_items_need_acknowledgement"
    with SessionLocal() as db:
        assert db.scalar(select(DeficiencyNotice.status)) == "IN_REVIEW"  # none of those attempts issued anything
    assert world.issuer.post(url, good).status_code == 200


def test_a_draft_cannot_be_issued_and_the_issuer_can_return_a_notice_with_a_reason(world):
    ready(world)
    draft = world.make_notice()
    r = world.issuer.post(f"/api/scrutiny/notices/{draft['id']}/issue", {"content_hash": "0" * 64, "issue_reason": "Skipping the review step.", "idempotency_key": "draft-key-001", "row_version": 1})
    assert r.status_code == 404  # outside an issuer's scope until it is submitted for issuance
    n = world.submit_notice(draft)
    assert world.issuer.post(f"/api/scrutiny/notices/{n['id']}/return", {"reason": "no"}).status_code == 422
    back = world.issuer.post(f"/api/scrutiny/notices/{n['id']}/return", {"reason": "The correction requested is not specific enough."}).json()
    assert back["queue"]["state"] == "UNDER_SCRUTINY" and back["notices"][0]["status"] == "DRAFT" and "not specific enough" in back["notices"][0]["returned_reason"]
    again = world.scrutiny.get(world.case_url).json()
    assert again["allowed_actions"] and "submit_notice" in again["allowed_actions"]
    assert world.a.get("/api/notices").json() == []  # still nothing for the institution


def test_the_issued_record_carries_actor_role_authority_confirmation_and_frozen_content(world):
    n = prepared(world)
    world.issue(n["id"])
    row = world.case()["notices"][0]
    assert row["issued_by"] == "Test notice_issuer" and row["issuer_role"] == "notice_issuer" and row["issued_at"] and row["issue_reason"].startswith("Inspected")
    assert row["authority"]["capability"] == "notice:issue" and "NOT_ESTABLISHED" in row["authority"]["official_designation"]
    assert row["template"]["demo"] is True and "DEMO ONLY" in row["template"]["label"]
    with SessionLocal() as db:
        db_n = db.scalar(select(DeficiencyNotice))
        assert db_n.issue_confirmation["confirmed_content_hash"] == db_n.content_hash and db_n.content_snapshot["issued_at"] and db_n.content_snapshot["items"][0]["requested_correction"]
        assert db_n.content_snapshot["rule_version"]["classification"] == "DEMO" and db_n.content_snapshot["demo_notice"]
        ev = db.scalar(select(AuditEvent).where(AuditEvent.action == "scrutiny.notice_issued"))
        assert ev.actor_label == "issuer@test.example.org" and ev.detail["content_hash"] == db_n.content_hash and ev.detail["role"] == "notice_issuer" and "validity" not in json.dumps(ev.detail)


# ---- 16. repeated issuance ---------------------------------------------------------------------------------------


def test_a_retried_issue_request_returns_the_same_notice_and_creates_nothing_more(world):
    n = prepared(world)
    first = world.issue(n["id"], key="retry-key-0001")
    again = world.issue(n["id"], key="retry-key-0001")
    assert again.headers.get("X-Idempotent-Replay") == "true" and first.headers.get("X-Idempotent-Replay") is None
    assert first.json()["notices"][0]["issued_at"] == again.json()["notices"][0]["issued_at"]
    other = world.issue(n["id"], key="different-key-02", expect=409)
    assert other.json()["code"] == "already_issued"
    with SessionLocal() as db:
        assert len(db.scalars(select(DeficiencyNotice).where(DeficiencyNotice.status == "ISSUED")).all()) == 1
        assert len(db.scalars(select(ScrutinyEvent).where(ScrutinyEvent.action == "notice_issued")).all()) == 1
        assert len(db.scalars(select(AuditEvent).where(AuditEvent.action == "scrutiny.notice_issued")).all()) == 1
        assert len(db.scalars(select(Notification).where(Notification.event_type == "NOTICE_ISSUED")).all()) == 1


@pytest.mark.skipif(engine.dialect.name == "sqlite", reason="needs real row locking (PostgreSQL)")
def test_simultaneous_issue_requests_issue_the_notice_exactly_once():
    w = make_world()
    n = prepared(w)
    clients = []
    for _ in range(4):
        c = Api()
        c.login("issuer@test.example.org")
        clients.append(c)
    pv = clients[0].get(f"/api/scrutiny/notices/{n['id']}/preview").json()

    def go(i):
        key = "same-key-for-all" if i < 2 else f"other-key-{i}-xxxx"
        r = clients[i].post(f"/api/scrutiny/notices/{n['id']}/issue", {"content_hash": pv["content_hash"], "issue_reason": "Concurrent issuance test", "acknowledge_unsourced": True, "idempotency_key": key, "row_version": pv["row_version"]})
        return key, r.status_code

    with ThreadPoolExecutor(4) as pool:
        results = list(pool.map(go, range(4)))
    assert sorted(code for _, code in results).count(200) == 2 and all(k == "same-key-for-all" for k, c in results if c == 200)  # the winner and its own retry
    with SessionLocal() as db:
        assert len(db.scalars(select(DeficiencyNotice).where(DeficiencyNotice.status == "ISSUED")).all()) == 1
        assert len(db.scalars(select(Notification).where(Notification.event_type == "NOTICE_ISSUED")).all()) == 1


# ---- 15. response periods: never invented ---------------------------------------------------------------------------


def cfg(policies, classification="DEMO", sources=None):
    return {"rule_set": {"classification": classification}, "deadlines": policies, "sources": sources or [{"id": "s1", "title": "T", "verification_status": "VERIFIED_OFFICIAL"}]}


def pol(**kw):
    base = {"policy_key": "p", "deadline_type": "STATUTORY", "trigger_event": "deficiency_notice_issued", "duration_value": 15, "duration_unit": "days", "calendar_convention": "CALENDAR", "source_id": "s1", "clause_ref": "x"}
    return {**base, **kw}


@pytest.mark.parametrize(
    "config,state",
    [
        (cfg([]), "MISSING"),
        (cfg([pol(trigger_event="something_else")]), "MISSING"),
        (cfg([pol(), pol(policy_key="q")]), "AMBIGUOUS"),
        (cfg([pol(duration_value=None, duration_unit=None)]), "NO_DURATION"),
        (cfg([pol(calendar_convention="WORKING")]), "UNSUPPORTED"),
        (cfg([pol(calendar_convention=None)]), "UNSUPPORTED"),
        (cfg([pol()], sources=[{"id": "s1", "title": "T", "verification_status": "SECONDARY_UNVERIFIED"}]), "UNVERIFIED"),
        (cfg([pol()], sources=[{"id": "s1", "title": "T", "verification_status": "PROPOSED_POLICY"}]), "UNVERIFIED"),
        (cfg([pol()]), "COMPUTED"),
        (cfg([pol()], "DEMO", [{"id": "s1", "title": "T", "verification_status": "DEMO_ONLY"}]), "DEMO_COMPUTED"),
        (cfg([pol()], "OFFICIAL", [{"id": "s1", "title": "T", "verification_status": "DEMO_ONLY"}]), "UNVERIFIED"),  # a demo source never counts on an official case
    ],
)
def test_the_response_policy_resolves_to_a_due_date_only_when_it_is_usable(config, state):
    res = policy.resolve_response_policy(config, Settings(allow_demo_rules=True, environment="development"))
    assert res.state.value == state
    issued = datetime(2026, 1, 1, tzinfo=UTC)
    assert (res.due_at(issued) == issued + timedelta(days=15)) == (state in ("COMPUTED", "DEMO_COMPUTED"))
    if state not in ("COMPUTED", "DEMO_COMPUTED"):
        assert res.due_at(issued) is None


def test_demo_policy_is_not_usable_when_demo_rules_are_disabled_or_in_production():
    config = cfg([pol()], "DEMO", [{"id": "s1", "title": "T", "verification_status": "DEMO_ONLY"}])
    assert policy.resolve_response_policy(config, Settings(allow_demo_rules=False)).state.value == "UNVERIFIED"
    assert policy.resolve_response_policy(config, Settings(environment="production")).state.value == "UNVERIFIED"


def test_hours_are_supported_as_a_unit():
    res = policy.resolve_response_policy(cfg([pol(duration_value=36, duration_unit="hours")]), Settings())
    assert res.due_at(datetime(2026, 1, 1, tzinfo=UTC)) == datetime(2026, 1, 2, 12, tzinfo=UTC)


def test_a_demo_case_without_a_duration_still_issues_but_with_no_invented_due_date(world):
    n = prepared(world)
    world.issue(n["id"])
    row = world.case()["notices"][0]
    assert row["response_due_at"] is None and row["policy_resolution"]["state"] == "NO_DURATION"
    seen = world.a.get("/api/notices").json()[0]
    assert seen["response_due_at"] is None
    q = world.coordinator.get("/api/scrutiny/cases").json()["items"][0]
    assert q["due_at"] is None and q["due_basis"] is None


def test_a_demo_policy_with_a_duration_sets_a_clearly_labelled_due_date():
    w = make_world(response_days=7)
    n = prepared(w)
    pv = w.issuer.get(f"/api/scrutiny/notices/{n['id']}/preview").json()
    assert pv["policy"]["state"] == "DEMO_COMPUTED" and pv["policy"]["demo"] is True and pv["can_issue"]
    before = datetime.now(UTC)
    w.issue(n["id"])
    row = w.case()["notices"][0]
    due = datetime.fromisoformat(row["response_due_at"].replace("Z", "+00:00"))
    issued = datetime.fromisoformat(row["issued_at"].replace("Z", "+00:00"))
    due, issued = (d if d.tzinfo else d.replace(tzinfo=UTC) for d in (due, issued))
    assert due - issued == timedelta(days=7) and issued >= before - timedelta(seconds=5)
    seen = w.a.get(f"/api/notices/{n['id']}").json()
    assert seen["response_due_at"] and seen["due_note"].startswith("DEMO ONLY")
    q = w.coordinator.get("/api/scrutiny/cases").json()["items"][0]
    assert q["due_at"] and q["due_basis"].startswith("DEMO ONLY")


def official_assign(w):
    """What an authorised selection interface would deliver: an OFFICIAL_SELECTION assignment. (No such interface exists; this is a test double.)"""
    with SessionLocal() as db:
        case = db.scalar(select(ScrutinyCase))
        uid = db.scalar(select(User.id).where(User.email == REVIEWER))
        db.add(ScrutinyAssignment(case_id=case.id, user_id=uid, source="OFFICIAL_SELECTION", is_demo=False, made_by_id=None, made_by_label="authorised-selection-interface (test double)", selection_ref={"external_ref": "SEL-1"}))
        db.flush()
        sc.transition(db, case, CaseAction.ASSIGN)
        db.commit()


@pytest.mark.parametrize("days", [None, 15])
def test_a_non_demo_case_cannot_issue_a_notice_without_an_approved_template_and_a_verified_response_period(days):
    w = make_world(classification=Classification.OFFICIAL, response_days=days)
    w.upload()
    drain()
    w.submit(assign=False)
    official_assign(w)
    w.start()
    n = w.submit_notice(w.make_notice())
    pv = w.issuer.get(f"/api/scrutiny/notices/{n['id']}/preview").json()
    codes = {b["code"] for b in pv["blockers"]}
    assert "notice_template_unavailable" in codes and pv["can_issue"] is False
    assert ("response_policy_unresolved" in codes) == (days is None)
    assert pv["policy"]["state"] == ("NO_DURATION" if days is None else "COMPUTED") and pv["content"]["template"] is None
    body = {"content_hash": pv["content_hash"], "issue_reason": "Attempting to issue an official notice.", "acknowledge_unsourced": True, "idempotency_key": "official-key-1", "row_version": pv["row_version"]}
    r = w.issuer.post(f"/api/scrutiny/notices/{n['id']}/issue", body)
    assert r.status_code == 409 and {i["code"] for i in r.json()["issues"]} == codes
    with SessionLocal() as db:
        assert db.scalar(select(DeficiencyNotice.status)) == "IN_REVIEW" and db.scalar(select(DeficiencyNotice.response_due_at)) is None
        assert db.scalar(select(AuditEvent).where(AuditEvent.action == "notice.issue_blocked")) is not None
        gaps = db.scalars(select(ConfigurationGap)).all()
    assert (len(gaps) == 1 and gaps[0].reason == "incomplete" and gaps[0].detail["missing"] == "deficiency_notice_response_policy") == (days is None)
    assert w.a.get("/api/notices").json() == []  # nothing official was shown to the institution


def test_a_later_rule_version_never_moves_an_existing_deadline_or_rebinds_the_case():
    w = make_world(response_days=7)
    n = prepared(w)
    w.issue(n["id"])
    before = w.case()["notices"][0]
    rv_before = w.case_row().rule_version_id
    publish_changed_version(w, days=30)
    after = w.case()["notices"][0]
    assert after["response_due_at"] == before["response_due_at"] and after["policy_resolution"] == before["policy_resolution"]
    assert w.case_row().rule_version_id == rv_before
    # and a new application under the new version *would* use 30 days, proving the catalogue really did change
    with SessionLocal() as db:
        versions = db.scalars(select(RuleVersion).order_by(RuleVersion.version_number)).all()
    assert [v.status for v in versions] == ["retired", "published"]
    assert w.a.get(f"/api/notices/{n['id']}").json()["response_due_at"] == before["response_due_at"]


def publish_changed_version(w, days: int):
    from app.config import get_settings
    from app.models_catalogue import DeadlinePolicy
    from app.services import rules as svc

    with SessionLocal() as db:
        author = db.scalar(select(User).where(User.email == w.cat.author))
        approver = db.scalar(select(User).where(User.email == w.cat.approver))
        v2 = svc.new_draft_version(db, w.cat.rule_set_id, author, None)
        v2.change_summary = "Longer response period (test)"
        for p in db.scalars(select(DeadlinePolicy).where(DeadlinePolicy.rule_version_id == v2.id)):
            p.duration_value = days
        db.flush()
        v2 = svc.submit_for_review(db, v2.id, v2.row_version, None, author, None, get_settings())
        db.commit()
        svc.publish(db, v2.id, True, "superseding", approver, None, get_settings())
        db.commit()


# ---- drafting ----------------------------------------------------------------------------------------------------


def test_notice_items_carry_only_the_institution_facing_wording_and_can_be_edited_while_draft(world):
    ready(world)
    f1 = world.eligible_finding()
    f2 = world.eligible_finding()
    notice = world.make_notice([f1["id"], f2["id"]])
    assert [i["position"] for i in notice["items"]] == [1, 2] and notice["items"][0]["description"] == "Please show the validity period of the affiliation letter."
    ids = [i["id"] for i in notice["items"]]
    edited = world.scrutiny.patch(f"/api/scrutiny/notices/{notice['id']}", {"row_version": notice["row_version"], "summary": "Two points need your attention.",
                                                                          "items": [{"id": ids[1], "requested_correction": "Provide the complete, signed affiliation letter."}]})
    assert edited.status_code == 200
    n2 = edited.json()["notices"][0]
    assert [i["id"] for i in n2["items"]] == [ids[1]] and n2["items"][0]["position"] == 1 and "signed" in n2["items"][0]["requested_correction"] and n2["summary"].startswith("Two points")
    stale = world.scrutiny.patch(f"/api/scrutiny/notices/{notice['id']}", {"row_version": notice["row_version"], "summary": "Overwriting from a stale view"})
    assert stale.status_code == 409 and stale.json()["code"] == "stale_version"
    assert world.scrutiny.patch(f"/api/scrutiny/notices/{notice['id']}", {"row_version": n2["row_version"], "items": []}).status_code == 422  # a notice needs at least one item
    # the finding's internal description is not in the notice
    assert "affiliation letter does not show" not in json.dumps(n2)


def test_only_one_notice_can_be_in_preparation_and_findings_in_a_draft_are_protected(world):
    ready(world)
    f = world.eligible_finding()
    world.make_notice([f["id"]])
    r = world.scrutiny.post(f"{world.case_url}/notices", {"finding_ids": [f["id"]], "summary": "A second draft"})
    assert r.status_code == 409 and r.json()["code"] == "open_draft_exists"
    assert world.scrutiny.post(f"{world.case_url}/findings/{f['id']}/disposition", {"state": "RESOLVED", "reason": "Concluding a finding that is in a draft", "row_version": f["row_version"]}).json()["code"] == "finding_in_notice_draft"
    assert world.scrutiny.patch(f"{world.case_url}/findings/{f['id']}", {"row_version": f["row_version"], "description": "Edit while in a draft"}).json()["code"] == "finding_in_notice_draft"


def test_withdrawing_a_notice_returns_the_case_to_scrutiny_and_frees_its_findings(world):
    n = prepared(world)
    assert world.case_row().state == "AWAITING_NOTICE_DECISION"
    assert world.scrutiny.post(f"/api/scrutiny/notices/{n['id']}/withdraw", {"reason": "x"}).status_code == 422
    out = world.scrutiny.post(f"/api/scrutiny/notices/{n['id']}/withdraw", {"reason": "Raised in error; the letter is fine."}).json()
    assert out["queue"]["state"] == "UNDER_SCRUTINY" and out["notices"][0]["status"] == "WITHDRAWN" and out["findings"][0]["state"] == "OPEN"
    assert world.issuer.post(f"/api/scrutiny/notices/{n['id']}/return", {"reason": "Already withdrawn so nothing to return"}).status_code == 409
    assert world.a.get("/api/notices").json() == []
    again = world.make_notice([out["findings"][0]["id"]])  # a fresh draft is possible, as a new notice
    assert again["sequence"] == 2


# ---- 14/34. atomicity ---------------------------------------------------------------------------------------------


def test_if_persistence_fails_part_way_the_notice_is_not_issued_anywhere(world, monkeypatch):
    n = prepared(world)
    real = sc.log_event

    def boom(db, case, actor, action, **kw):
        if action == "notice_issued":
            raise RuntimeError("database went away")
        return real(db, case, actor, action, **kw)

    monkeypatch.setattr(sc, "log_event", boom)
    with pytest.raises(RuntimeError):
        world.issue(n["id"], key="atomic-key-0001")
    monkeypatch.undo()
    with SessionLocal() as db:
        notice = db.scalar(select(DeficiencyNotice))
        assert notice.status == "IN_REVIEW" and notice.issued_at is None and notice.content_snapshot is None and notice.idempotency_key is None
        assert db.scalar(select(ScrutinyCase.state)) == "AWAITING_NOTICE_DECISION"
        assert {f.state for f in db.scalars(select(ScrutinyFinding))} == {"OPEN"}
        assert db.scalar(select(ScrutinyRound.status).where(ScrutinyRound.number == 1)) == "OPEN"
        assert db.scalars(select(Notification).where(Notification.event_type == "NOTICE_ISSUED")).all() == []
        assert db.scalar(select(AuditEvent).where(AuditEvent.action == "scrutiny.notice_issued")) is None
    assert world.a.get("/api/notices").json() == []
    world.issue(n["id"], key="atomic-key-0001")  # and a retry afterwards succeeds normally
    assert world.case()["notices"][0]["status"] == "ISSUED"


# ---- 13. notifications must not undo the issuance ---------------------------------------------------------------


def test_a_notification_that_cannot_be_stored_does_not_undo_issuance_and_can_be_reconciled(world, monkeypatch):
    n = prepared(world)

    def fail(db, row):
        raise OSError("notification store unavailable")

    monkeypatch.setattr(notifications, "_insert", fail)
    world.issue(n["id"])
    monkeypatch.undo()
    assert world.case()["notices"][0]["status"] == "ISSUED"  # the notice is real, and the institution can read it
    assert world.a.get(f"/api/notices/{n['id']}").status_code == 200
    with SessionLocal() as db:
        assert db.scalars(select(Notification).where(Notification.event_type == "NOTICE_ISSUED")).all() == []
        failures = db.scalars(select(AuditEvent).where(AuditEvent.action == "notification.persist_failed")).all()
    assert failures and failures[0].detail["event"] == "NOTICE_ISSUED"
    with SessionLocal() as db:
        assert notifications.reconcile(db) == 1
        db.commit()
        assert notifications.reconcile(db) == 0  # idempotent
    assert len(world.a.get("/api/notifications").json()["items"]) == 1


def test_notifying_twice_for_the_same_event_creates_one_notification(world):
    n = prepared(world)
    world.issue(n["id"])
    with SessionLocal() as db:
        notice = db.scalar(select(DeficiencyNotice))
        assert texts.notice_issued_notification(db, notice) == 0 and texts.notice_issued_notification(db, notice) == 0
    assert world.a.get("/api/notifications").json()["total"] == 1

