import uuid

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app import audit, presenters
from app.config import Settings, get_settings
from app.db import get_db
from app.deps import CurrentUser, csrf_protect, require_permission
from app.models_catalogue import ApplicationCategory, ConfigurationGap, RuleSet, RuleSource, RuleVersion, RuleVersionSource
from app.rbac import Permission
from app.schemas_catalogue import (
    CategoryIn,
    CategoryOut,
    GapOut,
    GapResolveIn,
    PublishIn,
    RequestChangesIn,
    RetireIn,
    RuleSetCreateIn,
    RuleSetOut,
    SourceIn,
    SourceOut,
    SubmitReviewIn,
    VersionContentIn,
    VersionDetailOut,
)
from app.models import utcnow
from app.services import rules as svc
from app.services.errors import conflict, not_found

router = APIRouter(prefix="/api/rules", tags=["rules"], dependencies=[Depends(csrf_protect)])

can_read = require_permission(Permission.RULES_READ)
can_edit = require_permission(Permission.RULES_EDIT)
can_approve = require_permission(Permission.RULES_APPROVE)


def _detail(db: Session, version_id: uuid.UUID, settings: Settings) -> VersionDetailOut:
    return presenters.version_detail(db, svc.load_version(db, version_id), settings)


# ---- categories --------------------------------------------------------------------------------


@router.get("/categories", response_model=list[CategoryOut])
def list_categories(_: CurrentUser = Depends(can_read), db: Session = Depends(get_db)) -> list[ApplicationCategory]:
    return list(db.scalars(select(ApplicationCategory).order_by(ApplicationCategory.name)))


@router.post("/categories", response_model=CategoryOut, status_code=status.HTTP_201_CREATED)
def create_category(body: CategoryIn, request: Request, current: CurrentUser = Depends(can_edit), db: Session = Depends(get_db)) -> ApplicationCategory:
    if db.scalar(select(ApplicationCategory.id).where(ApplicationCategory.key == body.key)):
        raise conflict("duplicate_key", f"A category with key '{body.key}' already exists.")
    cat = ApplicationCategory(**body.model_dump())
    db.add(cat)
    db.flush()
    audit.record(db, "rule.category_created", request=request, actor=current.user, target_type="category", target_id=cat.id, detail={"key": cat.key})
    db.commit()
    return cat


@router.put("/categories/{category_id}", response_model=CategoryOut)
def update_category(category_id: uuid.UUID, body: CategoryIn, request: Request, current: CurrentUser = Depends(can_edit), db: Session = Depends(get_db)) -> ApplicationCategory:
    cat = db.get(ApplicationCategory, category_id)
    if cat is None:
        raise not_found("Category")
    if body.key != cat.key:
        raise conflict("key_immutable", "A category's stable key cannot be changed.")
    for k, v in body.model_dump().items():
        setattr(cat, k, v)
    audit.record(db, "rule.category_updated", request=request, actor=current.user, target_type="category", target_id=cat.id, detail={"is_active": cat.is_active})
    db.commit()
    return cat


# ---- sources -----------------------------------------------------------------------------------


@router.get("/sources", response_model=list[SourceOut])
def list_sources(_: CurrentUser = Depends(can_read), db: Session = Depends(get_db)) -> list[SourceOut]:
    sources = list(db.scalars(select(RuleSource).order_by(RuleSource.title)))
    locked = presenters.locked_source_ids(db, [s.id for s in sources])
    return [presenters.source_out(s, s.id in locked) for s in sources]


@router.post("/sources", response_model=SourceOut, status_code=status.HTTP_201_CREATED)
def create_source(body: SourceIn, request: Request, current: CurrentUser = Depends(can_edit), db: Session = Depends(get_db)) -> SourceOut:
    src = RuleSource(**body.model_dump(mode="python"), created_by_id=current.user.id)
    db.add(src)
    db.flush()
    audit.record(db, "rule.source_created", request=request, actor=current.user, target_type="rule_source", target_id=src.id, detail={"verification_status": src.verification_status})
    db.commit()
    return presenters.source_out(src, False)


@router.put("/sources/{source_id}", response_model=SourceOut)
def update_source(source_id: uuid.UUID, body: SourceIn, request: Request, current: CurrentUser = Depends(can_edit), db: Session = Depends(get_db)) -> SourceOut:
    src = db.get(RuleSource, source_id)
    if src is None:
        raise not_found("Source")
    before = src.verification_status
    for k, v in body.model_dump(mode="python").items():
        setattr(src, k, v)
    audit.record(db, "rule.source_updated", request=request, actor=current.user, target_type="rule_source", target_id=src.id, detail={"verification_before": before, "verification_after": src.verification_status})
    db.commit()  # the immutability guard rejects this (409) if a version in review/published cites the source
    return presenters.source_out(src, False)


# ---- rule sets and versions --------------------------------------------------------------------


@router.get("/sets", response_model=list[RuleSetOut])
def list_rule_sets(_: CurrentUser = Depends(can_read), db: Session = Depends(get_db)) -> list[RuleSetOut]:
    sets = db.scalars(
        select(RuleSet).options(selectinload(RuleSet.category), selectinload(RuleSet.versions).selectinload(RuleVersion.source_links).selectinload(RuleVersionSource.source))
        .order_by(RuleSet.title)
    ).unique().all()
    out = []
    for rs in sets:
        out.append(RuleSetOut(id=rs.id, key=rs.key, title=rs.title, classification=rs.classification, category=CategoryOut.model_validate(rs.category), versions=[presenters.version_summary(v) for v in rs.versions]))
    return out


@router.post("/sets", response_model=VersionDetailOut, status_code=status.HTTP_201_CREATED)
def create_rule_set(body: RuleSetCreateIn, request: Request, current: CurrentUser = Depends(can_edit), db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> VersionDetailOut:
    v = svc.create_rule_set(db, body, current.user, request)
    db.commit()
    return _detail(db, v.id, settings)


@router.post("/sets/{rule_set_id}/versions", response_model=VersionDetailOut, status_code=status.HTTP_201_CREATED)
def new_version(rule_set_id: uuid.UUID, request: Request, current: CurrentUser = Depends(can_edit), db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> VersionDetailOut:
    v = svc.new_draft_version(db, rule_set_id, current.user, request)
    db.commit()
    return _detail(db, v.id, settings)


@router.get("/versions/{version_id}", response_model=VersionDetailOut)
def get_version(version_id: uuid.UUID, _: CurrentUser = Depends(can_read), db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> VersionDetailOut:
    return _detail(db, version_id, settings)


@router.put("/versions/{version_id}", response_model=VersionDetailOut)
def replace_content(version_id: uuid.UUID, body: VersionContentIn, request: Request, current: CurrentUser = Depends(can_edit), db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> VersionDetailOut:
    v = svc.replace_draft_content(db, version_id, body, current.user, request)
    db.commit()
    return _detail(db, v.id, settings)


@router.post("/versions/{version_id}/submit-review", response_model=VersionDetailOut)
def submit_review(version_id: uuid.UUID, body: SubmitReviewIn, request: Request, current: CurrentUser = Depends(can_edit), db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> VersionDetailOut:
    v = svc.submit_for_review(db, version_id, body.row_version, body.note, current.user, request, settings)
    db.commit()
    return _detail(db, v.id, settings)


@router.post("/versions/{version_id}/request-changes", response_model=VersionDetailOut)
def request_changes(version_id: uuid.UUID, body: RequestChangesIn, request: Request, current: CurrentUser = Depends(can_approve), db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> VersionDetailOut:
    v = svc.request_changes(db, version_id, body.note, current.user, request)
    db.commit()
    return _detail(db, v.id, settings)


@router.post("/versions/{version_id}/publish", response_model=VersionDetailOut)
def publish(version_id: uuid.UUID, body: PublishIn, request: Request, current: CurrentUser = Depends(can_approve), db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> VersionDetailOut:
    v = svc.publish(db, version_id, body.supersede, body.note, current.user, request, settings)
    db.commit()
    return _detail(db, v.id, settings)


@router.post("/versions/{version_id}/retire", response_model=VersionDetailOut)
def retire(version_id: uuid.UUID, body: RetireIn, request: Request, current: CurrentUser = Depends(can_approve), db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> VersionDetailOut:
    v = svc.retire(db, version_id, body.reason, current.user, request)
    db.commit()
    return _detail(db, v.id, settings)


# ---- configuration gaps ------------------------------------------------------------------------


@router.get("/gaps", response_model=list[GapOut])
def list_gaps(_: CurrentUser = Depends(can_read), db: Session = Depends(get_db)) -> list[ConfigurationGap]:
    return list(db.scalars(select(ConfigurationGap).options(selectinload(ConfigurationGap.category)).order_by(ConfigurationGap.status.desc(), ConfigurationGap.last_reported_at.desc())))


@router.post("/gaps/{gap_id}/resolve", response_model=GapOut)
def resolve_gap(gap_id: uuid.UUID, body: GapResolveIn, request: Request, current: CurrentUser = Depends(can_edit), db: Session = Depends(get_db)) -> ConfigurationGap:
    gap = db.get(ConfigurationGap, gap_id)
    if gap is None:
        raise not_found("Gap")
    gap.status, gap.resolved_at, gap.resolved_by_id, gap.resolution_note = "resolved", utcnow(), current.user.id, body.note
    audit.record(db, "rule.gap_resolved", request=request, actor=current.user, target_type="configuration_gap", target_id=gap.id)
    db.commit()
    return gap
