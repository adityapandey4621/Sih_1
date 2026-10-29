import re
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select, text

from app.config import get_settings
from app.db import SessionLocal, engine
from app.guards import ImmutableRecordError
from app.models import AuditEvent, User
from app.models_applications import Application, ApplicationDocument
from app.models_catalogue import ApplicationCategory, ConfigurationGap, RuleVersion
from app.rbac import Role
from tests.catalogue_helpers import PDF, build_published, make_category
from tests.conftest import Api, make_user

REF = re.compile(r"^APP-[A-Z2-9]{4}-[A-Z2-9]{4}$")


@pytest.fixture
def world():
    cat = build_published(set_key="s1")
    make_user(Role.INSTITUTE_USER, "a@test.example.org", institution=True)
    make_user(Role.INSTITUTE_USER, "b@test.example.org", institution=True)
    a, b = Api(), Api()
    assert a.login("a@test.example.org").status_code == 200
    assert b.login("b@test.example.org").status_code == 200
    return SimpleNamespace(cat=cat, a=a, b=b)


def create(client: Api, cat, year: str = "2026-27", **extra):
    return client.post("/api/applications", {"category_id": str(cat.category_id), "academic_year": year, **extra})


def complete(client: Api, app: dict) -> dict:
    """Supply every required item of the test configuration."""
    r = client.patch(f"/api/applications/{app['id']}", {"form_data": {"contact": "R. Sharma", "kind": "A", "decl": True}, "row_version": app["row_version"]})
    assert r.status_code == 200, r.text
    r = client.upload(f"/api/applications/{app['id']}/documents", "doc-main", "main.pdf", PDF)
    assert r.status_code == 201, r.text
    return r.json()


# ---- 1, 2, 3: drafts -----------------------------------------------------------------------------


def test_institute_can_create_and_save_a_draft_scoped_to_its_own_institution(world):
    with SessionLocal() as db:
        other_inst = db.scalar(select(User.institution_id).where(User.email == "b@test.example.org"))
    r = create(world.a, world.cat, institution_id=str(other_inst))  # a client-supplied institution is ignored
    assert r.status_code == 201, r.text
    app = r.json()
    assert REF.match(app["reference"]) and app["status"] == "DRAFT" and app["editable"] is True
    assert app["institution"]["id"] != str(other_inst)
    assert app["submitted_at"] is None
    assert app["rule_version"]["rule_set_key"] == "s1" and app["rule_version"]["number"] == 1
    assert app["is_demo"] is True and app["rule_version"]["demo_banner"].startswith("DEMO ONLY")
    assert app["checklist"] == {"required_total": 4, "required_supplied": 0, "optional_total": 1, "optional_supplied": 0}
    assert app["outstanding_action"] == "Supply 4 required items"
    assert [h["action"] for h in app["history"]] == ["created"]


def test_checklist_comes_from_the_approved_configuration(world):
    preview = world.a.get(f"/api/catalogue/checklist?category_id={world.cat.category_id}&academic_year=2026-27").json()
    app = create(world.a, world.cat).json()
    assert [i["item_key"] for i in preview["checklist"]] == [i["item_key"] for i in app["checklist_items"]]
    assert preview["is_demo"] and preview["verification"] == "demo" and preview["sources"][0]["verification_status"] == "DEMO_ONLY"
    by_key = {i["item_key"]: i for i in app["checklist_items"]}
    assert by_key["contact"]["required"] and not by_key["doc-extra"]["required"]
    assert by_key["doc-main"]["explanation"] == "Required file."
    assert by_key["b-detail"]["applicable"] is False and by_key["b-detail"]["status"] == "not_applicable"
    assert all(i["status"] in ("missing", "not_applicable") for i in app["checklist_items"])  # nothing is "verified" or even supplied yet


def test_draft_can_be_resumed_and_edited_by_its_owner(world):
    app = create(world.a, world.cat).json()
    r = world.a.patch(f"/api/applications/{app['id']}", {"form_data": {"contact": "  R. Sharma ", "kind": "A"}, "row_version": app["row_version"]})
    assert r.status_code == 200
    d = r.json()
    assert d["form_data"] == {"contact": "R. Sharma", "kind": "A"} and d["row_version"] == app["row_version"] + 1
    resumed = world.a.get(f"/api/applications/{app['id']}").json()
    assert resumed["form_data"]["contact"] == "R. Sharma" and resumed["checklist"]["required_supplied"] == 2
    cleared = world.a.patch(f"/api/applications/{app['id']}", {"form_data": {"contact": ""}, "row_version": d["row_version"]}).json()
    assert "contact" not in cleared["form_data"]
    assert [h["action"] for h in cleared["history"]][-2:] == ["draft_updated", "draft_updated"]


@pytest.mark.parametrize("data", [{"nope": 1}, {"kind": "C"}, {"contact": "x" * 51}, {"decl": False}, {"contact": 5}, {"doc-main": "x"}])
def test_invalid_draft_values_are_rejected_by_the_server(world, data):
    app = create(world.a, world.cat).json()
    r = world.a.patch(f"/api/applications/{app['id']}", {"form_data": data, "row_version": app["row_version"]})
    assert r.status_code == 422 and r.json()["issues"], r.text
    assert world.a.get(f"/api/applications/{app['id']}").json()["form_data"] == {}  # nothing partially saved


def test_stale_draft_edit_is_rejected_not_overwritten(world):
    app = create(world.a, world.cat).json()
    ok = world.a.patch(f"/api/applications/{app['id']}", {"form_data": {"contact": "first"}, "row_version": app["row_version"]})
    assert ok.status_code == 200
    stale = world.a.patch(f"/api/applications/{app['id']}", {"form_data": {"contact": "second"}, "row_version": app["row_version"]})
    assert stale.status_code == 409 and stale.json()["code"] == "stale_version"
    assert world.a.get(f"/api/applications/{app['id']}").json()["form_data"]["contact"] == "first"


def test_conditional_items_apply_only_when_their_condition_is_met(world):
    app = create(world.a, world.cat).json()
    d = world.a.patch(f"/api/applications/{app['id']}", {"form_data": {"kind": "B"}, "row_version": app["row_version"]}).json()
    detail = {i["item_key"]: i for i in d["checklist_items"]}
    assert detail["b-detail"]["applicable"] and detail["b-detail"]["status"] == "missing"
    assert d["checklist"]["required_total"] == 5
    d = world.a.patch(f"/api/applications/{app['id']}", {"form_data": {"kind": "A"}, "row_version": d["row_version"]}).json()
    assert d["checklist"]["required_total"] == 4


# ---- 4, 5, 6, 7: submission -----------------------------------------------------------------------


def test_submission_is_blocked_until_every_required_item_is_supplied(world):
    app = create(world.a, world.cat).json()
    r = world.a.post(f"/api/applications/{app['id']}/submit", {"row_version": app["row_version"]})
    assert r.status_code == 422 and r.json()["code"] == "submission_blocked"
    assert {i["path"] for i in r.json()["issues"]} == {"contact", "kind", "doc-main", "decl"}
    assert world.a.get(f"/api/applications/{app['id']}").json()["status"] == "DRAFT"
    # a file alone is not enough
    done = complete(world.a, app)
    done = world.a.patch(f"/api/applications/{app['id']}", {"form_data": {"kind": "B"}, "row_version": done["row_version"]}).json()
    r = world.a.post(f"/api/applications/{app['id']}/submit", {"row_version": done["row_version"]})
    assert r.status_code == 422 and {i["path"] for i in r.json()["issues"]} == {"b-detail"}


def test_submission_records_actor_time_and_the_exact_rule_version(world):
    app = complete(world.a, create(world.a, world.cat).json())
    r = world.a.post(f"/api/applications/{app['id']}/submit", {"row_version": app["row_version"]})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["status"] == "SUBMITTED" and d["editable"] is False and d["submitted_at"] and d["submitted_by"] == "Test institute_user"
    assert d["outstanding_action"] is None
    with SessionLocal() as db:
        row = db.get(Application, uuid.UUID(app["id"]))
        ver = db.get(RuleVersion, world.cat.version_id)
        snap = row.submission_snapshot
        assert row.rule_version_id == ver.id and snap["rule_version"]["number"] == 1 and snap["rule_version"]["content_hash"] == ver.content_hash
        assert snap["configuration"] == ver.snapshot and snap["institution"]["name"] == "Test Institute"
        assert snap["form_data"]["contact"] == "R. Sharma" and snap["documents"][0]["sha256"]
        assert row.submitted_by_id and row.submitted_at
        ev = db.scalar(select(AuditEvent).where(AuditEvent.action == "application.submitted"))
        assert ev.actor_label == "a@test.example.org" and ev.detail["content_hash"] == ver.content_hash
    assert [h["action"] for h in d["history"]][-1] == "submitted"


def test_repeated_submission_cannot_create_a_second_submission(world):
    app = complete(world.a, create(world.a, world.cat).json())
    first = world.a.post(f"/api/applications/{app['id']}/submit", {"row_version": app["row_version"]})
    again = world.a.post(f"/api/applications/{app['id']}/submit", {"row_version": app["row_version"]})
    retry_new_version = world.a.post(f"/api/applications/{app['id']}/submit", {"row_version": first.json()["row_version"]})
    assert first.status_code == 200 and again.status_code == 409 and retry_new_version.status_code == 409
    assert again.json()["code"] == "already_submitted"
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(AuditEvent).where(AuditEvent.action == "application.submitted")) == 1


@pytest.mark.skipif(engine.dialect.name == "sqlite", reason="needs real row locking (PostgreSQL)")
def test_concurrent_submissions_produce_exactly_one_submission(world):
    from concurrent.futures import ThreadPoolExecutor

    app = complete(world.a, create(world.a, world.cat).json())
    clients = []
    for _ in range(4):
        c = Api()
        c.login("a@test.example.org")
        clients.append(c)
    with ThreadPoolExecutor(4) as pool:
        codes = sorted(pool.map(lambda c: c.post(f"/api/applications/{app['id']}/submit", {"row_version": app["row_version"]}).status_code, clients))
    assert codes == [200, 409, 409, 409]
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(AuditEvent).where(AuditEvent.action == "application.submitted")) == 1


def test_a_submitted_application_cannot_be_silently_edited(world):
    app = complete(world.a, create(world.a, world.cat).json())
    sub = world.a.post(f"/api/applications/{app['id']}/submit", {"row_version": app["row_version"]}).json()
    doc = next(d for d in sub["documents"] if not d["removed"])
    edit = world.a.patch(f"/api/applications/{app['id']}", {"form_data": {"contact": "changed"}, "row_version": sub["row_version"]})
    assert edit.status_code == 409 and edit.json()["code"] == "not_draft"
    assert world.a.upload(f"/api/applications/{app['id']}/documents", "doc-extra", "x.pdf", PDF).status_code == 409
    assert world.a.upload(f"/api/applications/{app['id']}/documents", "doc-main", "replacement.pdf", PDF).status_code == 409
    assert world.a.delete(f"/api/applications/{app['id']}/documents/{doc['id']}").status_code == 409
    assert world.a.post(f"/api/applications/{app['id']}/rebind").status_code == 409
    d = world.a.get(f"/api/applications/{app['id']}").json()
    assert d["form_data"]["contact"] == "R. Sharma" and d["documents"][0]["original_filename"] == "main.pdf"


def test_submitted_records_are_also_protected_below_the_service_layer(world):
    app = complete(world.a, create(world.a, world.cat).json())
    world.a.post(f"/api/applications/{app['id']}/submit", {"row_version": app["row_version"]})
    with SessionLocal() as db:
        row = db.get(Application, uuid.UUID(app["id"]))
        row.form_data = {"tampered": True}
        with pytest.raises(ImmutableRecordError):
            db.flush()
        db.rollback()
        doc = db.scalar(select(ApplicationDocument).where(ApplicationDocument.application_id == uuid.UUID(app["id"])))
        doc.original_filename = "tampered.pdf"
        with pytest.raises(ImmutableRecordError):
            db.flush()


# ---- 8: scope and permissions ----------------------------------------------------------------------


def test_another_institution_cannot_read_or_change_an_application(world):
    app = complete(world.a, create(world.a, world.cat).json())
    doc = next(d for d in world.a.get(f"/api/applications/{app['id']}").json()["documents"])
    b, i = world.b, app["id"]
    checks = [
        b.get(f"/api/applications/{i}"),
        b.patch(f"/api/applications/{i}", {"form_data": {"contact": "hijack"}, "row_version": app["row_version"]}),
        b.post(f"/api/applications/{i}/submit", {"row_version": app["row_version"]}),
        b.post(f"/api/applications/{i}/rebind"),
        b.upload(f"/api/applications/{i}/documents", "doc-extra", "x.pdf", PDF),
        b.delete(f"/api/applications/{i}/documents/{doc['id']}"),
        b.get(f"/api/applications/{i}/documents/{doc['id']}/download"),
    ]
    assert [c.status_code for c in checks] == [404] * len(checks)  # "not found": existence is not revealed
    assert b.get("/api/applications").json()["total"] == 0
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(AuditEvent).where(AuditEvent.action == "application.access_denied")) == len(checks)
    assert world.a.get(f"/api/applications/{i}").json()["form_data"]["contact"] == "R. Sharma"


@pytest.mark.parametrize("role", [Role.SCRUTINY_OFFICER, Role.EVC_COORDINATOR, Role.EVC_MEMBER, Role.SHC_MEMBER, Role.EXECUTIVE_COMMITTEE, Role.SAC_MEMBER, Role.RULES_ADMIN, Role.RULES_APPROVER, Role.AUDITOR, Role.PLATFORM_ADMIN])
def test_only_institute_users_can_use_the_application_endpoints(world, role):
    make_user(role, "x@test.example.org")
    x = Api()
    x.login("x@test.example.org")
    app = create(world.a, world.cat).json()
    assert x.get("/api/applications").status_code == 403
    assert x.get(f"/api/applications/{app['id']}").status_code == 403
    assert create(x, world.cat).status_code == 403
    assert x.get("/api/catalogue/options").status_code == 403
    assert x.post(f"/api/applications/{app['id']}/submit", {"row_version": 1}).status_code == 403


def test_unauthenticated_access_is_refused(world):
    anon = Api()
    assert anon.get("/api/applications").status_code == 401
    assert anon.get("/api/catalogue/options").status_code == 401
    assert create(anon, world.cat).status_code == 401


def test_client_cannot_promote_itself_by_sending_role_or_permission_fields(world):
    r = world.a.post("/api/rules/sets", {"key": "k", "title": "t", "category_id": str(world.cat.category_id), "classification": "DEMO", "role": "rules_admin", "permissions": ["rules:edit"]})
    assert r.status_code == 403
    assert world.a.get("/api/auth/me", headers={"X-Role": "rules_admin"}).json()["role"] == "institute_user"


def test_user_without_an_institution_cannot_start_an_application(world):
    make_user(Role.INSTITUTE_USER, "orphan@test.example.org")  # no institution
    o = Api()
    o.login("orphan@test.example.org")
    r = create(o, world.cat)
    assert r.status_code == 403 and r.json()["code"] == "no_institution"
    assert o.get("/api/applications").status_code == 403


# ---- 15: missing / ambiguous configuration never yields a guessed route ------------------------------


def test_no_approved_configuration_blocks_creation_and_records_a_gap(world):
    other = make_category("cat-without-rules", "Category without rules")
    r = world.a.post("/api/applications", {"category_id": str(other), "academic_year": "2026-27"})
    assert r.status_code == 409 and r.json()["code"] == "no_applicable_configuration"
    assert "Nothing has been guessed" in r.json()["detail"]
    world.a.post("/api/applications", {"category_id": str(other), "academic_year": "2026-27"})
    assert world.a.get("/api/applications").json()["total"] == 0
    with SessionLocal() as db:
        gaps = list(db.scalars(select(ConfigurationGap)))
        assert len(gaps) == 1 and gaps[0].report_count == 2 and gaps[0].reason == "no_approved_version" and gaps[0].status == "open"
        assert db.scalar(select(func.count()).select_from(Application)) == 0
    # preview has no side effects
    assert world.a.get(f"/api/catalogue/checklist?category_id={other}&academic_year=2026-27").status_code == 409
    with SessionLocal() as db:
        assert db.scalar(select(ConfigurationGap.report_count)) == 2


def test_year_without_configuration_inactive_category_and_bad_year(world):
    assert create(world.a, world.cat, year="2031-32").status_code == 409
    assert create(world.a, world.cat, year="2026-28").status_code == 422
    assert create(world.a, world.cat, year="26-27").status_code == 422
    with SessionLocal() as db:
        db.get(ApplicationCategory, world.cat.category_id).is_active = False
        db.commit()
    assert create(world.a, world.cat).status_code == 422


def test_ambiguous_configuration_is_reported_not_resolved(world):
    other = build_published(set_key="s2", publish=False)
    with SessionLocal() as db:
        key = other.version_id.hex if engine.dialect.name == "sqlite" else str(other.version_id)
        db.execute(text("update rule_versions set status='published' where id=:i"), {"i": key})
        db.execute(text("update rule_versions set snapshot=(select snapshot from rule_versions where id=:j) where id=:i"), {"i": key, "j": world.cat.version_id.hex if engine.dialect.name == "sqlite" else str(world.cat.version_id)})
        db.commit()
    r = create(world.a, world.cat)
    assert r.status_code == 409 and r.json()["code"] == "no_applicable_configuration"
    with SessionLocal() as db:
        assert db.scalar(select(ConfigurationGap.reason)) == "ambiguous"


def test_options_list_only_selectable_categories_and_years(world):
    make_category("empty-cat", "Has no rules yet")
    o = world.a.get("/api/catalogue/options").json()
    assert o["academic_years"] == ["2026-27"]
    assert {c["key"] for c in o["categories"]} == {"cat-a", "empty-cat"}


# ---- 16, 17: rule changes never rewrite submitted cases ----------------------------------------------


def test_publishing_a_new_rule_version_does_not_rewrite_a_submitted_application(world):
    submitted = complete(world.a, create(world.a, world.cat).json())
    submitted = world.a.post(f"/api/applications/{submitted['id']}/submit", {"row_version": submitted["row_version"]}).json()
    draft = create(world.a, world.cat).json()
    with SessionLocal() as db:
        before = db.get(Application, uuid.UUID(submitted["id"])).submission_snapshot

    publish_v2_with_extra_required_item(world.cat)

    after = world.a.get(f"/api/applications/{submitted['id']}").json()
    assert after["rule_version"]["number"] == 1 and len(after["checklist_items"]) == 6 and after["status"] == "SUBMITTED"
    assert "extra-new" not in {i["item_key"] for i in after["checklist_items"]}
    with SessionLocal() as db:
        assert db.get(Application, uuid.UUID(submitted["id"])).submission_snapshot == before
        assert db.get(RuleVersion, world.cat.version_id).status == "retired"

    # the draft is told a newer configuration applies, cannot be submitted against the old one, and can be moved explicitly
    d = world.a.get(f"/api/applications/{draft['id']}").json()
    assert d["rule_version"]["number"] == 1 and d["current_rule_version_differs"] is True
    r = world.a.post(f"/api/applications/{draft['id']}/submit", {"row_version": d["row_version"]})
    assert r.status_code == 409 and r.json()["code"] == "rule_version_changed"
    moved = world.a.post(f"/api/applications/{draft['id']}/rebind").json()
    assert moved["rule_version"]["number"] == 2 and moved["current_rule_version_differs"] is False
    assert "extra-new" in {i["item_key"] for i in moved["checklist_items"]}
    assert [h["action"] for h in moved["history"]][-1] == "rule_version_changed"
    moved = complete(world.a, moved)
    blocked = world.a.post(f"/api/applications/{draft['id']}/submit", {"row_version": moved["row_version"]})
    assert blocked.status_code == 422 and {i["path"] for i in blocked.json()["issues"]} == {"extra-new"}
    moved = world.a.patch(f"/api/applications/{draft['id']}", {"form_data": {"extra-new": "supplied"}, "row_version": moved["row_version"]}).json()
    final = world.a.post(f"/api/applications/{draft['id']}/submit", {"row_version": moved["row_version"]}).json()
    assert final["status"] == "SUBMITTED" and final["rule_version"]["number"] == 2


def test_retiring_the_version_does_not_disturb_submitted_applications(world):
    submitted = complete(world.a, create(world.a, world.cat).json())
    world.a.post(f"/api/applications/{submitted['id']}/submit", {"row_version": submitted["row_version"]})
    approver = Api()
    approver.login("approver@test.example.org")
    assert approver.post(f"/api/rules/versions/{world.cat.version_id}/retire", {"reason": "No longer applicable"}).status_code == 200
    d = world.a.get(f"/api/applications/{submitted['id']}").json()
    assert d["status"] == "SUBMITTED" and d["rule_version"]["number"] == 1 and len(d["checklist_items"]) == 6
    assert create(world.a, world.cat).status_code == 409  # and no draft can start against a retired version


def publish_v2_with_extra_required_item(cat) -> None:
    author, approver = Api(), Api()
    author.login("author@test.example.org")
    approver.login("approver@test.example.org")
    v2 = author.post(f"/api/rules/sets/{cat.rule_set_id}/versions").json()
    items = [{k: i[k] for k in i} for i in v2["checklist"]]
    src = v2["sources"][0]["id"]
    items.append({"item_key": "extra-new", "label": "New requirement", "explanation": "Added in v2.", "required": True, "item_type": "form_field", "field_kind": "text", "options": None, "validation": None, "applies_when": None, "source_id": src, "clause_ref": None, "display_order": 9})
    r = author.put(f"/api/rules/versions/{v2['id']}", {
        "academic_years": v2["academic_years"], "effective_from": None, "effective_to": None, "change_summary": "Adds a required item",
        "source_ids": [s["id"] for s in v2["sources"]], "checklist": items, "route": v2["route"], "deadlines": v2["deadlines"], "row_version": v2["row_version"],
    })
    assert r.status_code == 200, r.text
    v2 = r.json()
    assert author.post(f"/api/rules/versions/{v2['id']}/submit-review", {"row_version": v2["row_version"]}).status_code == 200
    assert approver.post(f"/api/rules/versions/{v2['id']}/publish", {"supersede": True}).status_code == 200


# ---- list, search, filters, pagination ----------------------------------------------------------------


def test_application_list_filters_search_and_pagination(world):
    other_cat = build_published(category_key="cat-b", set_key="s-b")
    apps = [create(world.a, world.cat).json() for _ in range(3)] + [create(world.a, other_cat).json()]
    done = complete(world.a, apps[0])
    world.a.post(f"/api/applications/{apps[0]['id']}/submit", {"row_version": done["row_version"]})
    create(world.b, world.cat)

    page = world.a.get("/api/applications?page_size=3").json()
    assert page["total"] == 4 and len(page["items"]) == 3 and page["page"] == 1
    assert [a["reference"] for a in page["items"]] == [a["reference"] for a in reversed(apps)][:3]  # newest first
    assert len(world.a.get("/api/applications?page_size=3&page=2").json()["items"]) == 1
    assert world.a.get("/api/applications?status=SUBMITTED").json()["total"] == 1
    assert world.a.get("/api/applications?status=DRAFT").json()["total"] == 3
    assert world.a.get(f"/api/applications?category_id={other_cat.category_id}").json()["total"] == 1
    assert world.a.get("/api/applications?academic_year=2025-26").json()["total"] == 0
    assert world.a.get(f"/api/applications?q={apps[1]['reference'][4:9].lower()}").json()["total"] >= 1
    assert world.a.get("/api/applications?status=APPROVED").status_code == 422  # no invented states
    assert world.a.get("/api/applications?page_size=500").status_code == 422
    item = next(i for i in world.a.get("/api/applications").json()["items"] if i["reference"] == apps[0]["reference"])
    assert item["status"] == "SUBMITTED" and item["outstanding_action"] is None and item["checklist"]["required_supplied"] == 4
    draft_item = next(i for i in world.a.get("/api/applications").json()["items"] if i["reference"] == apps[1]["reference"])
    assert draft_item["outstanding_action"] == "Supply 4 required items"
    assert world.b.get("/api/applications").json()["total"] == 1
