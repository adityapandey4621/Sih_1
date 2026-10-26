"""Rule catalogue: content editing, publication gates, snapshots, selection and lifecycle.

Nothing here knows any regulatory content. It enforces *how* configuration may be authored,
reviewed, published and selected, so that regulatory content stays in versioned data.
"""

import hashlib
import json
import uuid
from datetime import date

from fastapi import Request
from sqlalchemy import select, update
from sqlalchemy.orm import Session, selectinload

from app import audit
from app.config import Settings
from app.domain import (
    ALLOWED_TRANSITIONS,
    Classification,
    GapReason,
    ItemType,
    SourceVerification,
    VersionStatus,
)
from app.models import User, utcnow
from app.models_catalogue import (
    ApplicationCategory,
    ChecklistItem,
    ConfigurationGap,
    DeadlinePolicy,
    RouteStage,
    RuleSet,
    RuleSource,
    RuleVersion,
    RuleVersionEvent,
    RuleVersionSource,
)
from app.schemas_catalogue import RuleSetCreateIn, VersionContentIn
from app.services.errors import Issue, conflict, invalid, not_found


def _event(db: Session, version: RuleVersion, actor: User | None, action: str, frm: str | None = None, to: str | None = None, note: str | None = None) -> None:
    db.add(RuleVersionEvent(rule_version_id=version.id, actor_id=actor.id if actor else None, action=action, from_status=frm, to_status=to, note=note))


def _audit(db: Session, request: Request | None, actor: User, action: str, version: RuleVersion, **detail) -> None:
    audit.record(
        db, action, request=request, actor=actor, target_type="rule_version", target_id=version.id,
        detail={"rule_set": version.rule_set.key, "version": version.version_number, **detail},
    )


# ---- loading -----------------------------------------------------------------------------------


def load_version(db: Session, version_id: uuid.UUID, *, lock: bool = False) -> RuleVersion:
    q = (
        select(RuleVersion)
        .where(RuleVersion.id == version_id)
        .options(
            selectinload(RuleVersion.rule_set).selectinload(RuleSet.category),
            selectinload(RuleVersion.source_links).selectinload(RuleVersionSource.source),
            selectinload(RuleVersion.checklist_items),
            selectinload(RuleVersion.route_stages),
            selectinload(RuleVersion.deadline_policies),
            selectinload(RuleVersion.events).selectinload(RuleVersionEvent.actor),
        )
    )
    if lock:
        q = q.with_for_update()
    # Sessions keep objects after commit, so always refresh rows and collections from the database.
    v = db.scalars(q.execution_options(populate_existing=True)).unique().one_or_none()
    if v is None:
        raise not_found("Rule version")
    return v


def verification_label(version: RuleVersion) -> str:
    """verified: official classification and every cited source read in the official publication."""
    if version.rule_set.classification == Classification.DEMO:
        return "demo"
    statuses = [l.source.verification_status for l in version.source_links]
    return "verified" if statuses and all(s == SourceVerification.VERIFIED_OFFICIAL for s in statuses) else "unverified"


# ---- creation and editing ----------------------------------------------------------------------


def create_rule_set(db: Session, payload: RuleSetCreateIn, actor: User, request: Request | None) -> RuleVersion:
    category = db.get(ApplicationCategory, payload.category_id)
    if category is None:
        raise not_found("Category")
    if db.scalar(select(RuleSet.id).where(RuleSet.key == payload.key)):
        raise conflict("duplicate_key", f"A rule set with key '{payload.key}' already exists.")
    rs = RuleSet(key=payload.key, title=payload.title, category_id=category.id, classification=payload.classification.value, created_by_id=actor.id)
    db.add(rs)
    db.flush()
    version = RuleVersion(rule_set_id=rs.id, version_number=1, status="draft", created_by_id=actor.id, change_summary="")
    db.add(version)
    db.flush()
    _event(db, version, actor, "created", to="draft", note="Rule set created")
    version = load_version(db, version.id)
    _audit(db, request, actor, "rule.draft_created", version, classification=payload.classification.value)
    return version


def new_draft_version(db: Session, rule_set_id: uuid.UUID, actor: User, request: Request | None) -> RuleVersion:
    rs = db.get(RuleSet, rule_set_id)
    if rs is None:
        raise not_found("Rule set")
    open_ = db.scalar(
        select(RuleVersion).where(RuleVersion.rule_set_id == rs.id, RuleVersion.status.in_(("draft", "in_review"))).limit(1)
    )
    if open_ is not None:
        raise conflict("open_version_exists", f"Version {open_.version_number} of this rule set is still {open_.status.replace('_', ' ')}; finish or return it first.")
    latest = db.scalars(select(RuleVersion).where(RuleVersion.rule_set_id == rs.id).order_by(RuleVersion.version_number.desc()).limit(1)).one_or_none()
    number = (latest.version_number if latest else 0) + 1
    new = RuleVersion(rule_set_id=rs.id, version_number=number, status="draft", created_by_id=actor.id, supersedes_version_id=latest.id if latest else None)
    db.add(new)
    db.flush()
    if latest is not None:  # clone content as the starting point; the new version is independent
        src = load_version(db, latest.id)
        new.academic_years = list(src.academic_years)
        new.effective_from, new.effective_to = src.effective_from, src.effective_to
        new.change_summary = ""
        for l in src.source_links:
            db.add(RuleVersionSource(rule_version_id=new.id, source_id=l.source_id, note=l.note))
        for i in src.checklist_items:
            db.add(ChecklistItem(rule_version_id=new.id, **_cols(i, ChecklistItem)))
        for s in src.route_stages:
            db.add(RouteStage(rule_version_id=new.id, **_cols(s, RouteStage)))
        for p in src.deadline_policies:
            db.add(DeadlinePolicy(rule_version_id=new.id, **_cols(p, DeadlinePolicy)))
    _event(db, new, actor, "created", to="draft", note=f"Draft version {number}" + (f" based on version {latest.version_number}" if latest else ""))
    db.flush()  # autoflush is off: the cloned children must exist before the version is (re)loaded
    db.expire(new)
    version = load_version(db, new.id)
    _audit(db, request, actor, "rule.draft_created", version, based_on=latest.version_number if latest else None)
    return version


_SKIP = {"id", "rule_version_id", "version", "source"}


def _cols(obj, model) -> dict:
    return {c.key: getattr(obj, c.key) for c in model.__mapper__.column_attrs if c.key not in _SKIP}


def replace_draft_content(db: Session, version_id: uuid.UUID, payload: VersionContentIn, actor: User, request: Request | None) -> RuleVersion:
    version = load_version(db, version_id, lock=True)
    if version.status != VersionStatus.DRAFT:
        raise conflict("not_editable", f"This version is {version.status.replace('_', ' ')} and can no longer be edited. Create a new version to make changes.")
    if payload.row_version != version.row_version:
        raise conflict("stale_version", "This draft was changed by someone else since you opened it. Reload to see the latest content.")

    wanted_sources = set(payload.source_ids)
    referenced = {x for x in [*(i.source_id for i in payload.checklist), *(s.source_id for s in payload.route), *(p.source_id for p in payload.deadlines)] if x}
    existing = set(db.scalars(select(RuleSource.id).where(RuleSource.id.in_(wanted_sources | referenced))))
    missing = (wanted_sources | referenced) - existing
    if missing:
        raise invalid("Some referenced sources do not exist.", [Issue("unknown_source", f"Source {m} not found.") for m in sorted(map(str, missing))])

    version.academic_years = list(payload.academic_years)
    version.effective_from = payload.effective_from
    version.effective_to = payload.effective_to
    version.change_summary = payload.change_summary

    # Replace children. Flush the deletions first so re-used keys do not trip unique constraints.
    version.source_links.clear()
    version.checklist_items.clear()
    version.route_stages.clear()
    version.deadline_policies.clear()
    db.flush()
    for sid in payload.source_ids:
        version.source_links.append(RuleVersionSource(source_id=sid))
    for n, i in enumerate(payload.checklist):
        version.checklist_items.append(ChecklistItem(**{**i.model_dump(mode="json", exclude={"source_id"}), "source_id": i.source_id, "display_order": i.display_order or n}))
    for n, s in enumerate(payload.route):
        version.route_stages.append(RouteStage(**{**s.model_dump(mode="json", exclude={"source_id"}), "source_id": s.source_id, "display_order": s.display_order or n}))
    for p in payload.deadlines:
        version.deadline_policies.append(DeadlinePolicy(**{**p.model_dump(mode="json", exclude={"source_id"}), "source_id": p.source_id}))
    version.row_version += 1
    version.updated_at = utcnow()
    db.flush()
    _event(db, version, actor, "draft_edited", note=None)
    _audit(db, request, actor, "rule.draft_edited", version, checklist=len(payload.checklist), stages=len(payload.route))
    return load_version(db, version.id)


# ---- publication gates -------------------------------------------------------------------------


def _ranges_overlap(a_from: date | None, a_to: date | None, b_from: date | None, b_to: date | None) -> bool:
    lo = max(a_from or date.min, b_from or date.min)
    hi = min(a_to or date.max, b_to or date.max)
    return lo <= hi


def _route_issues(stages: list[RouteStage]) -> list[Issue]:
    out: list[Issue] = []
    if not stages:
        return [Issue("route_missing", "The route has no stages. Missing route information is never guessed.", "route")]
    keys = {s.stage_key for s in stages}
    for s in stages:
        for nk in s.next_stage_keys or []:
            if nk not in keys:
                out.append(Issue("route_unknown_next", f"Stage '{s.stage_key}' points to unknown stage '{nk}'.", f"route.{s.stage_key}"))
            if nk == s.stage_key:
                out.append(Issue("route_self_loop", f"Stage '{s.stage_key}' points to itself.", f"route.{s.stage_key}"))
        if s.is_conditional and not (s.condition_description or "").strip():
            out.append(Issue("route_condition_missing", f"Conditional stage '{s.stage_key}' must describe when it applies.", f"route.{s.stage_key}"))
    if out:
        return out
    ordered = sorted(stages, key=lambda s: s.display_order)
    graph = {s.stage_key: list(s.next_stage_keys or []) for s in stages}
    # reachability from the first stage
    seen: set[str] = set()
    stack = [ordered[0].stage_key]
    while stack:
        k = stack.pop()
        if k not in seen:
            seen.add(k)
            stack.extend(graph[k])
    for k in sorted(keys - seen):
        out.append(Issue("route_unreachable", f"Stage '{k}' cannot be reached from the first stage '{ordered[0].stage_key}'.", f"route.{k}"))
    # cycles
    state: dict[str, int] = {}

    def has_cycle(k: str) -> bool:
        state[k] = 1
        for n in graph[k]:
            if state.get(n) == 1 or (state.get(n) is None and has_cycle(n)):
                return True
        state[k] = 2
        return False

    if any(state.get(k) is None and has_cycle(k) for k in graph):
        out.append(Issue("route_cycle", "The route contains a cycle; routes must move forward.", "route"))
    if not any(not graph[k] for k in graph):
        out.append(Issue("route_no_end", "The route has no final stage.", "route"))
    return out


def publication_issues(db: Session, version: RuleVersion, settings: Settings) -> list[Issue]:
    """Everything that currently stops this version being published. Empty list = ready."""
    out: list[Issue] = []
    rs = version.rule_set
    if not rs.category.is_active:
        out.append(Issue("category_inactive", "The category is inactive.", "category"))
    if not version.academic_years:
        out.append(Issue("years_missing", "Select at least one academic year this version applies to.", "academic_years"))
    if not (version.change_summary or "").strip():
        out.append(Issue("summary_missing", "Describe what this version is or what changed.", "change_summary"))

    linked = {l.source_id: l.source for l in version.source_links}
    if not linked:
        out.append(Issue("sources_missing", "Cite at least one source. Configuration without a source cannot be presented as an approved rule.", "sources"))
    if rs.classification == Classification.OFFICIAL:
        for s in linked.values():
            if s.verification_status != SourceVerification.VERIFIED_OFFICIAL:
                out.append(Issue("source_not_verified", f"Source “{s.title}” is {s.verification_status}; an official rule version needs every source verified against the official publication.", f"sources.{s.id}"))
    elif not settings.demo_rules_enabled:
        out.append(Issue("demo_not_permitted", "DEMO configuration cannot be published in this environment.", "classification"))

    def check_src(sid: uuid.UUID | None, path: str) -> None:
        if sid is not None and sid not in linked:
            out.append(Issue("source_not_cited", "This element cites a source that is not listed among the version's sources.", path))

    items = list(version.checklist_items)
    if not items:
        out.append(Issue("checklist_missing", "The checklist is empty. Required documents are never guessed.", "checklist"))
    by_key = {i.item_key: i for i in items}
    for i in items:
        p = f"checklist.{i.item_key}"
        check_src(i.source_id, p)
        if i.required and not (i.explanation or "").strip():
            out.append(Issue("explanation_missing", f"Required item '{i.item_key}' needs an explanation of what it represents.", p))
        if i.applies_when:
            ref = by_key.get(i.applies_when.get("item_key"))
            if ref is None or ref.item_type == ItemType.DOCUMENT_UPLOAD or ref.item_key == i.item_key:
                out.append(Issue("condition_invalid", f"Condition on '{i.item_key}' must refer to a different form field or declaration.", p))
            elif "in" in i.applies_when and not isinstance(i.applies_when["in"], list):
                out.append(Issue("condition_invalid", f"Condition on '{i.item_key}' must give a list for 'in'.", p))
            elif ref.options:
                vals = i.applies_when["in"] if "in" in i.applies_when else [i.applies_when.get("equals")]
                if any(v not in ref.options for v in vals):
                    out.append(Issue("condition_invalid", f"Condition on '{i.item_key}' uses a value that is not an option of '{ref.item_key}'.", p))

    for s in version.route_stages:
        check_src(s.source_id, f"route.{s.stage_key}")
    out.extend(_route_issues(list(version.route_stages)))

    for pol in version.deadline_policies:
        p = f"deadlines.{pol.policy_key}"
        check_src(pol.source_id, p)
        if pol.duration_value is not None and pol.source_id is None:
            out.append(Issue("duration_unsourced", f"Policy '{pol.policy_key}' states a duration but cites no source. Durations are never invented.", p))

    # Overlap with other published configuration for the same category (never silently ambiguous).
    others = db.scalars(
        select(RuleVersion)
        .join(RuleSet, RuleSet.id == RuleVersion.rule_set_id)
        .where(RuleSet.category_id == rs.category_id, RuleVersion.status == "published", RuleVersion.id != version.id)
        .options(selectinload(RuleVersion.rule_set))
    )
    for o in others:
        shared = sorted(set(o.academic_years or []) & set(version.academic_years or []))
        if shared and _ranges_overlap(version.effective_from, version.effective_to, o.effective_from, o.effective_to):
            same = o.rule_set_id == version.rule_set_id
            out.append(Issue(
                "overlap_same_rule_set" if same else "overlap_other_rule_set",
                f"Overlaps published {o.rule_set.key} v{o.version_number} for {', '.join(shared)}. "
                + ("Publish with explicit supersede to retire it." if same else "Retire that version first; two active configurations cannot both apply."),
                "academic_years",
            ))
    return out


# ---- snapshot ----------------------------------------------------------------------------------


def _iso(d: date | None) -> str | None:
    return d.isoformat() if d else None


def build_snapshot(version: RuleVersion) -> dict:
    rs, cat = version.rule_set, version.rule_set.category
    return {
        "schema": 1,
        "rule_set": {"id": str(rs.id), "key": rs.key, "title": rs.title, "classification": rs.classification},
        "category": {"id": str(cat.id), "key": cat.key, "name": cat.name, "description": cat.description},
        "version": {
            "id": str(version.id),
            "number": version.version_number,
            "academic_years": sorted(version.academic_years),
            "effective_from": _iso(version.effective_from),
            "effective_to": _iso(version.effective_to),
            "change_summary": version.change_summary,
        },
        "sources": [
            {
                "id": str(l.source.id), "title": l.source.title, "issuing_authority": l.source.issuing_authority, "url": l.source.url,
                "document_ref": l.source.document_ref, "clause_ref": l.source.clause_ref, "page_ref": l.source.page_ref,
                "publication_date": _iso(l.source.publication_date), "effective_date": _iso(l.source.effective_date),
                "retrieved_date": _iso(l.source.retrieved_date), "verification_status": l.source.verification_status,
                "applicability_notes": l.source.applicability_notes,
            }
            for l in sorted(version.source_links, key=lambda l: l.source.title)
        ],
        "checklist": [
            {
                "item_key": i.item_key, "label": i.label, "explanation": i.explanation, "required": i.required, "item_type": i.item_type,
                "field_kind": i.field_kind, "options": i.options, "validation": i.validation, "applies_when": i.applies_when,
                "source_id": str(i.source_id) if i.source_id else None, "clause_ref": i.clause_ref, "display_order": i.display_order,
            }
            for i in sorted(version.checklist_items, key=lambda i: (i.display_order, i.item_key))
        ],
        "route": [
            {
                "stage_key": s.stage_key, "label": s.label, "description": s.description, "display_order": s.display_order,
                "is_conditional": s.is_conditional, "condition_description": s.condition_description,
                "next_stage_keys": list(s.next_stage_keys or []), "source_id": str(s.source_id) if s.source_id else None, "clause_ref": s.clause_ref,
            }
            for s in sorted(version.route_stages, key=lambda s: (s.display_order, s.stage_key))
        ],
        "deadlines": [
            {
                "policy_key": p.policy_key, "label": p.label, "deadline_type": p.deadline_type, "trigger_event": p.trigger_event,
                "duration_value": p.duration_value, "duration_unit": p.duration_unit, "calendar_convention": p.calendar_convention,
                "pause_resume_note": p.pause_resume_note, "escalation": p.escalation,
                "source_id": str(p.source_id) if p.source_id else None, "clause_ref": p.clause_ref,
            }
            for p in sorted(version.deadline_policies, key=lambda p: p.policy_key)
        ],
    }


def snapshot_hash(snapshot: dict) -> str:
    return hashlib.sha256(json.dumps(snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


# ---- lifecycle ---------------------------------------------------------------------------------


def _require_transition(version: RuleVersion, to: VersionStatus) -> None:
    if to not in ALLOWED_TRANSITIONS[VersionStatus(version.status)]:
        raise conflict("invalid_transition", f"A {version.status.replace('_', ' ')} version cannot move to {to.value.replace('_', ' ')}.")


def submit_for_review(db: Session, version_id: uuid.UUID, row_version: int, note: str | None, actor: User, request: Request | None, settings: Settings) -> RuleVersion:
    version = load_version(db, version_id, lock=True)
    _require_transition(version, VersionStatus.IN_REVIEW)
    if row_version != version.row_version:
        raise conflict("stale_version", "This draft changed since you opened it. Reload before submitting.")
    # Cheap structural completeness at submission; full publication gates are re-run at publish time.
    blocking = [i for i in publication_issues(db, version, settings) if not i.code.startswith("overlap_") and i.code != "demo_not_permitted"]
    structural = [i for i in blocking if i.code not in ("source_not_verified",)]
    if structural:
        raise invalid("The draft is incomplete and cannot be sent for review yet.", structural, code="incomplete_configuration")
    version.status = VersionStatus.IN_REVIEW.value
    version.submitted_by_id, version.submitted_at, version.review_note = actor.id, utcnow(), None
    version.updated_at = utcnow()
    _event(db, version, actor, "submitted_for_review", "draft", "in_review", note)
    _audit(db, request, actor, "rule.submitted_for_review", version)
    db.flush()
    return load_version(db, version.id)


def request_changes(db: Session, version_id: uuid.UUID, note: str, actor: User, request: Request | None) -> RuleVersion:
    version = load_version(db, version_id, lock=True)
    _require_transition(version, VersionStatus.DRAFT)
    version.status = VersionStatus.DRAFT.value
    version.review_note = note
    version.updated_at = utcnow()
    version.row_version += 1
    _event(db, version, actor, "changes_requested", "in_review", "draft", note)
    _audit(db, request, actor, "rule.changes_requested", version)
    db.flush()
    return load_version(db, version.id)


def _contributors(db: Session, version: RuleVersion) -> set[uuid.UUID]:
    ids = {version.created_by_id, version.submitted_by_id}
    ids |= set(db.scalars(select(RuleVersionEvent.actor_id).where(RuleVersionEvent.rule_version_id == version.id, RuleVersionEvent.action.in_(("created", "draft_edited", "submitted_for_review")))))
    return {i for i in ids if i}


def publish(db: Session, version_id: uuid.UUID, supersede: bool, note: str | None, actor: User, request: Request | None, settings: Settings) -> RuleVersion:
    version = load_version(db, version_id, lock=True)
    _require_transition(version, VersionStatus.PUBLISHED)
    if actor.id in _contributors(db, version):
        raise conflict("self_approval", "You contributed to this version, so you cannot publish it. A different approver must review it.")

    # Serialise publications within a category so two overlapping versions cannot both pass the overlap check.
    db.scalars(select(ApplicationCategory.id).where(ApplicationCategory.id == version.rule_set.category_id).with_for_update()).all()

    issues = publication_issues(db, version, settings)
    superseded: list[RuleVersion] = []
    if supersede:
        same = [i for i in issues if i.code == "overlap_same_rule_set"]
        if same:
            superseded = list(db.scalars(select(RuleVersion).where(RuleVersion.rule_set_id == version.rule_set_id, RuleVersion.status == "published", RuleVersion.id != version.id)))
            issues = [i for i in issues if i.code != "overlap_same_rule_set"]
    if issues:
        raise invalid("This version cannot be published yet.", issues, code="publication_blocked")

    now = utcnow()
    for old in superseded:
        old.status = VersionStatus.RETIRED.value
        old.retired_by_id, old.retired_at = actor.id, now
        old.retirement_reason = f"Superseded by version {version.version_number}"
        _event(db, old, actor, "retired", "published", "retired", old.retirement_reason)
        _audit(db, request, actor, "rule.retired", old, reason=old.retirement_reason, superseded_by=version.version_number)

    snap = build_snapshot(version)
    version.status = VersionStatus.PUBLISHED.value
    version.published_by_id, version.published_at = actor.id, now
    version.snapshot = snap
    version.content_hash = snapshot_hash(snap)
    version.updated_at = now
    _event(db, version, actor, "published", "in_review", "published", note)
    _audit(db, request, actor, "rule.published", version, content_hash=version.content_hash, superseded=[v.version_number for v in superseded])
    db.flush()
    resolve_gaps_for(db, version, actor)
    return load_version(db, version.id)


def retire(db: Session, version_id: uuid.UUID, reason: str, actor: User, request: Request | None) -> RuleVersion:
    version = load_version(db, version_id, lock=True)
    _require_transition(version, VersionStatus.RETIRED)
    version.status = VersionStatus.RETIRED.value
    version.retired_by_id, version.retired_at, version.retirement_reason = actor.id, utcnow(), reason
    version.updated_at = utcnow()
    _event(db, version, actor, "retired", "published", "retired", reason)
    _audit(db, request, actor, "rule.retired", version, reason=reason)
    db.flush()
    return load_version(db, version.id)


# ---- selection ---------------------------------------------------------------------------------


class Resolution:
    def __init__(self, version: RuleVersion | None, reason: GapReason | None, detail: dict | None = None) -> None:
        self.version, self.reason, self.detail = version, reason, detail or {}


def resolve_version(db: Session, category_id: uuid.UUID, academic_year: str, on: date, settings: Settings) -> Resolution:
    """The single published, in-force version for a category/year, or a reason why there is not exactly one."""
    rows = db.scalars(
        select(RuleVersion)
        .join(RuleSet, RuleSet.id == RuleVersion.rule_set_id)
        .where(RuleSet.category_id == category_id, RuleVersion.status == "published")
        .options(selectinload(RuleVersion.rule_set), selectinload(RuleVersion.checklist_items), selectinload(RuleVersion.route_stages))
    ).all()
    cands = [
        v for v in rows
        if academic_year in (v.academic_years or [])
        and (v.effective_from is None or v.effective_from <= on)
        and (v.effective_to is None or on <= v.effective_to)
        and (v.rule_set.classification != Classification.DEMO or settings.demo_rules_enabled)
    ]
    if not cands:
        return Resolution(None, GapReason.NO_APPROVED_VERSION)
    if len(cands) > 1:
        return Resolution(None, GapReason.AMBIGUOUS, {"candidates": sorted(f"{c.rule_set.key} v{c.version_number}" for c in cands)})
    v = cands[0]
    if not v.checklist_items or not v.route_stages or not v.snapshot:
        return Resolution(None, GapReason.INCOMPLETE, {"candidate": f"{v.rule_set.key} v{v.version_number}"})
    return Resolution(v, None)


def record_gap(db: Session, category_id: uuid.UUID, academic_year: str, reason: GapReason, detail: dict | None) -> None:
    gap = db.scalar(select(ConfigurationGap).where(ConfigurationGap.category_id == category_id, ConfigurationGap.academic_year == academic_year, ConfigurationGap.reason == reason.value))
    now = utcnow()
    if gap is None:
        db.add(ConfigurationGap(category_id=category_id, academic_year=academic_year, reason=reason.value, detail=detail))
    else:
        gap.report_count += 1
        gap.last_reported_at = now
        gap.detail = detail
        if gap.status == "resolved":
            gap.status, gap.resolved_at, gap.resolved_by_id, gap.resolution_note = "open", None, None, None


def resolve_gaps_for(db: Session, version: RuleVersion, actor: User) -> None:
    """A newly published version closes open gaps for the category/years it now covers."""
    db.execute(
        update(ConfigurationGap)
        .where(ConfigurationGap.category_id == version.rule_set.category_id, ConfigurationGap.academic_year.in_(version.academic_years), ConfigurationGap.status == "open")
        .values(status="resolved", resolved_at=utcnow(), resolved_by_id=actor.id, resolution_note=f"Resolved by publication of {version.rule_set.key} v{version.version_number}")
    )
