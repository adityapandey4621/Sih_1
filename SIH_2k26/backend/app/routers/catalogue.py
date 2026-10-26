"""What an institution may select when starting an application. Read-only, derived from published rules."""

import uuid
from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.config import Settings, get_settings
from app.db import get_db
from app.deps import CurrentUser, csrf_protect, require_permission
from app.domain import DEMO_BANNER, Classification, is_valid_academic_year
from app.models_catalogue import ApplicationCategory, RuleSet, RuleVersion
from app.presenters import verification_for_snapshot
from app.rbac import Permission
from app.schemas_applications import CatalogueOptionsOut, ResolvedChecklistOut
from app.schemas_catalogue import CategoryOut, ChecklistItemOut, RouteStageOut, SourceOut
from app.services import rules as rules_svc
from app.services.errors import ServiceError, invalid

router = APIRouter(prefix="/api/catalogue", tags=["catalogue"], dependencies=[Depends(csrf_protect)])


@router.get("/options", response_model=CatalogueOptionsOut)
def options(_: CurrentUser = Depends(require_permission(Permission.APPLICATION_CREATE)), db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> CatalogueOptionsOut:
    cats = list(db.scalars(select(ApplicationCategory).where(ApplicationCategory.is_active.is_(True)).order_by(ApplicationCategory.name)))
    published = db.scalars(select(RuleVersion).join(RuleSet, RuleSet.id == RuleVersion.rule_set_id).where(RuleVersion.status == "published").options(selectinload(RuleVersion.rule_set)))
    years = sorted({y for v in published if (v.rule_set.classification != Classification.DEMO or settings.demo_rules_enabled) for y in (v.academic_years or [])})
    return CatalogueOptionsOut(categories=[CategoryOut.model_validate(c) for c in cats], academic_years=years)


@router.get("/checklist", response_model=ResolvedChecklistOut)
def preview_checklist(
    category_id: uuid.UUID = Query(),
    academic_year: str = Query(min_length=7, max_length=7),
    _: CurrentUser = Depends(require_permission(Permission.APPLICATION_CREATE)),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> ResolvedChecklistOut:
    """Preview only; it has no side effects. The gap is recorded when a draft is actually attempted."""
    if not is_valid_academic_year(academic_year):
        raise invalid("Choose a valid academic year (for example 2026-27).")
    res = rules_svc.resolve_version(db, category_id, academic_year, date.today(), settings)
    if res.version is None:
        raise ServiceError(
            409, "no_applicable_configuration",
            "No single approved configuration applies to this category and academic year, so its requirements cannot be shown. Nothing has been guessed.",
        )
    snap = res.version.snapshot
    is_demo = snap["rule_set"]["classification"] == Classification.DEMO
    return ResolvedChecklistOut(
        rule_set_key=snap["rule_set"]["key"],
        rule_set_title=snap["rule_set"]["title"],
        version_number=snap["version"]["number"],
        is_demo=is_demo,
        demo_banner=DEMO_BANNER if is_demo else None,
        verification=verification_for_snapshot(snap),
        checklist=[ChecklistItemOut(**i) for i in snap["checklist"]],
        route=[RouteStageOut(**s) for s in snap["route"]],
        sources=[SourceOut(**s) for s in snap["sources"]],
    )
