"""Direct-API permission matrix, immutability of history, notifications (acceptance 14, 32, 33, 38)."""

import uuid

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.guards import ImmutableRecordError
from app.main import app as fastapi_app
from app.models import AuditEvent
from app.models_scrutiny import DeficiencyNotice, FindingEvent, Notification, NoticeItem, NoticeResponse, ScrutinyCase, ScrutinyEvent, ScrutinyFinding
from app.rbac import Role
from tests.conftest import Api, make_user
from tests.evidence_world import drain, make_world

world = pytest.fixture(make_world)

NEW_PREFIXES = ("/api/scrutiny", "/api/notices", "/api/notifications")


def issued(w):
    w.upload()
    drain()
    w.submit()
    drain()
    w.start()
    n = w.submit_notice(w.make_notice())
    w.issue(n["id"])
    return n["id"]


def concrete(path: str, w, nid: str, fid: str) -> str:
    return (path.replace("{case_id}", w.case_id).replace("{notice_id}", nid).replace("{finding_id}", fid).replace("{item_id}", str(uuid.uuid4()))
            .replace("{notification_id}", str(uuid.uuid4())).replace("{application_id}", w.app["id"]))


def test_every_new_endpoint_rejects_anonymous_callers(world):
    nid = issued(world)
    fid = world.case()["findings"][0]["id"]
    anon = Api()
    checked = 0
    for path, methods in fastapi_app.openapi()["paths"].items():
        if not (path.startswith(NEW_PREFIXES) or path.endswith("/scrutiny")):
            continue
        for method in methods:
            url = concrete(path, world, nid, fid)
            r = anon.http.request(method.upper(), url, headers=anon._csrf() if method != "get" else {}, json={} if method != "get" else None)
            assert r.status_code in (401, 403), (method, path, r.status_code)
            checked += 1
    assert checked >= 28


# endpoint -> the roles that may *reach* it at all (further scope checks still apply). Everything else must be 403.
ALLOWED = {
    ("GET", "/api/scrutiny/cases"): {"scrutiny_officer", "scrutiny_coordinator", "notice_issuer"},
    ("GET", "/api/scrutiny/cases/{case_id}"): {"scrutiny_officer", "scrutiny_coordinator", "notice_issuer"},
    # EVIDENCE_READ also opens the permission gate for committee roles (Phase 5); without scrutiny scope the case is simply "not found" (404), tested in test_committee_security
    ("GET", "/api/scrutiny/cases/{case_id}/evidence"): {"scrutiny_officer", "notice_issuer", "evc_coordinator", "evc_member", "shc_member"},
    ("GET", "/api/scrutiny/handoffs"): {"scrutiny_coordinator"},
    ("GET", "/api/scrutiny/assignable"): {"scrutiny_coordinator"},
    ("POST", "/api/scrutiny/cases/{case_id}/assign"): {"scrutiny_coordinator"},
    ("POST", "/api/scrutiny/cases/{case_id}/handoff/determine"): {"scrutiny_coordinator"},
    ("POST", "/api/scrutiny/cases/{case_id}/start"): {"scrutiny_officer"},
    ("PUT", "/api/scrutiny/cases/{case_id}/notes"): {"scrutiny_officer"},
    ("POST", "/api/scrutiny/cases/{case_id}/findings"): {"scrutiny_officer"},
    ("PATCH", "/api/scrutiny/cases/{case_id}/findings/{finding_id}"): {"scrutiny_officer"},
    ("POST", "/api/scrutiny/cases/{case_id}/findings/{finding_id}/disposition"): {"scrutiny_officer"},
    ("POST", "/api/scrutiny/cases/{case_id}/complete"): {"scrutiny_officer"},
    ("POST", "/api/scrutiny/cases/{case_id}/notices"): {"scrutiny_officer"},
    ("PATCH", "/api/scrutiny/notices/{notice_id}"): {"scrutiny_officer"},
    ("POST", "/api/scrutiny/notices/{notice_id}/submit"): {"scrutiny_officer"},
    ("POST", "/api/scrutiny/notices/{notice_id}/withdraw"): {"scrutiny_officer"},
    ("GET", "/api/scrutiny/notices/{notice_id}/preview"): {"scrutiny_officer", "notice_issuer"},
    ("POST", "/api/scrutiny/notices/{notice_id}/issue"): {"notice_issuer"},
    ("POST", "/api/scrutiny/notices/{notice_id}/return"): {"notice_issuer"},
    ("GET", "/api/notices"): {"institute_user"},
    ("GET", "/api/notices/{notice_id}"): {"institute_user"},
    ("PUT", "/api/notices/{notice_id}/response"): {"institute_user"},
    ("POST", "/api/notices/{notice_id}/items/{item_id}/documents"): {"institute_user"},
    ("POST", "/api/notices/{notice_id}/response/submit"): {"institute_user"},
}


def test_role_permissions_are_enforced_on_every_staff_and_institution_endpoint_directly(world):
    """Not a UI test: each role calls each endpoint over HTTP. A role outside the allowed set must get 403 (never a 2xx, never data)."""
    nid = issued(world)
    fid = world.case()["findings"][0]["id"]
    clients = {"institute_user": world.a, "scrutiny_officer": world.scrutiny, "scrutiny_coordinator": world.coordinator, "notice_issuer": world.issuer, "auditor": world.auditor}
    for role, email in ((Role.EVC_COORDINATOR, "e1@t.example.org"), (Role.EVC_MEMBER, "e2@t.example.org"), (Role.SHC_MEMBER, "e3@t.example.org"), (Role.EXECUTIVE_COMMITTEE, "e4@t.example.org"),
                        (Role.SAC_MEMBER, "e5@t.example.org"), (Role.RULES_ADMIN, "e6@t.example.org"), (Role.RULES_APPROVER, "e7@t.example.org"), (Role.PLATFORM_ADMIN, "e8@t.example.org")):
        make_user(role, email)
        c = Api()
        c.login(email)
        clients[role.value] = c
    for (method, path), allowed in ALLOWED.items():
        url = concrete(path, world, nid, fid)
        for role, c in clients.items():
            if role in allowed:
                continue
            kw = {} if method == "GET" else {"json": {}}
            r = c.http.request(method, url, headers=c._csrf() if method != "GET" else {}, **kw)
            assert r.status_code == 403, f"{role} {method} {path} -> {r.status_code}"
    covered = {(m.upper(), p) for p, ms in fastapi_app.openapi()["paths"].items() if p.startswith(("/api/scrutiny", "/api/notices")) for m in ms}
    assert covered == set(ALLOWED), covered ^ set(ALLOWED)  # a new endpoint cannot appear without being added to this matrix


def test_notifications_belong_to_their_recipient(world):
    issued(world)
    mine = world.a.get("/api/notifications").json()
    assert mine["total"] == 1 and mine["unread"] == 1
    nid = mine["items"][0]["id"]
    assert world.b.post(f"/api/notifications/{nid}/read").status_code == 404
    assert world.scrutiny.post(f"/api/notifications/{nid}/read").status_code == 404
    assert world.b.get("/api/notifications").json()["total"] == 0
    assert world.a.post(f"/api/notifications/{nid}/read").json()["read"] is True
    assert world.a.get("/api/notifications?unread_only=true").json()["total"] == 0 and world.a.get("/api/notifications").json()["unread"] == 0
    assert world.coordinator.post("/api/notifications/read-all").json()["marked"] >= 1
    assert Api().get("/api/notifications").status_code == 401
    assert world.auditor.get("/api/notifications").status_code == 403  # the auditor has no scrutiny events addressed to them and no inbox


# ---- 14. history cannot be rewritten --------------------------------------------------------------------------------


def test_no_endpoint_can_modify_an_issued_notice_a_submitted_response_or_history(world):
    nid = issued(world)
    world.respond(nid)
    items = world.a.get(f"/api/notices/{nid}").json()["items"]
    for method in ("PUT", "PATCH", "DELETE"):
        r = world.a.http.request(method, f"/api/notices/{nid}", headers=world.a._csrf(), json={"summary": "rewritten"})
        assert r.status_code in (404, 405), method
    # staff: the draft-editing endpoint refuses anything that is no longer a draft
    n = world.case()["notices"][0]
    r = world.scrutiny.patch(f"/api/scrutiny/notices/{nid}", {"row_version": n["row_version"], "summary": "Rewriting an issued notice"})
    assert r.status_code == 409 and r.json()["code"] == "notice_not_draft"
    assert world.scrutiny.post(f"/api/scrutiny/notices/{nid}/withdraw", {"reason": "Trying to withdraw an issued notice"}).status_code == 409
    assert world.issuer.post(f"/api/scrutiny/notices/{nid}/return", {"reason": "Trying to return an issued notice"}).status_code == 409
    saved = world.a.put(f"/api/notices/{nid}/response", {"explanation": "Edit after submit", "items": [], "row_version": 2})
    assert saved.status_code == 409
    after = world.case()["notices"][0]
    assert after["summary"] == n["summary"] and after["items"][0]["response_explanation"] == "The letter is enclosed."
    _ = items


def test_the_orm_refuses_to_modify_frozen_records_even_if_a_handler_tried(world):
    nid = issued(world)
    world.respond(nid)
    world.start()
    checks = [
        (DeficiencyNotice, lambda o: setattr(o, "summary", "tampered")),
        (NoticeItem, lambda o: setattr(o, "description", "tampered")),
        (NoticeResponse, lambda o: setattr(o, "explanation", "tampered")),
        (ScrutinyEvent, lambda o: setattr(o, "reason", "tampered")),
        (FindingEvent, lambda o: setattr(o, "reason", "tampered")),
        (ScrutinyCase, lambda o: setattr(o, "state", "SCRUTINY_COMPLETED")),  # a status can only move through the transition table
        (ScrutinyFinding, lambda o: setattr(o, "state", "RESOLVED")),
    ]
    for model, mutate in checks:
        with SessionLocal() as db:
            obj = db.scalar(select(model))
            mutate(obj)
            with pytest.raises(ImmutableRecordError):
                db.flush()
    with SessionLocal() as db:  # deleting is refused too
        db.delete(db.scalar(select(DeficiencyNotice)))
        with pytest.raises(ImmutableRecordError):
            db.flush()


def test_notice_state_changes_only_along_the_transition_table(world):
    from app.domain import NOTICE_TRANSITIONS, NoticeStatus
    from app.services import notices as nsvc
    from app.services.errors import ServiceError

    nid = issued(world)
    with SessionLocal() as db:
        n = db.scalar(select(DeficiencyNotice).where(DeficiencyNotice.id == uuid.UUID(nid)))
        for target in (NoticeStatus.DRAFT, NoticeStatus.IN_REVIEW, NoticeStatus.CLOSED, NoticeStatus.WITHDRAWN, NoticeStatus.ISSUED):
            assert target not in NOTICE_TRANSITIONS[NoticeStatus.ISSUED]
            with pytest.raises(ServiceError) as e:
                nsvc._set_status(db, n, target)
            assert e.value.code == "invalid_transition"


def test_case_transitions_reject_undefined_moves(world):
    from app.domain import CaseAction
    from app.services import scrutiny as sc
    from app.services.errors import ServiceError

    world.upload()
    drain()
    world.submit(assign=False)
    with SessionLocal() as db:
        case = db.scalar(select(ScrutinyCase))
        for action in (CaseAction.START, CaseAction.COMPLETE, CaseAction.ISSUE_NOTICE, CaseAction.RECEIVE_RESPONSE, CaseAction.PREPARE_NOTICE):
            with pytest.raises(ServiceError) as e:
                sc.transition(db, case, action)
            assert e.value.code == "invalid_transition", action


# ---- 32. events identify actor, time, object and action ----------------------------------------------------------------------


def test_every_scrutiny_event_names_its_actor_time_object_and_action(world):
    nid = issued(world)
    world.respond(nid)
    with SessionLocal() as db:
        events = list(db.scalars(select(ScrutinyEvent).order_by(ScrutinyEvent.at)))
        audits = {a.action: a for a in db.scalars(select(AuditEvent).where(AuditEvent.action.like("scrutiny.%")))}
    assert events and all(e.actor_label and e.at and e.action and e.object_type and e.object_id for e in events)
    assert all(e.actor_id for e in events)  # every one of these was a person
    assert {"scrutiny.case_opened", "scrutiny.assigned", "scrutiny.round_started", "scrutiny.finding_recorded", "scrutiny.notice_drafted", "scrutiny.notice_submitted_for_issuance",
            "scrutiny.notice_issued", "scrutiny.response_submitted"} <= set(audits)
    issued_audit = audits["scrutiny.notice_issued"]
    assert issued_audit.actor_label == "issuer@test.example.org" and issued_audit.target_type == "deficiency_notice" and issued_audit.target_id == nid and issued_audit.occurred_at
    assert issued_audit.detail["from"] == "AWAITING_NOTICE_DECISION" and issued_audit.detail["to"] == "AWAITING_RESPONSE" and issued_audit.detail["reason"]
    # the audit log never carries document text or reviewer wording
    import json

    blob = json.dumps([a.detail for a in audits.values()])
    assert "validity period" not in blob and "affiliation" not in blob


def test_the_institutions_timeline_shows_only_events_meant_for_it(world):
    nid = issued(world)
    world.respond(nid)
    labels = [e["label"] for e in world.a.get(f"{world.url}/scrutiny").json()["events"]]
    assert labels[0] == "Application received for scrutiny" and labels[-1].startswith("Your response to DN-") and not any("assigned" in l.lower() or "finding" in l.lower() for l in labels)
    internal = {e["action"] for e in world.case()["events"]}
    assert {"assigned", "finding_recorded", "notice_drafted", "notice_submitted_for_issuance"} <= internal  # those exist for staff, not for the institution


def test_notifications_are_never_duplicated_when_events_are_replayed(world):
    nid = issued(world)
    world.issue(nid, key="issue-key-0001")  # a replay of the very same request
    world.respond(nid)
    world.a.post(f"/api/notices/{nid}/response/submit", {"idempotency_key": "resp-key-0001", "row_version": 3})  # replay
    with SessionLocal() as db:
        rows = list(db.scalars(select(Notification)))
    keys = [r.dedupe_key for r in rows]
    assert len(keys) == len(set(keys))
    by_event = {}
    for r in rows:
        by_event[r.event_type] = by_event.get(r.event_type, 0) + 1
    assert by_event["NOTICE_ISSUED"] == 1 and by_event["RESPONSE_SUBMITTED"] == 1 and by_event["READY_FOR_RESCRUTINY"] == 1 and by_event["CASE_ASSIGNED"] == 1
