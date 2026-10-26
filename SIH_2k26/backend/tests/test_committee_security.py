"""Who may call what (every role against every committee endpoint, over HTTP), scope, failure injection, concurrency and recovery."""

import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import select

from app.db import SessionLocal, engine
from app.models import AuditEvent
from app.models_committee import CommitteeCase, CommitteeReport, CommitteeSchedule, DecisionHandoff, RouteTransition, SelectionRequest
from app.models_scrutiny import Notification, ScrutinyCase
from app.rbac import Role
from app.services import committee_texts as ctexts
from app.services import notifications as nt
from app.services import stages
from tests.committee_world import make_cworld
from tests.conftest import Api, make_user
from tests.test_notices import publish_changed_version

# ---- the role x endpoint matrix -------------------------------------------------------------------------

EVC_C, EVC_W = {Role.EVC_COORDINATOR}, {Role.EVC_MEMBER}
SHC_C, SHC_W = {Role.SHC_COORDINATOR}, {Role.SHC_MEMBER}
EVIDENCE_ROLES = EVC_C | EVC_W | SHC_C | SHC_W | {Role.SCRUTINY_OFFICER, Role.NOTICE_ISSUER}
ID = "00000000-0000-4000-8000-000000000001"
OBS = {"status": "OBSERVED", "note": "Seen as described.", "row_version": None}
SCHED = {"start_local": "2031-01-01T10:00", "timezone": "Asia/Kolkata", "mode": "ONLINE", "access_details": "x", "idempotency_key": "matrix-key-0001"}

MATRIX: list[tuple[str, str, dict | None, set]] = [
    ("GET", "/api/evc/cases", None, EVC_C | EVC_W),
    ("GET", f"/api/evc/cases/{ID}", None, EVC_C | EVC_W),
    ("GET", f"/api/evc/cases/{ID}/evidence", None, EVIDENCE_ROLES),  # EVIDENCE_READ opens the permission gate; the case scope then answers "not found"
    ("GET", "/api/evc/assignable", None, EVC_C),
    ("POST", f"/api/evc/cases/{ID}/selection", {"user_ids": [], "idempotency_key": "matrix-key-0001"}, EVC_C),
    ("POST", f"/api/evc/cases/{ID}/selection/refresh", {}, EVC_C),
    ("POST", f"/api/evc/cases/{ID}/selection/cancel", {"reason": "Because."}, EVC_C),
    ("POST", f"/api/evc/cases/{ID}/reselect", {"reason": "Because.", "row_version": 1}, EVC_C),
    ("POST", f"/api/evc/cases/{ID}/schedule", SCHED, EVC_C),
    ("POST", f"/api/evc/cases/{ID}/schedule/reschedule", {**SCHED, "reason": "Because."}, EVC_C),
    ("POST", f"/api/evc/cases/{ID}/schedule/{ID}/confirm", {"row_version": 1}, EVC_C),
    ("POST", f"/api/evc/cases/{ID}/schedule/{ID}/cancel", {"reason": "Because."}, EVC_C),
    ("POST", f"/api/evc/cases/{ID}/route/determine", {"stage_key": None, "reason": "Because."}, EVC_C),
    ("POST", f"/api/evc/cases/{ID}/visit/start", {"row_version": 1}, EVC_W),
    ("PUT", f"/api/evc/cases/{ID}/observations/demo-premises", OBS, EVC_W),
    ("POST", f"/api/evc/cases/{ID}/evidence/link", {"url": "https://x.example.org/a", "description": "x"}, EVC_W),
    ("DELETE", f"/api/evc/cases/{ID}/evidence/{ID}", None, EVC_W),
    ("GET", f"/api/evc/evidence/{ID}/download", None, EVC_C | EVC_W),
    ("PUT", f"/api/evc/cases/{ID}/report", {"sections": {"summary": "x"}, "row_version": 1}, EVC_W),
    ("POST", f"/api/evc/cases/{ID}/report/submit", {"idempotency_key": "matrix-key-0001", "row_version": 1}, EVC_W),
    ("POST", f"/api/evc/cases/{ID}/report/amend", {"reason": "Because.", "row_version": 1}, EVC_C),
    ("POST", f"/api/evc/cases/{ID}/complete", {"summary": "Because.", "row_version": 1}, EVC_C),
    ("GET", "/api/shc/cases", None, SHC_C | SHC_W),
    ("GET", f"/api/shc/cases/{ID}", None, SHC_C | SHC_W),
    ("GET", f"/api/shc/cases/{ID}/evidence", None, EVIDENCE_ROLES),
    ("GET", "/api/shc/assignable", None, SHC_C),
    ("POST", f"/api/shc/cases/{ID}/selection", {"user_ids": [], "idempotency_key": "matrix-key-0001"}, SHC_C),
    ("POST", f"/api/shc/cases/{ID}/schedule", SCHED, SHC_C),
    ("POST", f"/api/shc/cases/{ID}/schedule/{ID}/confirm", {"row_version": 1}, SHC_C),
    ("POST", f"/api/shc/cases/{ID}/route/determine", {"stage_key": None, "reason": "Because."}, SHC_C),
    ("POST", f"/api/shc/cases/{ID}/requirements", {"title": "A point", "description": "Please address this."}, SHC_C),
    ("PATCH", f"/api/shc/cases/{ID}/requirements/{ID}", {"title": "A point", "row_version": 1}, SHC_C),
    ("DELETE", f"/api/shc/cases/{ID}/requirements/{ID}", None, SHC_C),
    ("POST", f"/api/shc/cases/{ID}/compliance/request", {"instructions": "Please respond.", "row_version": 1}, SHC_C),
    ("POST", f"/api/shc/cases/{ID}/review/start", {"row_version": 1}, SHC_C),
    ("POST", f"/api/shc/cases/{ID}/findings", {"assessment": "INCONCLUSIVE", "text": "Reasons given."}, SHC_W),
    ("PATCH", f"/api/shc/cases/{ID}/findings/{ID}", {"text": "Revised reasons.", "row_version": 1}, SHC_W),
    ("POST", f"/api/shc/cases/{ID}/proceedings", {"held_local": "2020-01-01T10:00", "timezone": "UTC", "text": "It happened."}, SHC_W),
    ("PUT", f"/api/shc/cases/{ID}/recommendation", {"option": None, "sections": {}, "row_version": 1}, SHC_W),
    ("POST", f"/api/shc/cases/{ID}/recommendation/submit", {"idempotency_key": "matrix-key-0001", "row_version": 1}, SHC_W),
    ("POST", f"/api/shc/cases/{ID}/recommendation/amend", {"reason": "Because.", "row_version": 1}, SHC_C),
    ("POST", f"/api/shc/cases/{ID}/complete", {"summary": "Because.", "row_version": 1}, SHC_C),
    ("GET", f"/api/applications/{ID}/committee", None, {Role.INSTITUTE_USER}),
    ("GET", "/api/compliance", None, {Role.INSTITUTE_USER}),
    ("GET", f"/api/compliance/{ID}", None, {Role.INSTITUTE_USER}),
    ("PUT", f"/api/compliance/{ID}/response", {"explanation": "x", "items": [], "row_version": None}, {Role.INSTITUTE_USER}),
    ("POST", f"/api/compliance/{ID}/response/submit", {"idempotency_key": "matrix-key-0001", "row_version": 1}, {Role.INSTITUTE_USER}),
    ("GET", "/api/committee/decision-handoffs", None, EVC_C | SHC_C | {Role.SCRUTINY_COORDINATOR}),
]


def call(client: Api, method: str, path: str, body: dict | None):
    fn = getattr(client, method.lower())
    return fn(path) if method in ("GET", "DELETE") else fn(path, body)


def clients_for_all_roles() -> dict[Role, Api]:
    out = {}
    for role in Role:
        make_user(role, f"{role.value}@matrix.example.org", institution=role == Role.INSTITUTE_USER)
        c = Api()
        assert c.login(f"{role.value}@matrix.example.org").status_code == 200
        out[role] = c
    return out


def test_every_endpoint_refuses_every_role_that_lacks_its_permission_and_unauthenticated_users():
    clients = clients_for_all_roles()
    anonymous = Api()
    failures = []
    for method, path, body, allowed in MATRIX:
        for role, c in clients.items():
            r = call(c, method, path, body)
            if role in allowed and r.status_code in (401, 403):
                failures.append(f"{role.value} wrongly refused {method} {path} ({r.status_code})")
            if role not in allowed and r.status_code != 403:
                failures.append(f"{role.value} not refused on {method} {path} ({r.status_code})")
        r = call(anonymous, method, path, body)
        if r.status_code not in (401, 403):
            failures.append(f"anonymous not refused on {method} {path} ({r.status_code})")
    assert not failures, "\n".join(failures)


def test_state_changing_committee_requests_need_the_csrf_token():
    c = make_cworld("evc_only")
    c.finish_scrutiny()
    r = c.evc_coord.http.post(f"{c.url('EVC')}/selection", json={"user_ids": [], "idempotency_key": "csrf-key-00001"})
    assert r.status_code == 403
    r = c.evc_coord.http.post(f"{c.url('EVC')}/selection", json={"user_ids": [], "idempotency_key": "csrf-key-00001"}, headers={"X-CSRF-Token": "forged", "Origin": "https://evil.example.org"})
    assert r.status_code == 403


def test_roles_outside_the_committee_gain_nothing_by_being_administrators_or_auditors():
    c = make_cworld("evc_only")
    c.finish_scrutiny()
    c.ready_visit()
    others = {}
    for role in (Role.AUDITOR, Role.PLATFORM_ADMIN, Role.RULES_ADMIN, Role.RULES_APPROVER, Role.EXECUTIVE_COMMITTEE, Role.SAC_MEMBER, Role.SCRUTINY_OFFICER, Role.NOTICE_ISSUER):
        make_user(role, f"{role.value}@other.example.org")
        o = Api()
        o.login(f"{role.value}@other.example.org")
        others[role] = o
    doc = c.w.refresh()["documents"][0]["id"] if c.w.refresh().get("documents") else None
    for role, o in others.items():
        assert o.get(c.url("EVC")).status_code == 403, role
        assert o.get("/api/evc/cases").status_code == 403, role
        assert o.get(c.url("EVC").replace("/evc/", "/shc/")).status_code == 403, role
    assert doc is None or others[Role.AUDITOR].get(f"/api/documents/{doc}").status_code == 404  # an auditor has no document access


def test_a_scrutiny_reviewer_and_an_shc_member_see_nothing_of_the_evc_case():
    c = make_cworld("evc_only")
    c.finish_scrutiny()
    c.ready_visit()
    assert c.w.scrutiny.get(c.url("EVC")).status_code == 403
    assert c.shc1.get(c.url("EVC")).status_code == 403
    assert c.shc_coord.get(f"{c.url('EVC')}/evidence").status_code == 404  # holds EVIDENCE_READ but has no scope on an EVC case
    doc_id = c.w.a.get(f"/api/applications/{c.w.app['id']}/documents").json()[0]["id"]
    assert c.shc1.get(f"/api/documents/{doc_id}").status_code == 404 and c.evc1.get(f"/api/documents/{doc_id}").status_code == 200 and c.evc2.get(f"/api/documents/{doc_id}").status_code == 404


def test_the_document_download_audits_who_read_what_for_committee_work():
    c = make_cworld("evc_only")
    c.finish_scrutiny()
    c.ready_visit()
    doc_id = c.w.a.get(f"/api/applications/{c.w.app['id']}/documents").json()[0]["id"]
    assert c.evc1.get(f"/api/documents/{doc_id}/download").status_code == 200
    assert c.evc1.get(f"{c.url('EVC')}/evidence").status_code == 200
    with SessionLocal() as db:
        acts = [a for (a,) in db.execute(select(AuditEvent.action))]
    assert "document.downloaded" in acts and "evidence.viewed" in acts


# ---- failure injection ------------------------------------------------------------------------------------


def test_a_failure_while_completing_a_stage_leaves_no_false_completed_state(monkeypatch):
    c = make_cworld("evc_only")
    c.finish_scrutiny()
    c.ready_visit()
    c.evc_to_report()

    def boom(*a, **k):
        raise RuntimeError("the database failed during the route step")

    monkeypatch.setattr(stages, "advance_from_case", boom)
    with pytest.raises(RuntimeError):
        c.evc_coord.post(f"{c.url('EVC')}/complete", {"summary": "Completing the stage.", "row_version": c.rv("EVC")})
    monkeypatch.undo()
    assert c.case("EVC")["state"] == "REPORT_SUBMITTED" and c.case("EVC")["completed_at"] is None
    with SessionLocal() as db:
        assert db.scalar(select(RouteTransition.id)) is None and db.scalar(select(DecisionHandoff.id)) is None
    assert c.complete_evc()["state"] == "COMPLETED"  # the same request succeeds once the fault is gone


def test_a_failure_while_activating_a_stage_rolls_back_the_scrutiny_completion_with_it(monkeypatch):
    c = make_cworld("evc_only")
    c.w.upload()
    from tests.evidence_world import drain

    drain()
    c.w.submit(assign=True)
    drain()
    c.w.start()

    def boom(*a, **k):
        raise RuntimeError("activation failed")

    monkeypatch.setattr(stages, "activate", boom)
    with pytest.raises(RuntimeError):
        c.w.scrutiny.post(f"{c.w.case_url}/complete", {"row_version": c.w.case_row().row_version, "summary": "Completing scrutiny."})
    monkeypatch.undo()
    with SessionLocal() as db:
        assert db.scalar(select(ScrutinyCase.state)) == "UNDER_SCRUTINY"  # not half-completed with a hand-off that nothing acted on
    assert c.row("EVC") is None


def test_a_failed_selection_request_write_leaves_the_case_unchanged(monkeypatch):
    c = make_cworld("evc_only")
    c.finish_scrutiny()

    def boom(*a, **k):
        raise RuntimeError("write failed")

    monkeypatch.setattr(ctexts, "selection_requested", boom)
    with pytest.raises(RuntimeError):
        c.evc_select()
    monkeypatch.undo()
    assert c.case("EVC")["state"] == "SELECTION_PENDING"
    with SessionLocal() as db:
        assert db.scalars(select(SelectionRequest)).all() == []


def test_a_notification_that_cannot_be_stored_never_undoes_the_change_that_caused_it(monkeypatch):
    c = make_cworld("evc_only")
    c.finish_scrutiny()
    c.evc_select()
    c.schedule()

    def refuse(db, row):
        raise OSError("the notification table is unavailable")

    monkeypatch.setattr(nt, "_insert", refuse)
    d = c.confirm()
    monkeypatch.undo()
    assert d["state"] == "SCHEDULED" and d["schedule"]["active"]["status"] == "CONFIRMED"
    with SessionLocal() as db:
        assert db.scalar(select(AuditEvent.id).where(AuditEvent.action == "notification.persist_failed")) is not None
        assert db.scalar(select(Notification.id).where(Notification.event_type == "SCHEDULE_CONFIRMED")) is None


def test_stale_views_are_refused_on_every_coordinator_and_member_action():
    c = make_cworld("evc_only")
    c.finish_scrutiny()
    c.ready_visit()
    stale = c.rv("EVC") + 7
    assert c.evc1.post(f"{c.url('EVC')}/visit/start", {"row_version": stale}).json()["code"] == "stale_version"
    c.start_visit()
    assert c.evc_coord.post(f"{c.url('EVC')}/reselect", {"reason": "Too late for this.", "row_version": c.rv("EVC")}).json()["code"] == "invalid_transition"
    assert c.evc_coord.post(f"{c.url('EVC')}/complete", {"summary": "Not yet complete.", "row_version": c.rv("EVC")}).json()["code"] == "invalid_transition"


def test_a_rule_version_published_mid_process_changes_nothing_about_a_running_case():
    c = make_cworld("evc_only")
    c.finish_scrutiny()
    before = c.case("EVC")
    publish_changed_version(c.w, days=30)
    after = c.case("EVC")
    assert after["trigger"]["rule_version"] == before["trigger"]["rule_version"] and after["policy"]["content_hash"] == before["policy"]["content_hash"]
    with SessionLocal() as db:
        case = db.scalar(select(CommitteeCase))
        assert case.rule_version_id == db.scalar(select(ScrutinyCase.rule_version_id))
    c.ready_visit()
    c.evc_to_report()
    assert c.complete_evc()["state"] == "COMPLETED"


def test_state_cannot_be_assigned_directly_only_through_a_transition():
    from app.guards import ImmutableRecordError

    c = make_cworld("evc_only")
    c.finish_scrutiny()
    with SessionLocal() as db:
        case = db.scalar(select(CommitteeCase))
        case.state = "COMPLETED"  # a code path that tried to skip the workflow
        with pytest.raises(ImmutableRecordError):
            db.flush()
        db.rollback()
    assert c.case("EVC")["state"] == "SELECTION_PENDING"


def test_database_constraints_back_up_the_services():
    from sqlalchemy.exc import IntegrityError

    c = make_cworld("evc_only")
    c.finish_scrutiny()
    c.ready_visit()
    with SessionLocal() as db:  # a second EVC case for the same application is impossible
        first = db.scalar(select(CommitteeCase))
        db.add(CommitteeCase(application_id=first.application_id, stage="EVC", rule_version_id=first.rule_version_id, state="SELECTION_PENDING", trigger={}))
        with pytest.raises(IntegrityError):
            db.flush()
        db.rollback()
    with SessionLocal() as db:  # two live schedules for one case are impossible
        first = db.scalar(select(CommitteeCase))
        live = db.scalar(select(CommitteeSchedule))
        db.add(CommitteeSchedule(case_id=first.id, kind="VISIT", sequence=9, status="PROPOSED", start_at=live.start_at, timezone="UTC", mode="ONLINE", created_by_id=live.created_by_id))
        with pytest.raises(IntegrityError):
            db.flush()
        db.rollback()
    with SessionLocal() as db:  # a state outside the machine is refused by the database itself
        from sqlalchemy import text

        with pytest.raises(IntegrityError):
            db.execute(text("UPDATE committee_cases SET state = 'APPROVED'"))
        db.rollback()


# ---- concurrency (needs a real database) ----------------------------------------------------------------------


@pytest.mark.skipif(engine.dialect.name == "sqlite", reason="needs real row locking (PostgreSQL)")
def test_simultaneous_report_submissions_create_exactly_one_report():
    c = make_cworld("evc_only")
    c.finish_scrutiny()
    c.ready_visit()
    c.start_visit()
    c.complete_checklist()
    c.save_report()
    version = c.case("EVC", c.evc1)["evc"]["draft"]["row_version"]
    members = []
    for _ in range(4):
        m = Api()
        m.login("evc.m1@test.example.org")
        members.append(m)

    def go(i):
        key = "same-report-key" if i < 2 else f"other-report-key-{i}"
        return key, members[i].post(f"{c.url('EVC')}/report/submit", {"idempotency_key": key, "row_version": version}).status_code

    with ThreadPoolExecutor(4) as pool:
        results = list(pool.map(go, range(4)))
    # Whichever request takes the lock first wins; only its key may succeed, and a repeat of that key replays the same record.
    winning_keys = {k for k, code in results if code == 200}
    assert len(winning_keys) == 1 and all(code == 409 for k, code in results if k not in winning_keys)
    assert sum(1 for _, code in results if code == 200) == (2 if winning_keys == {"same-report-key"} else 1)
    with SessionLocal() as db:
        assert len(db.scalars(select(CommitteeReport).where(CommitteeReport.status == "SUBMITTED")).all()) == 1
        assert len(db.scalars(select(Notification).where(Notification.event_type == "REPORT_SUBMITTED", Notification.recipient_kind == "coordinator")).all()) == 1


@pytest.mark.skipif(engine.dialect.name == "sqlite", reason="needs real row locking (PostgreSQL)")
def test_simultaneous_schedule_proposals_leave_one_live_schedule():
    c = make_cworld("evc_only")
    c.finish_scrutiny()
    c.evc_select()
    coords = []
    for _ in range(4):
        m = Api()
        m.login("evc.coord@test.example.org")
        coords.append(m)

    def go(i):
        return coords[i].post(f"{c.url('EVC')}/schedule", {"start_local": f"2031-02-0{i + 1}T10:00", "timezone": "Asia/Kolkata", "mode": "ONLINE", "access_details": "x", "idempotency_key": f"race-key-{i}-abcdef"}).status_code

    with ThreadPoolExecutor(4) as pool:
        codes = list(pool.map(go, range(4)))
    assert codes.count(201) == 1 and all(code in (201, 409) for code in codes)
    with SessionLocal() as db:
        assert len(db.scalars(select(CommitteeSchedule).where(CommitteeSchedule.status.in_(("PROPOSED", "CONFIRMED")))).all()) == 1


@pytest.mark.skipif(engine.dialect.name == "sqlite", reason="needs real row locking (PostgreSQL)")
def test_simultaneous_activation_requests_create_one_case():
    from app.models_applications import Application

    c = make_cworld("evc_only")
    c.finish_scrutiny()
    app_id = uuid.UUID(c.w.app["id"])

    def go(_):
        with SessionLocal() as db:
            app = db.get(Application, app_id)
            case = stages.activate(db, app, "evc", from_stage_key="scrutiny", source="race", actor=None, request=None)
            db.commit()
            return str(case.id)

    with ThreadPoolExecutor(4) as pool:
        ids = set(pool.map(go, range(4)))
    assert len(ids) == 1
