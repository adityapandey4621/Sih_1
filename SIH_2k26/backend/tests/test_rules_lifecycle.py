import pytest
from sqlalchemy import select, text

from app.config import Settings, get_settings
from app.db import SessionLocal
from app.domain import Classification, SourceVerification
from app.guards import ImmutableRecordError
from app.main import app as fastapi_app
from app.models import AuditEvent, User
from app.models_catalogue import ChecklistItem, ConfigurationGap, RuleSource, RuleVersion
from app.rbac import Role
from app.services import rules as svc
from tests.catalogue_helpers import build_published, checklist, make_category, make_source, route, users
from tests.conftest import Api, make_user


def staff(email: str) -> Api:
    c = Api()
    assert c.login(email).status_code == 200
    return c


def content_payload(version: dict, **over) -> dict:
    base = {
        "academic_years": ["2026-27"], "effective_from": None, "effective_to": None, "change_summary": "A draft",
        "source_ids": [], "checklist": [], "route": [], "deadlines": [], "row_version": version["row_version"],
    }
    return {**base, **over}


def new_set(author: Api, category_id, key="set-x", classification="DEMO") -> dict:
    r = author.post("/api/rules/sets", {"key": key, "title": "Set X", "category_id": str(category_id), "classification": classification})
    assert r.status_code == 201, r.text
    return r.json()


@pytest.fixture
def people():
    users()
    return staff("author@test.example.org"), staff("approver@test.example.org")


# ---- 9, 10: author drafts; only an approver publishes ------------------------------------------


def test_author_creates_a_draft_without_publishing(people):
    author, _ = people
    cat = make_category()
    v = new_set(author, cat)
    assert v["status"] == "draft" and v["version_number"] == 1 and v["published_at"] is None and v["content_hash"] is None
    assert v["demo_banner"].startswith("DEMO ONLY")


def test_author_cannot_publish_or_retire_or_request_changes_and_approver_cannot_edit(people):
    author, approver = people
    c = build_published(set_key="s1")
    for path, body in (("publish", {}), ("retire", {"reason": "because"}), ("request-changes", {"note": "please change"})):
        r = author.post(f"/api/rules/versions/{c.version_id}/{path}", body)
        assert r.status_code == 403, path
    v = approver.get(f"/api/rules/versions/{c.version_id}").json()
    assert approver.put(f"/api/rules/versions/{c.version_id}", content_payload(v)).status_code == 403
    assert approver.post("/api/rules/sets", {"key": "k", "title": "t", "category_id": str(c.category_id), "classification": "DEMO"}).status_code == 403


@pytest.mark.parametrize("role", [Role.INSTITUTE_USER, Role.SCRUTINY_OFFICER, Role.EVC_COORDINATOR, Role.EVC_MEMBER, Role.SHC_MEMBER, Role.EXECUTIVE_COMMITTEE, Role.SAC_MEMBER, Role.PLATFORM_ADMIN])
def test_other_roles_cannot_touch_the_rule_catalogue(role):
    c = build_published(set_key="s1")
    make_user(role, "other@test.example.org", institution=role is Role.INSTITUTE_USER)
    other = staff("other@test.example.org")
    assert other.get("/api/rules/sets").status_code == 403
    assert other.get(f"/api/rules/versions/{c.version_id}").status_code == 403
    assert other.post("/api/rules/categories", {"key": "x", "name": "X"}).status_code == 403
    assert other.post(f"/api/rules/versions/{c.version_id}/publish", {}).status_code == 403
    with SessionLocal() as db:
        denied = [e for e in db.scalars(select(AuditEvent).where(AuditEvent.action == "security.permission_denied"))]
    assert denied, "refusals are audited"


def test_auditor_can_read_but_not_change(people):
    c = build_published(set_key="s1")
    make_user(Role.AUDITOR, "aud@test.example.org")
    aud = staff("aud@test.example.org")
    assert aud.get(f"/api/rules/versions/{c.version_id}").status_code == 200
    assert aud.post("/api/rules/sets", {"key": "k", "title": "t", "category_id": str(c.category_id), "classification": "DEMO"}).status_code == 403


# ---- 11: incomplete / unverified configuration is not official ---------------------------------


def test_official_version_with_unverified_source_cannot_be_published(people):
    author, approver = people
    cat = make_category()
    with SessionLocal() as db:
        aid = db.scalar(select(User.id).where(User.email == "author@test.example.org"))
    src = make_source(aid, SourceVerification.SECONDARY_UNVERIFIED, "Secondary summary")
    v = new_set(author, cat, key="official-1", classification="OFFICIAL")
    v = author.put(f"/api/rules/versions/{v['id']}", content_payload(v, source_ids=[str(src)], checklist=[i.model_dump(mode="json") for i in checklist(src)], route=[s.model_dump(mode="json") for s in route(src)])).json()
    assert v["verification"] == "unverified"
    v = author.post(f"/api/rules/versions/{v['id']}/submit-review", {"row_version": v["row_version"]}).json()
    assert v["status"] == "in_review"
    r = approver.post(f"/api/rules/versions/{v['id']}/publish", {})
    assert r.status_code == 422 and r.json()["code"] == "publication_blocked"
    assert "source_not_verified" in {i["code"] for i in r.json()["issues"]}
    assert approver.get(f"/api/rules/versions/{v['id']}").json()["status"] == "in_review"


def test_version_with_no_sources_checklist_or_route_is_blocked(people):
    author, approver = people
    v = new_set(author, make_category())
    issues = {i["code"] for i in v["publication_issues"]}
    assert {"sources_missing", "checklist_missing", "route_missing", "years_missing", "summary_missing"} <= issues
    r = author.post(f"/api/rules/versions/{v['id']}/submit-review", {"row_version": v["row_version"]})
    assert r.status_code == 422 and r.json()["code"] == "incomplete_configuration"


def test_route_problems_are_reported(people):
    author, _ = people
    cat = make_category()
    with SessionLocal() as db:
        aid = db.scalar(select(User.id).where(User.email == "author@test.example.org"))
    src = make_source(aid, SourceVerification.DEMO_ONLY)
    v = new_set(author, cat)
    bad = [
        {"stage_key": "a", "label": "A", "display_order": 0, "next_stage_keys": ["b", "ghost"]},
        {"stage_key": "b", "label": "B", "display_order": 1, "next_stage_keys": ["a"], "is_conditional": True},
        {"stage_key": "orphan", "label": "Orphan", "display_order": 2, "next_stage_keys": []},
    ]
    v = author.put(f"/api/rules/versions/{v['id']}", content_payload(v, source_ids=[str(src)], route=bad)).json()
    codes = {i["code"] for i in v["publication_issues"]}
    assert {"route_unknown_next", "route_condition_missing"} <= codes
    fixed = [
        {"stage_key": "a", "label": "A", "display_order": 0, "next_stage_keys": ["b"]},
        {"stage_key": "b", "label": "B", "display_order": 1, "next_stage_keys": ["a"]},
        {"stage_key": "orphan", "label": "Orphan", "display_order": 2, "next_stage_keys": []},
    ]
    v = author.put(f"/api/rules/versions/{v['id']}", content_payload(v, source_ids=[str(src)], route=fixed)).json()
    codes = {i["code"] for i in v["publication_issues"]}
    assert {"route_cycle", "route_unreachable"} <= codes


def test_duration_without_source_and_uncited_source_are_blocked(people):
    author, _ = people
    cat = make_category()
    with SessionLocal() as db:
        aid = db.scalar(select(User.id).where(User.email == "author@test.example.org"))
    s1, s2 = make_source(aid, SourceVerification.DEMO_ONLY, "one"), make_source(aid, SourceVerification.DEMO_ONLY, "two")
    v = new_set(author, cat)
    dl = {"policy_key": "p", "label": "P", "deadline_type": "STATUTORY", "trigger_event": "x", "duration_value": 7, "duration_unit": "days"}
    v = author.put(f"/api/rules/versions/{v['id']}", content_payload(v, source_ids=[str(s1)], deadlines=[dl])).json()
    assert "duration_unsourced" in {i["code"] for i in v["publication_issues"]}
    v = author.put(f"/api/rules/versions/{v['id']}", content_payload(v, source_ids=[str(s1)], deadlines=[{**dl, "source_id": str(s2)}])).json()
    assert "source_not_cited" in {i["code"] for i in v["publication_issues"]}


def test_demo_rules_cannot_be_published_where_demo_is_disabled(people):
    c = build_published(set_key="s1", publish=False)
    fastapi_app.dependency_overrides[get_settings] = lambda: Settings(environment="test", allow_demo_rules=False)
    try:
        approver = staff("approver@test.example.org")
        author = staff("author@test.example.org")
        v = author.get(f"/api/rules/versions/{c.version_id}").json()
        assert author.post(f"/api/rules/versions/{c.version_id}/submit-review", {"row_version": v["row_version"]}).status_code == 200
        r = approver.post(f"/api/rules/versions/{c.version_id}/publish", {})
        assert r.status_code == 422 and "demo_not_permitted" in {i["code"] for i in r.json()["issues"]}
    finally:
        fastapi_app.dependency_overrides.pop(get_settings, None)


# ---- 12, 13: published versions are immutable; changes need a new version ----------------------


def test_published_version_cannot_be_edited_through_the_api(people):
    author, _ = people
    c = build_published(set_key="s1")
    v = author.get(f"/api/rules/versions/{c.version_id}").json()
    assert v["status"] == "published" and v["content_hash"] and len(v["content_hash"]) == 64
    r = author.put(f"/api/rules/versions/{c.version_id}", content_payload(v, change_summary="sneaky"))
    assert r.status_code == 409 and r.json()["code"] == "not_editable"
    assert author.post(f"/api/rules/versions/{c.version_id}/submit-review", {"row_version": v["row_version"]}).status_code == 409


def test_published_content_is_protected_below_the_service_layer_too():
    c = build_published(set_key="s1")
    with SessionLocal() as db:
        item = db.scalar(select(ChecklistItem).where(ChecklistItem.rule_version_id == c.version_id))
        item.label = "tampered"
        with pytest.raises(ImmutableRecordError):
            db.flush()
        db.rollback()
        ver = db.get(RuleVersion, c.version_id)
        ver.change_summary = "tampered"
        with pytest.raises(ImmutableRecordError):
            db.flush()
        db.rollback()
        ver = db.get(RuleVersion, c.version_id)
        ver.snapshot = {"x": 1}
        with pytest.raises(ImmutableRecordError):
            db.flush()
        db.rollback()
        db.delete(db.scalar(select(ChecklistItem).where(ChecklistItem.rule_version_id == c.version_id)))
        with pytest.raises(ImmutableRecordError):
            db.flush()


def test_snapshot_is_frozen_and_hash_matches_content():
    c = build_published(set_key="s1")
    with SessionLocal() as db:
        v = db.get(RuleVersion, c.version_id)
        assert svc.snapshot_hash(v.snapshot) == v.content_hash
        assert v.snapshot["rule_set"]["classification"] == "DEMO"
        assert [i["item_key"] for i in v.snapshot["checklist"]][:2] == ["contact", "kind"]
        assert v.snapshot["sources"][0]["verification_status"] == "DEMO_ONLY"


def test_changes_need_a_new_version_and_only_one_open_version_at_a_time(people):
    author, approver = people
    c = build_published(set_key="s1")
    r = author.post(f"/api/rules/sets/{c.rule_set_id}/versions")
    assert r.status_code == 201
    v2 = r.json()
    assert v2["version_number"] == 2 and v2["status"] == "draft" and v2["supersedes_version_id"] == str(c.version_id)
    assert len(v2["checklist"]) == 6 and len(v2["route"]) == 2  # starting point cloned from v1
    assert author.post(f"/api/rules/sets/{c.rule_set_id}/versions").status_code == 409  # one open version at a time
    assert author.get(f"/api/rules/versions/{c.version_id}").json()["change_summary"] == "Initial test version"  # v1 untouched


def test_source_cited_by_a_reviewed_version_is_locked(people):
    author, _ = people
    c = build_published(set_key="s1")
    with SessionLocal() as db:
        sid = db.scalar(select(RuleSource.id).limit(1))
    src = next(s for s in author.get("/api/rules/sources").json() if s["id"] == str(sid))
    assert src["locked"] is True
    body = {k: src[k] for k in ("title", "issuing_authority", "url", "document_ref", "clause_ref", "page_ref", "publication_date", "effective_date", "retrieved_date", "verification_status", "applicability_notes")}
    r = author.put(f"/api/rules/sources/{sid}", {**body, "verification_status": "VERIFIED_OFFICIAL"})
    assert r.status_code == 409 and r.json()["code"] == "immutable_record"
    with SessionLocal() as db:
        assert db.get(RuleSource, sid).verification_status == "DEMO_ONLY"


# ---- review workflow ---------------------------------------------------------------------------


def test_request_changes_returns_to_draft_with_a_note_and_resubmission_works(people):
    author, approver = people
    c = build_published(set_key="s1", publish=False)
    v = author.get(f"/api/rules/versions/{c.version_id}").json()
    v = author.post(f"/api/rules/versions/{c.version_id}/submit-review", {"row_version": v["row_version"]}).json()
    assert v["status"] == "in_review"
    assert author.put(f"/api/rules/versions/{c.version_id}", content_payload(v)).status_code == 409  # frozen while in review
    assert approver.post(f"/api/rules/versions/{c.version_id}/request-changes", {"note": "x"}).status_code == 422  # note too short
    v = approver.post(f"/api/rules/versions/{c.version_id}/request-changes", {"note": "Please cite the clause."}).json()
    assert v["status"] == "draft" and v["review_note"] == "Please cite the clause."
    v = author.post(f"/api/rules/versions/{c.version_id}/submit-review", {"row_version": v["row_version"]}).json()
    assert approver.post(f"/api/rules/versions/{c.version_id}/publish", {"note": "ok"}).json()["status"] == "published"
    assert [e["action"] for e in v["events"]][:2] == ["created", "draft_edited"]


def test_invalid_transitions_are_refused_by_the_backend(people):
    author, approver = people
    c = build_published(set_key="s1", publish=False)  # draft
    assert approver.post(f"/api/rules/versions/{c.version_id}/publish", {}).status_code == 409  # draft -> published not allowed
    assert approver.post(f"/api/rules/versions/{c.version_id}/retire", {"reason": "not yet published"}).status_code == 409
    assert approver.post(f"/api/rules/versions/{c.version_id}/request-changes", {"note": "not in review"}).status_code == 409


def test_author_cannot_approve_even_with_both_capabilities_bypassed():
    """Defence in depth: the service itself refuses a contributor as approver."""
    c = build_published(set_key="s1", publish=False)
    settings = get_settings()
    with SessionLocal() as db:
        author = db.query(User).filter_by(email="author@test.example.org").one()
        v = svc.load_version(db, c.version_id)
        svc.submit_for_review(db, v.id, v.row_version, None, author, None, settings)
        db.commit()
        with pytest.raises(Exception) as exc:
            svc.publish(db, c.version_id, False, None, author, None, settings)
        assert getattr(exc.value, "code", "") == "self_approval"


def test_stale_edit_is_rejected(people):
    author, _ = people
    v = new_set(author, make_category())
    assert author.put(f"/api/rules/versions/{v['id']}", content_payload(v, row_version=v["row_version"] + 5)).status_code == 409
    assert author.put(f"/api/rules/versions/{v['id']}", content_payload(v)).status_code == 200
    assert author.put(f"/api/rules/versions/{v['id']}", content_payload(v)).json()["code"] == "stale_version"  # old row_version again


# ---- 14: retiring preserves history ------------------------------------------------------------


def test_retiring_preserves_content_history_and_snapshot(people):
    author, approver = people
    c = build_published(set_key="s1")
    r = approver.post(f"/api/rules/versions/{c.version_id}/retire", {"reason": "Superseded by new guidance"})
    assert r.status_code == 200
    v = r.json()
    assert v["status"] == "retired" and v["retirement_reason"] and len(v["checklist"]) == 6 and v["content_hash"]
    assert [e["action"] for e in v["events"]][-2:] == ["published", "retired"]
    with SessionLocal() as db:
        assert db.get(RuleVersion, c.version_id).snapshot["checklist"]
    assert approver.post(f"/api/rules/versions/{c.version_id}/retire", {"reason": "again please"}).status_code == 409


# ---- 7, ambiguity: overlapping active configuration never silently selectable ------------------


def test_overlapping_publication_is_blocked_unless_explicitly_superseding_the_same_rule_set(people):
    author, approver = people
    c = build_published(set_key="s1")
    # another rule set for the same category and year
    with SessionLocal() as db:
        aid = db.scalar(select(User.id).where(User.email == "author@test.example.org"))
    other = build_published(category_key="cat-a", set_key="s2", publish=False)
    v = author.get(f"/api/rules/versions/{other.version_id}").json()
    author.post(f"/api/rules/versions/{other.version_id}/submit-review", {"row_version": v["row_version"]})
    r = approver.post(f"/api/rules/versions/{other.version_id}/publish", {})
    assert r.status_code == 422 and "overlap_other_rule_set" in {i["code"] for i in r.json()["issues"]}

    # same rule set: new version must be published with explicit supersede
    v2 = author.post(f"/api/rules/sets/{c.rule_set_id}/versions").json()
    author.put(f"/api/rules/versions/{v2['id']}", content_payload(v2, source_ids=[s["id"] for s in v2["sources"]], checklist=[{k: i[k] for k in i if k != "source_id"} | {"source_id": i["source_id"]} for i in v2["checklist"]], route=[{k: s[k] for k in s} for s in v2["route"]], change_summary="v2 wording"))
    v2 = author.get(f"/api/rules/versions/{v2['id']}").json()
    author.post(f"/api/rules/versions/{v2['id']}/submit-review", {"row_version": v2["row_version"]})
    r = approver.post(f"/api/rules/versions/{v2['id']}/publish", {})
    assert r.status_code == 422 and "overlap_same_rule_set" in {i["code"] for i in r.json()["issues"]}
    r = approver.post(f"/api/rules/versions/{v2['id']}/publish", {"supersede": True})
    assert r.status_code == 200 and r.json()["status"] == "published"
    assert author.get(f"/api/rules/versions/{c.version_id}").json()["status"] == "retired"
    with SessionLocal() as db:
        actions = [e.action for e in db.scalars(select(AuditEvent).order_by(AuditEvent.occurred_at))]
    assert actions.count("rule.published") == 2 and "rule.retired" in actions


# ---- 15: missing / ambiguous rules never produce a guessed route -------------------------------


def test_resolution_reports_no_version_and_ambiguity_instead_of_guessing():
    from datetime import date

    settings = get_settings()
    c = build_published(set_key="s1")
    with SessionLocal() as db:
        assert svc.resolve_version(db, c.category_id, "2026-27", date.today(), settings).version is not None
        none = svc.resolve_version(db, c.category_id, "2031-32", date.today(), settings)
        assert none.version is None and none.reason == "no_approved_version"
        # force an impossible state (two published versions in force) to prove it is reported, not resolved
        other = build_published(set_key="s2", publish=False)
        db.execute(text("update rule_versions set status='published', snapshot='{}' where id = :i"), {"i": other.version_id.hex if db.bind.dialect.name == "sqlite" else str(other.version_id)})
        db.commit()
        db.expire_all()
        amb = svc.resolve_version(db, c.category_id, "2026-27", date.today(), settings)
        assert amb.version is None and amb.reason == "ambiguous" and len(amb.detail["candidates"]) == 2


def test_effective_dates_are_respected():
    from datetime import date, timedelta

    c = build_published(set_key="s1")
    with SessionLocal() as db:
        v = db.get(RuleVersion, c.version_id)
        # simulate a version whose effective window has not started (frozen content: use SQL, as an operator would not)
        db.execute(text("update rule_versions set effective_from = :d where id = :i"), {"d": date.today() + timedelta(days=30), "i": c.version_id.hex if db.bind.dialect.name == "sqlite" else str(c.version_id)})
        db.commit()
        db.expire_all()
        assert svc.resolve_version(db, c.category_id, "2026-27", date.today(), get_settings()).reason == "no_approved_version"


def test_audit_records_authoring_review_and_publication(people):
    c = build_published(set_key="s1")
    with SessionLocal() as db:
        events = list(db.scalars(select(AuditEvent).where(AuditEvent.action.like("rule.%")).order_by(AuditEvent.occurred_at)))
    actions = [e.action for e in events]
    assert actions[:4] == ["rule.draft_created", "rule.draft_edited", "rule.submitted_for_review", "rule.published"]
    pub = events[3]
    assert pub.actor_label == "approver@test.example.org" and pub.detail["content_hash"] and pub.target_type == "rule_version"


def test_configuration_gap_is_recorded_and_closed_by_publication(people):
    # recorded through the institution flow in test_applications; here: publication resolves open gaps
    from app.models import Institution  # noqa: F401

    c = build_published(set_key="s1", publish=False)
    with SessionLocal() as db:
        db.add(ConfigurationGap(category_id=c.category_id, academic_year="2026-27", reason="no_approved_version"))
        db.commit()
    author, approver = staff("author@test.example.org"), staff("approver@test.example.org")
    v = author.get(f"/api/rules/versions/{c.version_id}").json()
    author.post(f"/api/rules/versions/{c.version_id}/submit-review", {"row_version": v["row_version"]})
    approver.post(f"/api/rules/versions/{c.version_id}/publish", {})
    gaps = approver.get("/api/rules/gaps").json()
    assert gaps[0]["status"] == "resolved" and "Resolved by publication" in gaps[0]["resolution_note"]
