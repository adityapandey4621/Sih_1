"""Build rule configuration for tests through the real service layer."""

import uuid
from dataclasses import dataclass

from app.config import get_settings
from app.db import SessionLocal
from app.domain import Classification, SourceVerification
from app.models import User
from app.models_catalogue import ApplicationCategory, RuleSource
from app.rbac import Role
from app.schemas_catalogue import ChecklistItemIn, DeadlinePolicyIn, RouteStageIn, RuleSetCreateIn, VersionContentIn
from app.services import rules as svc
from tests.conftest import make_user

PDF = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF\n"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 32


@dataclass
class Cat:
    category_id: uuid.UUID
    rule_set_id: uuid.UUID
    version_id: uuid.UUID
    author: str
    approver: str


# What the processing layer should look for. Data, exactly as a rule version would carry it.
MAIN_FIELDS = [
    {"field_key": "institution_name", "label": "Institution name", "kind": "text", "anchors": ["Name of the Institution"], "compare_group": "inst", "compare_to_application": "institution_name"},
    {"field_key": "document_date", "label": "Document date", "kind": "date", "anchors": ["Date"]},
    {"field_key": "reference_number", "label": "Reference number", "kind": "identifier", "anchors": ["Reference No"], "required": False},
]
EXTRA_FIELDS = [
    {"field_key": "institution_name", "label": "Institution name", "kind": "text", "anchors": ["Institute Name", "Name of the Institution"], "compare_group": "inst", "required": False},
]
MAIN_TEXT = ["Name of the Institution: Test Institute", "Date: 12/03/2024", "Reference No: RF-2024-9"]


def checklist(source_id: uuid.UUID | None = None) -> list[ChecklistItemIn]:
    mk = lambda **kw: ChecklistItemIn(source_id=source_id, **kw)  # noqa: E731
    return [
        mk(item_key="contact", label="Contact person", explanation="Who we contact.", item_type="form_field", field_kind="text", validation={"max_length": 50}, display_order=0),
        mk(item_key="kind", label="Kind of change", explanation="Choose one.", item_type="form_field", field_kind="select", options=["A", "B"], display_order=1),
        mk(item_key="b-detail", label="Detail for B", explanation="Only for B.", item_type="form_field", field_kind="text", applies_when={"item_key": "kind", "equals": "B"}, display_order=2),
        mk(item_key="doc-main", label="Main document", explanation="Required file.", item_type="document_upload", display_order=3, clause_ref="cl. 9", extraction=MAIN_FIELDS),
        mk(item_key="doc-extra", label="Extra document", explanation="Optional file.", required=False, item_type="document_upload", display_order=4, extraction=EXTRA_FIELDS),
        mk(item_key="decl", label="Declaration", explanation="Confirm.", item_type="declaration", display_order=5),
    ]


def route(source_id: uuid.UUID | None = None) -> list[RouteStageIn]:
    return [
        RouteStageIn(stage_key="scrutiny", label="Scrutiny", display_order=0, next_stage_keys=["evc"], source_id=source_id),
        RouteStageIn(stage_key="evc", label="EVC", display_order=1, is_conditional=True, condition_description="Where the route requires it.", next_stage_keys=[], source_id=source_id),
    ]


def make_category(key: str = "cat-a", name: str = "Category A", active: bool = True) -> uuid.UUID:
    with SessionLocal() as db:
        c = ApplicationCategory(key=key, name=name, description="", is_active=active)
        db.add(c)
        db.commit()
        return c.id


def make_source(author_id: uuid.UUID, status: SourceVerification, title: str = "A source") -> uuid.UUID:
    with SessionLocal() as db:
        s = RuleSource(title=title, issuing_authority="Test authority", verification_status=status.value, created_by_id=author_id)
        db.add(s)
        db.commit()
        return s.id


def users() -> tuple[User, User]:
    author = make_user(Role.RULES_ADMIN, "author@test.example.org")
    approver = make_user(Role.RULES_APPROVER, "approver@test.example.org")
    return author, approver


def build_published(
    *,
    category_key: str = "cat-a",
    years: tuple[str, ...] = ("2026-27",),
    classification: Classification = Classification.DEMO,
    set_key: str | None = None,
    with_checklist: bool = True,
    publish: bool = True,
    route_stages: "list[RouteStageIn] | None" = None,
    deadlines: "list[DeadlinePolicyIn] | None" = None,
    response_days: int | None = None,
) -> Cat:
    """Create (or reuse) author/approver, a category and a rule set, optionally taken all the way to published.

    `route_stages` / `deadlines` replace the default route and the (duration-less) response policy. `response_days` adds a
    CALENDAR response-period policy for deficiency notices backed by the version's own source (DEMO_ONLY for DEMO, verified for OFFICIAL)."""
    settings = get_settings()
    with SessionLocal() as db:
        author = db.query(User).filter_by(email="author@test.example.org").one_or_none()
        approver = db.query(User).filter_by(email="approver@test.example.org").one_or_none()
    if author is None:
        author, approver = users()
    cat = None
    with SessionLocal() as db:
        cat = db.query(ApplicationCategory).filter_by(key=category_key).one_or_none()
        cat_id = cat.id if cat else None
    if cat_id is None:
        cat_id = make_category(category_key, f"Category {category_key}")
    verified = classification == Classification.OFFICIAL
    src_id = make_source(author.id, SourceVerification.VERIFIED_OFFICIAL if verified else SourceVerification.DEMO_ONLY, title=f"Source for {set_key or category_key}")
    with SessionLocal() as db:
        a = db.get(User, author.id)
        v = svc.create_rule_set(db, RuleSetCreateIn(key=set_key or f"set-{category_key}", title="Test set", category_id=cat_id, classification=classification), a, None)
        v = svc.replace_draft_content(
            db, v.id,
            VersionContentIn(
                academic_years=list(years), change_summary="Initial test version", source_ids=[src_id],
                checklist=checklist(src_id) if with_checklist else [], route=route_stages if route_stages is not None else route(src_id),
                deadlines=deadlines if deadlines is not None else [
                    DeadlinePolicyIn(
                        policy_key="resp", label="Response", deadline_type="INTERNAL_SLA", trigger_event="deficiency_notice_issued", source_id=src_id,
                        **({"duration_value": response_days, "duration_unit": "days", "calendar_convention": "CALENDAR"} if response_days else {}),
                    )
                ],
                row_version=v.row_version,
            ),
            a, None,
        )
        rs_id, v_id = v.rule_set_id, v.id
        db.commit()
        if publish:
            v = svc.submit_for_review(db, v_id, v.row_version, None, a, None, settings)
            db.commit()
            svc.publish(db, v_id, False, None, db.get(User, approver.id), None, settings)
            db.commit()
    return Cat(cat_id, rs_id, v_id, "author@test.example.org", "approver@test.example.org")
