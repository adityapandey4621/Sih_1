"""Turn ORM rows and frozen snapshots into API schemas. No business rules here."""

import uuid
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.domain import DEMO_BANNER, ApplicationStatus, Classification
from app.models import User
from app.models_applications import Application
from app.models_catalogue import RuleSource, RuleVersion, RuleVersionSource
from app.schemas_applications import (
    ApplicationDetailOut,
    ApplicationSummaryOut,
    ChecklistStateOut,
    ChecklistSummaryOut,
    DocumentOut,
    HistoryOut,
    InstitutionOut,
    RuleVersionRefOut,
)
from app.schemas_catalogue import (
    CategoryOut,
    ChecklistItemOut,
    DeadlinePolicyOut,
    IssueOut,
    RouteStageOut,
    RuleSetRef,
    SourceOut,
    VersionDetailOut,
    VersionEventOut,
    VersionSummaryOut,
)
from app.services import applications as app_svc
from app.services import checklist as cl
from app.services import rules as rules_svc
from app.services.errors import Issue


def _name(u) -> str | None:
    return u.full_name if u else None


def _issues(items: list[Issue]) -> list[IssueOut]:
    return [IssueOut(code=i.code, message=i.message, path=i.path) for i in items]


# ---- rule catalogue ----------------------------------------------------------------------------


def version_summary(v: RuleVersion) -> VersionSummaryOut:
    rs = v.rule_set
    return VersionSummaryOut(
        id=v.id,
        rule_set=RuleSetRef(id=rs.id, key=rs.key, title=rs.title, classification=rs.classification),
        category=CategoryOut.model_validate(rs.category),
        version_number=v.version_number,
        status=v.status,
        academic_years=list(v.academic_years or []),
        effective_from=v.effective_from,
        effective_to=v.effective_to,
        change_summary=v.change_summary,
        is_demo=rs.classification == Classification.DEMO,
        verification=rules_svc.verification_label(v),
        created_at=v.created_at,
        updated_at=v.updated_at,
        published_at=v.published_at,
    )


def locked_source_ids(db: Session, ids: list[uuid.UUID]) -> set[uuid.UUID]:
    if not ids:
        return set()
    return set(
        db.scalars(
            select(RuleVersionSource.source_id)
            .join(RuleVersion, RuleVersion.id == RuleVersionSource.rule_version_id)
            .where(RuleVersionSource.source_id.in_(ids), RuleVersion.status != "draft")
        )
    )


def source_out(s: RuleSource, locked: bool) -> SourceOut:
    out = SourceOut.model_validate(s)
    out.locked = locked
    return out


def version_detail(db: Session, v: RuleVersion, settings: Settings) -> VersionDetailOut:
    base = version_summary(v)
    sources = [l.source for l in v.source_links]
    locked = locked_source_ids(db, [s.id for s in sources])
    issues = rules_svc.publication_issues(db, v, settings) if v.status in ("draft", "in_review") else []
    return VersionDetailOut(
        **base.model_dump(),
        row_version=v.row_version,
        created_by=_name(_user(db, v.created_by_id)),
        submitted_by=_name(_user(db, v.submitted_by_id)),
        submitted_at=v.submitted_at,
        review_note=v.review_note,
        published_by=_name(_user(db, v.published_by_id)),
        retired_by=_name(_user(db, v.retired_by_id)),
        retired_at=v.retired_at,
        retirement_reason=v.retirement_reason,
        supersedes_version_id=v.supersedes_version_id,
        content_hash=v.content_hash,
        sources=[source_out(s, s.id in locked) for s in sorted(sources, key=lambda s: s.title)],
        checklist=[ChecklistItemOut.model_validate(i) for i in v.checklist_items],
        route=[RouteStageOut.model_validate(s) for s in v.route_stages],
        deadlines=[DeadlinePolicyOut.model_validate(p) for p in v.deadline_policies],
        events=[
            VersionEventOut(at=e.at, actor=_name(e.actor), action=e.action, from_status=e.from_status, to_status=e.to_status, note=e.note)
            for e in v.events
        ],
        publication_issues=_issues(issues),
        demo_banner=DEMO_BANNER if v.rule_set.classification == Classification.DEMO else None,
    )


def _user(db: Session, user_id: uuid.UUID | None) -> User | None:
    return db.get(User, user_id) if user_id else None


# ---- applications ------------------------------------------------------------------------------


def verification_for_snapshot(snap: dict) -> str:
    if snap["rule_set"]["classification"] == Classification.DEMO:
        return "demo"
    srcs = snap["sources"]
    return "verified" if srcs and all(s["verification_status"] == "VERIFIED_OFFICIAL" for s in srcs) else "unverified"


def _outstanding_action(app: Application, summary: dict) -> str | None:
    if app.status == ApplicationStatus.SUBMITTED:
        return None
    missing = summary["required_total"] - summary["required_supplied"]
    return f"Supply {missing} required item{'s' if missing != 1 else ''}" if missing else "Ready to review and submit"


def application_summary(app: Application) -> ApplicationSummaryOut:
    states = app_svc.checklist_states(app)
    summary = cl.summarise(states)
    cfg = app_svc.config_of(app)
    return ApplicationSummaryOut(
        id=app.id,
        reference=app.reference,
        category=CategoryOut.model_validate(app.category),
        academic_year=app.academic_year,
        status=app.status,
        created_at=app.created_at,
        updated_at=app.updated_at,
        submitted_at=app.submitted_at,
        row_version=app.row_version,
        is_demo=cfg["rule_set"]["classification"] == Classification.DEMO,
        checklist=ChecklistSummaryOut(**summary),
        outstanding_action=_outstanding_action(app, summary),
    )


def application_detail(db: Session, app: Application, settings: Settings) -> ApplicationDetailOut:
    states = app_svc.checklist_states(app)
    summary = cl.summarise(states)
    cfg = app_svc.config_of(app)
    is_demo = cfg["rule_set"]["classification"] == Classification.DEMO
    inputs = cl.input_items(cfg)
    differs = False
    if app.status == ApplicationStatus.DRAFT:
        res = rules_svc.resolve_version(db, app.category_id, app.academic_year, date.today(), settings)
        differs = res.version is None or res.version.id != app.rule_version_id
    docs = [
        DocumentOut(
            id=d.id, item_key=d.item_key, original_filename=d.original_filename, media_type=d.media_type, size_bytes=d.size_bytes,
            sha256=d.sha256, uploaded_at=d.uploaded_at, uploaded_by=_name(d.uploaded_by), removed=d.removed_at is not None,
            verification_status=d.verification_status,
        )
        for d in app.documents
    ]
    doc_keys = {i["item_key"] for i in cfg["checklist"]}
    orphan_docs = [d.item_key for d in app.documents if d.removed_at is None and d.item_key not in doc_keys]
    base = application_summary(app)
    return ApplicationDetailOut(
        **base.model_dump(),
        institution=InstitutionOut(id=app.institution.id, name=app.institution.name, external_identifier=app.institution.external_identifier),
        created_by=_name(app.created_by),
        submitted_by=_name(app.submitted_by),
        form_data=dict(app.form_data or {}),
        rule_version=RuleVersionRefOut(
            id=uuid.UUID(cfg["version"]["id"]),
            rule_set_key=cfg["rule_set"]["key"],
            rule_set_title=cfg["rule_set"]["title"],
            number=cfg["version"]["number"],
            classification=cfg["rule_set"]["classification"],
            is_demo=is_demo,
            demo_banner=DEMO_BANNER if is_demo else None,
            content_hash=app.rule_version.content_hash,
            verification=verification_for_snapshot(cfg),
            sources=cfg["sources"],
            route=[RouteStageOut(**s) for s in cfg["route"]],
        ),
        checklist_items=[ChecklistStateOut(**s.as_dict()) for s in states],
        documents=docs,
        history=[HistoryOut(at=e.at, actor=_name(e.actor), action=e.action, detail=e.detail) for e in app.events],
        editable=app.status == ApplicationStatus.DRAFT,
        current_rule_version_differs=differs,
        orphaned_data_keys=sorted(k for k in (app.form_data or {}) if k not in inputs) + sorted(set(orphan_docs)),
    )
