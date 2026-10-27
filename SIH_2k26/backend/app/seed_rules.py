"""Seed the rule catalogue for development.

    python -m app.seed        # users first (needs SEED_PASSWORD)
    python -m app.seed_rules

What this creates, and its standing:

* Sources, categories and OFFICIAL-classified *draft* rule sets built from the project's curated APH
  reference pack (docs/sources/). Every source is SECONDARY_UNVERIFIED because the official AICTE
  PDF could not be retrieved and the pack itself says it is not authoritative. These drafts therefore
  CANNOT be published as official rules (the backend refuses). They carry the Table 2.3 route shapes
  only; checklists are an explicit GAP because the sources contain no verified document lists.
* DEMO rule sets, one per category: invented, illustrative checklists labelled
  "DEMO ONLY — NOT VERIFIED AS CURRENT OFFICIAL RULES". They go through the real
  author -> review -> publish lifecycle, and only exist where demo rules are enabled
  (never in production).
"""

import sys
from datetime import date

from sqlalchemy import select

from app.config import get_settings
from app.db import SessionLocal
from app.domain import DEMO_BANNER, Classification, SourceVerification
from app.models import User
from app.models_catalogue import ApplicationCategory, RuleSet, RuleSource
from app.schemas_catalogue import (
    ChecklistItemIn,
    DeadlinePolicyIn,
    RouteStageIn,
    RuleSetCreateIn,
    SubmitReviewIn,
    VersionContentIn,
)
from app.services import rules as svc

OFFICIAL_URL = "https://www.aicte.gov.in/sites/default/files/APH%20Final.pdf"
MIRROR_URL = "https://www.iiqaccreditation.com/public/assets/aicte-process-handbook-2025-26.pdf"
CORRIGENDUM_URL = "https://teamleaseregtech.com/updates/article/52940/aicte-issued-the-corrigendum-regarding-the-approval-process-2026-27/"
APH_TITLE = "AICTE Approval Process Handbook 2024-25 to 2026-27"
AICTE = "All India Council for Technical Education (AICTE)"
APH_PDF_REF = "docs/sources/2025031399.pdf"
APH_PDF_SHA256 = "153d54816f2cf11489218e239113544e8d49b73680009688baa7dc4880636051"
# The official host was unreachable during development (HTTP 404/502/connection reset); the team then supplied the handbook PDF itself.
VERIFIED_NOTE = (
    f"Clause text read on 2026-10-27 in the official handbook PDF supplied by the team ({APH_PDF_REF}, SHA-256 {APH_PDF_SHA256[:16]}…, 231 pages, "
    "'Approval Process Handbook 2024-25 to 2026-27'). It matched the project's curated summary. Verified for THIS edition only: "
    "corrigenda issued after it (a December 2025 one is reported) have not been checked against a primary source."
)

# (key, name as listed in Table 2.3 per the reference pack, route stages)
CATEGORIES = [
    ("extension-self-disclosure", "Extension of Approval based on self-disclosure", ["self_disclosure"]),
    ("name-or-affiliation-change", "Change in institution name / affiliating University or Board / relevant institution type change", ["scrutiny"]),
    ("intake-increase-or-additional-courses", "Increase in intake / additional courses", ["scrutiny", "evc", "sac_shc_aa"]),
    ("new-programme-existing-institution", "Start new programme/level in an existing institution", ["scrutiny", "evc", "sac_shc_aa"]),
    ("course-name-merger-or-intake-reduction", "Change in course name / merger of courses / reduction in intake", ["scrutiny"]),
]

STAGES = {
    "self_disclosure": ("Self-disclosure", "Self-disclosure step listed for this application type in Table 2.3.", False, None),
    "scrutiny": ("Scrutiny / re-scrutiny", "Scrutiny / re-scrutiny step listed for this application type in Table 2.3 (see also Ch. I cl. 1.8).", False, None),
    "evc": ("Expert Visit Committee (EVC)", "EVC step listed for this application type in Table 2.3 (see also Ch. I cl. 1.9). EVC findings are a report, not a final approval.", False, None),
    "sac_shc_aa": (
        "SAC / SHC (as applicable)",
        "Table 2.3's SAC/SHC column is marked AA for this application type. SHC is a compliance/hearing process and SAC is an appeal process; they are distinct.",
        True,
        "AA — as applicable depending on the outcome of the previous processing steps (APH definition, as summarised in the reference pack).",
    ),
}


def _src(db, author: User, **kw) -> RuleSource:
    existing = db.scalar(select(RuleSource).where(RuleSource.title == kw["title"]))
    if existing:
        for k, v in kw.items():  # re-running the seed brings an existing development source up to date
            if getattr(existing, k) != v:
                setattr(existing, k, v)
        return existing
    s = RuleSource(created_by_id=author.id, **kw)
    db.add(s)
    db.flush()
    return s


def _stage_models(keys: list[str], source_id, clause: str) -> list[RouteStageIn]:
    out = []
    for n, k in enumerate(keys):
        label, desc, cond, cond_desc = STAGES[k]
        out.append(RouteStageIn(
            stage_key=k.replace("_", "-"), label=label, description=desc, display_order=n, is_conditional=cond,
            condition_description=cond_desc, next_stage_keys=[keys[n + 1].replace("_", "-")] if n + 1 < len(keys) else [],
            source_id=source_id, clause_ref=clause,
        ))
    return out


DEMO_FIELDS_A = [
    {"field_key": "institution_name", "label": "Institution name", "kind": "text", "anchors": ["Name of the Institution", "Institution Name"], "compare_group": "institution_name", "compare_to_application": "institution_name"},
    {"field_key": "document_date", "label": "Document date", "kind": "date", "anchors": ["Date of Issue", "Date"], "required": False},
    {"field_key": "reference_number", "label": "Reference number", "kind": "identifier", "anchors": ["Reference No", "Ref No"], "required": False},
]
DEMO_FIELDS_B = [
    {"field_key": "institution_name", "label": "Institution name", "kind": "text", "anchors": ["Name of the Institution", "Institution Name"], "compare_group": "institution_name", "required": False},
]


def _demo_checklist(demo_src) -> list[ChecklistItemIn]:
    note = " Illustrative item for demonstrating the interface; not a regulatory requirement."
    mk = lambda **kw: ChecklistItemIn(source_id=demo_src, clause_ref="DEMO ONLY", **kw)  # noqa: E731
    return [
        mk(item_key="demo-contact-person", label="[DEMO] Contact person name", explanation="A free-text form field." + note, required=True, item_type="form_field", field_kind="text", validation={"max_length": 120}, display_order=0),
        mk(item_key="demo-change-summary", label="[DEMO] Summary of the proposed change", explanation="A long-text form field." + note, required=True, item_type="form_field", field_kind="long_text", validation={"min_length": 10}, display_order=1),
        mk(item_key="demo-change-type", label="[DEMO] Type of change", explanation="A select field whose answer reveals a follow-up item." + note, required=True, item_type="form_field", field_kind="select", options=["Option A", "Option B"], display_order=2),
        mk(item_key="demo-option-b-detail", label="[DEMO] Details for Option B", explanation="Appears only when 'Option B' is chosen (conditional item)." + note, required=True, item_type="form_field", field_kind="text", applies_when={"item_key": "demo-change-type", "equals": "Option B"}, display_order=3),
        mk(item_key="demo-document-a", label="[DEMO] Supporting document A", explanation="A required file upload slot (PDF, PNG or JPEG)." + note + " The document is read for an institution name, a date and a reference number (all invented for the demo).", required=True, item_type="document_upload", display_order=4, extraction=DEMO_FIELDS_A),
        mk(item_key="demo-document-b", label="[DEMO] Supporting document B", explanation="An optional file upload slot." + note, required=False, item_type="document_upload", display_order=5, extraction=DEMO_FIELDS_B),
        mk(item_key="demo-declaration", label="[DEMO] Declaration that the information provided is accurate", explanation="A declaration the applicant confirms." + note, required=True, item_type="declaration", display_order=6),
    ]


def main() -> int:
    settings = get_settings()
    if settings.is_production:
        print("Refusing to seed demo configuration in production.", file=sys.stderr)
        return 1
    with SessionLocal() as db:
        author = db.scalar(select(User).where(User.email == "rules.admin@demo.example.org"))
        approver = db.scalar(select(User).where(User.email == "rules.approver@demo.example.org"))
        if not author or not approver:
            print("Seed users first: python -m app.seed (needs SEED_PASSWORD).", file=sys.stderr)
            return 1

        # ---- sources ---------------------------------------------------------------------------
        aph2 = _src(db, author, title=f"{APH_TITLE} — Chapter II, clauses 2.23–2.24 and Table 2.3", issuing_authority=AICTE, url=OFFICIAL_URL,
                    clause_ref="Ch. II cl. 2.23–2.24; Table 2.3", page_ref="printed pp. 29–30", verification_status=SourceVerification.VERIFIED_OFFICIAL.value, document_ref=APH_PDF_REF, retrieved_date=date(2026, 10, 27),
                    applicability_notes=VERIFIED_NOTE + " Table 2.3 is the table for Chapter II application types; AA = as applicable depending on the outcome of previous steps.")
        aph1 = _src(db, author, title=f"{APH_TITLE} — Chapter I, clauses 1.8–1.11", issuing_authority=AICTE, url=OFFICIAL_URL,
                    clause_ref="Ch. I cl. 1.8–1.11", page_ref="printed pp. 7–10", verification_status=SourceVerification.VERIFIED_OFFICIAL.value, document_ref=APH_PDF_REF, retrieved_date=date(2026, 10, 27),
                    applicability_notes=VERIFIED_NOTE + " Cl. 1.8(f): deficiencies are communicated through the AICTE web portal; cl. 1.8(g): re-scrutiny verifies ONLY those deficiencies; no response period for them is stated. "
                    "Clause 1.11 and Chapter II clause 2.24 word the appeal trigger differently; no single appeal clock is encoded.")
        pack = _src(db, author, title="Project reference pack: APH selected provisions (curated summary)", issuing_authority="SIH 26262 project team (not an AICTE publication)",
                    document_ref="docs/sources/AICTE_SIH26262_APH_Selected_Provisions_Reference.pdf", verification_status=SourceVerification.SECONDARY_UNVERIFIED.value,
                    applicability_notes="Curated summary prepared 2026-10-26. States that it is not the full APH and not an official publication; the official handbook controls in case of conflict.")
        _src(db, author, title="APH mirror copy (non-authoritative)", issuing_authority="iiqaccreditation.com (not the issuing authority)", url=MIRROR_URL,
             verification_status=SourceVerification.SECONDARY_UNVERIFIED.value, applicability_notes="Mirror inspected when the official host was unreachable. Not an authority.")
        _src(db, author, title="Reported corrigendum dated 2 December 2025 (EVC / SAC constitution) — secondary report", issuing_authority="TeamLease RegTech (secondary, non-official)",
             url=CORRIGENDUM_URL, publication_date=date(2025, 12, 2), verification_status=SourceVerification.SECONDARY_UNVERIFIED.value,
             applicability_notes="CRITICAL GAP: the primary official corrigendum has not been verified, so none of its reported changes are encoded.")
        demo = _src(db, author, title=DEMO_BANNER, issuing_authority="None — illustrative", verification_status=SourceVerification.DEMO_ONLY.value,
                    applicability_notes="Invented for demonstrating the interface. Not a statement of any AICTE requirement.")

        # ---- categories ------------------------------------------------------------------------
        cats: dict[str, ApplicationCategory] = {}
        for key, name, _ in CATEGORIES:
            c = db.scalar(select(ApplicationCategory).where(ApplicationCategory.key == key))
            if c is None:
                c = ApplicationCategory(
                    key=key, name=name, source_id=aph2.id,
                    description="Application type as listed in APH Table 2.3 (per the project reference pack).",
                    applicability_note="Table 2.3 is not an exhaustive catalogue of AICTE application types. Name and applicability not independently verified.",
                )
                db.add(c)
                db.flush()
            cats[key] = c
        db.commit()

        created = []
        for key, name, route in CATEGORIES:
            cat = cats[key]
            # ---- OFFICIAL-classified draft: route shape only; checklist is a declared gap ------
            off_key = f"aph-table-2-3-{key}"
            if not db.scalar(select(RuleSet.id).where(RuleSet.key == off_key)):
                v = svc.create_rule_set(db, RuleSetCreateIn(key=off_key, title=f"{name} — Table 2.3 route (UNVERIFIED)", category_id=cat.id, classification=Classification.OFFICIAL), author, None)
                svc.replace_draft_content(db, v.id, VersionContentIn(
                    academic_years=["2026-27"],
                    change_summary="Route shape from Table 2.3, checked against the official handbook PDF (2024-27 edition). CHECKLIST GAP: the document lists in Annexure-2 are not yet encoded, so none is invented. "
                                   "Cannot be published until every linked source is verified and the post-2024 corrigenda are checked.",
                    source_ids=[aph2.id, aph1.id, pack.id],
                    route=_stage_models(route, aph2.id, "Table 2.3"),
                    deadlines=[DeadlinePolicyIn(
                        policy_key="deficiency-response-no-period", label="Response to deficiencies — the handbook states NO fixed period (cl. 1.8(b), (f), (g))",
                        deadline_type="INTERNAL_SLA", trigger_event="deficiency_notice_issued", duration_value=None, duration_unit=None,
                        pause_resume_note="Cl. 1.8(f): deficiencies are communicated through the web portal and the institution is expected to fulfil them for re-scrutiny; cl. 1.8(b): the Regulation Bureau informs the date and time of the (re-)scrutiny. "
                                          "No number of days is stated, so none is set.", source_id=aph1.id, clause_ref="Ch. I cl. 1.8(b), (f), (g)"),
                      DeadlinePolicyIn(
                        policy_key="appeal-window-ch2-cl-2-24", label="Appeal window per Ch. II cl. 2.24 — applicability to this category NOT confirmed",
                        deadline_type="STATUTORY", trigger_event="executive_committee_decision_uploaded", duration_value=7, duration_unit="days", calendar_convention=None,
                        pause_resume_note="Calendar convention and pause/resume are not established by the available sources.", source_id=aph2.id, clause_ref="Ch. II cl. 2.24")],
                    row_version=v.row_version), author, None)
                db.commit()
                created.append(off_key)

            # ---- DEMO rule set through the real lifecycle --------------------------------------
            demo_key = f"demo-{key}"
            if settings.demo_rules_enabled and not db.scalar(select(RuleSet.id).where(RuleSet.key == demo_key)):
                v = svc.create_rule_set(db, RuleSetCreateIn(key=demo_key, title=f"DEMO — {name}", category_id=cat.id, classification=Classification.DEMO), author, None)
                v = svc.replace_draft_content(db, v.id, VersionContentIn(
                    academic_years=["2026-27"], change_summary=f"{DEMO_BANNER}. Invented checklist for demonstrating application intake.",
                    source_ids=[demo.id], checklist=_demo_checklist(demo.id),
                    route=_stage_models(route, demo.id, "DEMO ONLY"),
                    deadlines=[DeadlinePolicyIn(policy_key="demo-response-window", label="[DEMO] Response window — duration deliberately not set", deadline_type="INTERNAL_SLA",
                                                trigger_event="deficiency_notice_issued", source_id=demo.id, clause_ref="DEMO ONLY"),
                               DeadlinePolicyIn(policy_key="demo-appeal-window", label="[DEMO] Appeal window — 7 calendar days, invented for demonstration", deadline_type="INTERNAL_SLA",
                                                trigger_event="executive_committee_decision_uploaded", duration_value=7, duration_unit="days", calendar_convention="CALENDAR", source_id=demo.id, clause_ref="DEMO ONLY")],
                    row_version=v.row_version), author, None)
                v = svc.submit_for_review(db, v.id, v.row_version, "DEMO ONLY", author, None, settings)
                svc.publish(db, v.id, False, "DEMO ONLY — published for development use", approver, None, settings)
                db.commit()
                created.append(demo_key)

        if settings.demo_rules_enabled:
            from app.services import committee_policy

            for k in committee_policy.seed_demo_policies(db):
                created.append(f"committee policy {k} (DEMO ONLY)")
            db.commit()

        # One demo version left in review so the review screen has something real to show.
        ds = db.scalar(select(RuleSet).where(RuleSet.key == "demo-intake-increase-or-additional-courses"))
        if ds is not None and settings.demo_rules_enabled and len(ds.versions) == 1:
            v2 = svc.new_draft_version(db, ds.id, author, None)  # clones v1's content as a starting point
            v2.change_summary = "DEMO: wording update awaiting review. Publishing it requires an explicit supersede of v1."
            db.flush()
            svc.submit_for_review(db, v2.id, v2.row_version, "DEMO", author, None, settings)
            db.commit()
            created.append("demo v2 (left in review)")
    print(f"Seeded {len(created)} item(s)." if created else "Catalogue already seeded; nothing changed.")
    for c in created:
        print(f"  {c}")
    print("OFFICIAL-classified drafts remain unpublished by design (no checklist encoded; the project summary source is unverified; corrigenda unchecked).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
