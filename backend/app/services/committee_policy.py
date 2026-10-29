"""Committee configuration: what a visit may look like, what a checklist asks, how a report is structured, whether and how an SHC
may take compliance and hold hearings.

None of this is hard-coded in a service or a component. It lives in `committee_policies` (versioned, with sources and a
verification status), and a case freezes a copy when its stage is activated, so a later policy never changes a running case.

There is **no OFFICIAL policy**. The official handbook is only partly specific about these matters (see docs/COMMITTEE.md) and a
reported corrigendum that changes the EVC constitution and allows hybrid visits has not been verified from a primary source. So:

  * a DEMO policy exists for DEMO-classified rule sets (fictional checklist, fictional composition, clearly labelled);
  * for any other case the stage still activates, but the missing configuration is shown as a blocker and recorded as a
    configuration gap; nothing is guessed to keep the case moving.
"""

import hashlib
import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.domain import APPEAL_DEMO_BANNER, COMMITTEE_DEMO_BANNER, DECISION_POLICY_STAGE, CommitteeStage
from app.models_committee import CommitteePolicy

APH = "AICTE Approval Process Handbook 2024-27 (official PDF supplied by the team)"
HYBRID_REASON = (
    "A hybrid mode is reported in a December 2025 corrigendum. That corrigendum could not be retrieved from the official source, so hybrid visits are "
    "not permitted until it is verified and configured."
)

# ---- the demonstration policies (fictional; used only for DEMO-classified rule sets) -------------------

DEMO_EVC: dict = {
    "banner": COMMITTEE_DEMO_BANNER,
    "visit": {
        "modes": [
            {
                "key": "ONLINE", "label": "Online", "verification": "VERIFIED_OFFICIAL", "requires": ["access_details"], "requires_reason": False,
                "clause_ref": "APH 2024-27 Ch. I cl. 1.8 NOTE and cl. 1.9(f): scrutiny and EVC are conducted in online mode",
            },
            {
                "key": "PHYSICAL", "label": "Physical (in person)", "verification": "VERIFIED_OFFICIAL", "requires": ["location"], "requires_reason": True,
                "clause_ref": "APH 2024-27 Ch. I cl. 1.8 NOTE: offline mode only under extraordinary circumstances (including Court directions); cl. 1.9(g)",
            },
        ],
        "unverified_modes": [{"key": "HYBRID", "label": "Hybrid", "reason": HYBRID_REASON}],
    },
    "composition": {"min_members": 1, "note": "DEMO ONLY: a minimum invented for the demonstration. The real constitution and selection authority are not configured."},
    "checklist": [
        {"item_key": "demo-premises", "label": "DEMO: premises match the application", "description": "Fictional item. Record what you observe about the premises described in the application.",
         "required": True, "evidence_required": False, "clause_ref": "DEMO ONLY", "demo": True},
        {"item_key": "demo-equipment", "label": "DEMO: equipment listed in the application is present", "description": "Fictional item. Note which listed equipment you could and could not see.",
         "required": True, "evidence_required": True, "clause_ref": "DEMO ONLY", "demo": True},
        {"item_key": "demo-staff", "label": "DEMO: staff records are available", "description": "Fictional item. Record whether the staff records were available to you.",
         "required": False, "evidence_required": False, "clause_ref": "DEMO ONLY", "demo": True},
    ],
    "report": {
        "sections": [
            {"key": "summary", "label": "Summary of the visit", "required": True},
            {"key": "limitations", "label": "Limitations and evidence that was not available", "required": False},
        ],
        "visible_to_institution_on": "COMPLETED",
    },
}

DEMO_SHC: dict = {
    "banner": COMMITTEE_DEMO_BANNER,
    "composition": {"min_members": 1, "note": "DEMO ONLY: a minimum invented for the demonstration. The real constitution and selection authority are not configured."},
    "shc": {
        "compliance_submission": "PERMITTED",
        "compliance_clause_ref": "APH 2024-27 Ch. II cl. 2.23.3: institutions have a chance to submit compliance before the Standing Hearing Committee",
        "response_period": None,  # the handbook states none; nothing is invented
        "hearing": "OPTIONAL",
        "hearing_modes": [
            {"key": "ONLINE", "label": "Online", "verification": "DEMO_ONLY", "requires": ["access_details"], "requires_reason": False, "clause_ref": "DEMO ONLY"},
            {"key": "PHYSICAL", "label": "Physical (in person)", "verification": "DEMO_ONLY", "requires": ["location"], "requires_reason": True, "clause_ref": "DEMO ONLY"},
        ],
        "recommendation_options": [
            {"key": "RECOMMEND_NEXT_STAGE", "label": "Recommend that the case proceeds to the next stage"},
            {"key": "DO_NOT_RECOMMEND", "label": "Do not recommend that the case proceeds"},
            {"key": "FURTHER_COMPLIANCE", "label": "Further compliance is required"},
        ],
        "recommendation_sections": [
            {"key": "summary", "label": "Summary of the SHC review", "required": True},
            {"key": "rationale", "label": "Reasons for the recommendation", "required": True},
        ],
    },
}

# Phase 6. The clause references are real (read in the supplied official handbook); the CONFIGURATION built on them is a demonstration, because
# which outcomes are appealable, the ratification vocabulary and the Council's appeal outcomes are not fully stated by the handbook.
DEMO_SAC: dict = {
    "banner": APPEAL_DEMO_BANNER,
    "composition": {"min_members": 1, "note": "DEMO ONLY: a minimum invented for the demonstration. The real constitution and selection authority are not configured."},
    "sac": {
        "hearing": "OPTIONAL",
        "hearing_modes": [
            {"key": "ONLINE", "label": "Online", "verification": "DEMO_ONLY", "requires": ["access_details"], "requires_reason": False, "clause_ref": "DEMO ONLY"},
            {"key": "PHYSICAL", "label": "Physical (in person)", "verification": "DEMO_ONLY", "requires": ["location"], "requires_reason": True, "clause_ref": "DEMO ONLY"},
        ],
        # Ch. I cl. 1.11(d): the SAC either recommends / does not recommend the case to the Council, or recommends scrutiny / EVC.
        "recommendation_options": [
            {"key": "RECOMMEND", "label": "Recommend the case to the Council"},
            {"key": "NOT_RECOMMEND", "label": "Do not recommend the case to the Council"},
            {"key": "FURTHER_SCRUTINY_OR_EVC", "label": "Recommend further scrutiny or an expert visit"},
        ],
        "recommendation_clause_ref": "APH 2024-27 Ch. I cl. 1.11(d); Ch. II cl. 2.24(f)",
        "recommendation_sections": [
            {"key": "summary", "label": "Summary of the SAC review", "required": True},
            {"key": "rationale", "label": "Reasons for the recommendation", "required": True},
        ],
    },
}

DEMO_DEC: dict = {
    "banner": APPEAL_DEMO_BANNER,
    "decision": {
        "outcomes": [
            {"key": "LOA", "label": "Letter of Approval (LoA)", "requires_speaking_order": False, "clause_ref": "APH 2024-27 Ch. I cl. 1.10(b)"},
            {"key": "LOD", "label": "Letter of Deficiency (LoD)", "requires_speaking_order": False, "clause_ref": "APH 2024-27 Ch. I cl. 1.10(b)"},
            {"key": "LOR", "label": "Letter of Rejection (LoR)", "requires_speaking_order": True, "clause_ref": "APH 2024-27 Ch. I cl. 1.10(b): a Speaking Order states the reasons for a rejection"},
        ],
        "bureau_observation_required": True,
        "bureau_clause_ref": "APH 2024-27 Ch. I cl. 1.10(a): the Executive Committee decides after considering the committee's recommendations and the views of the Regulation Bureau",
    },
    "ratification": {
        "required": True,
        "clause_ref": "APH 2024-27 Ch. I cl. 1.10(a); Ch. II cl. 2.25(a): decisions of the Executive Committee are ratified by the Council",
        "outcomes": [{"key": "RATIFIED", "label": "Ratified"}, {"key": "NOT_RATIFIED", "label": "Not ratified"}],  # the vocabulary is a demonstration
        "issue_requires_ratification": False,  # the handbook does not state the order of issue and ratification
    },
    "appeal": {
        "permitted": True,
        "once_only": True,
        "appealable_outcomes": ["LOD", "LOR"],  # the handbook says "if aggrieved"; which outcomes qualify is a demonstration setting
        "clause_ref": "APH 2024-27 Ch. I cl. 1.11(a); Ch. II cl. 2.24(a): an institution aggrieved by the Executive Committee's decision may appeal once to the Council",
        "council_outcomes": [
            {"key": "LOA", "label": "Letter of Approval (LoA)", "requires_speaking_order": False},
            {"key": "LOR", "label": "Letter of Rejection (LoR)", "requires_speaking_order": True},
        ],
        "council_clause_ref": "APH 2024-27 Ch. I cl. 1.11(e), (g); Ch. II cl. 2.24(f): the Council's decision is final",
    },
}

DEMO_SOURCES = [{"title": COMMITTEE_DEMO_BANNER, "clause_ref": "DEMO ONLY", "verification_status": "DEMO_ONLY"}]
DEMO_SPECS = {
    CommitteeStage.EVC: ("demo-evc-policy", "DEMO — EVC visit, checklist and report structure", DEMO_EVC),
    CommitteeStage.SHC: ("demo-shc-policy", "DEMO — SHC compliance, hearing and recommendation", DEMO_SHC),
    CommitteeStage.SAC: ("demo-sac-policy", "DEMO — SAC review, hearing and recommendation", DEMO_SAC),
}
DEMO_DEC_SPEC = ("demo-decision-policy", "DEMO — Executive Committee decision, Council ratification and appeal", DEMO_DEC)


def content_hash(content: dict) -> str:
    return hashlib.sha256(json.dumps(content, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def seed_demo_policies(db: Session) -> list[str]:
    """Idempotent: publish the DEMO policies if they are absent. Used by the seed command and by tests."""
    made = []
    for stage, (key, title, content) in [(s.value, spec) for s, spec in DEMO_SPECS.items()] + [(DECISION_POLICY_STAGE, DEMO_DEC_SPEC)]:
        if db.scalar(select(CommitteePolicy.id).where(CommitteePolicy.stage == stage, CommitteePolicy.classification == "DEMO", CommitteePolicy.status == "published")):
            continue
        db.add(CommitteePolicy(stage=stage, key=key, version_number=1, classification="DEMO", status="published", title=title, content=content, sources=DEMO_SOURCES, content_hash=content_hash(content)))
        made.append(key)
    db.flush()
    return made


# ---- applying a policy to a case ---------------------------------------------------------------------


def applicable(db: Session, stage: "CommitteeStage | str", config: dict, settings: Settings) -> CommitteePolicy | None:
    """The published policy for this stage that suits the case's rule-set classification. DEMO policies serve only DEMO cases, and only where demo rules are on."""
    classification = config.get("rule_set", {}).get("classification")
    if classification == "DEMO" and not settings.demo_rules_enabled:
        return None
    if classification not in ("DEMO", "OFFICIAL"):
        return None
    return db.scalar(select(CommitteePolicy).where(CommitteePolicy.stage == getattr(stage, "value", stage), CommitteePolicy.classification == classification, CommitteePolicy.status == "published"))


def freeze(policy: CommitteePolicy) -> dict:
    return {
        "id": str(policy.id), "key": policy.key, "version": policy.version_number, "classification": policy.classification, "title": policy.title,
        "content": policy.content, "sources": policy.sources, "content_hash": policy.content_hash,
    }


def ref(snapshot: dict | None) -> dict | None:
    return {k: snapshot[k] for k in ("id", "key", "version", "classification", "content_hash")} if snapshot else None


def blockers_for(stage: CommitteeStage, snapshot: dict | None) -> list[dict]:
    """Configuration that is missing. Each blocker names the thing that cannot proceed; none is worked around."""
    if snapshot is None:
        return [{"code": "no_committee_policy", "message": f"No verified {stage.value} configuration (visit modes, checklist, report structure) applies to this application's rule set. Nothing is assumed in its place; it is recorded for the rule configuration reviewers."}]
    c = snapshot["content"]
    out: list[dict] = []
    if stage == CommitteeStage.EVC:
        if not c.get("visit", {}).get("modes"):
            out.append({"code": "no_visit_modes", "message": "The configuration permits no visit mode, so a visit cannot be scheduled."})
        if not c.get("checklist"):
            out.append({"code": "no_checklist", "message": "No visit checklist is configured, so observations cannot be recorded against one."})
        if not c.get("report", {}).get("sections"):
            out.append({"code": "no_report_structure", "message": "No report structure is configured, so a report cannot be validated or submitted."})
    elif stage == CommitteeStage.SAC:
        s = c.get("sac", {})
        if not s.get("recommendation_sections") or not s.get("recommendation_options"):
            out.append({"code": "no_recommendation_structure", "message": "No SAC recommendation structure is configured, so a recommendation cannot be validated or submitted."})
    else:
        s = c.get("shc", {})
        if s.get("compliance_submission") not in ("PERMITTED", "NOT_PERMITTED"):
            out.append({"code": "compliance_rule_unverified", "message": "Whether the institution may submit compliance is not established by the configuration."})
        if not s.get("recommendation_sections"):
            out.append({"code": "no_recommendation_structure", "message": "No recommendation structure is configured, so a recommendation cannot be validated or submitted."})
    return out


def verification_note(snapshot: dict | None) -> str | None:
    if snapshot is None:
        return None
    return COMMITTEE_DEMO_BANNER if snapshot["classification"] == "DEMO" else None


def response_period(snapshot: dict | None, config: dict) -> dict:
    """The compliance response period. The handbook states none; a period exists only if the (verified) policy says so, and none is invented."""
    s = (snapshot or {}).get("content", {}).get("shc", {})
    value = s.get("response_period")
    if not value:
        return {"state": "NO_DURATION", "message": "No response period is established by the configuration. No due date has been set."}
    return {"state": "UNSUPPORTED", "message": "A configured response period is not applied in this phase, so no due date has been set."}


def mode_options(snapshot: dict | None, stage: CommitteeStage) -> list[dict]:
    c = (snapshot or {}).get("content", {})
    if stage == CommitteeStage.EVC:
        return list(c.get("visit", {}).get("modes", []))
    if stage == CommitteeStage.SAC:
        return list(c.get("sac", {}).get("hearing_modes", []))
    return list(c.get("shc", {}).get("hearing_modes", []))


def decision_blockers(snapshot: dict | None) -> list[dict]:
    """What is missing for the Executive Committee's decision. The appeal rules are checked separately, when an appeal is attempted."""
    if snapshot is None:
        return [{"code": "no_decision_policy", "message": "No verified decision configuration (the outcomes the Executive Committee may record, whether Bureau observations and Council ratification are required) applies to this application's rule set. Nothing is assumed in its place; it is recorded for the rule configuration reviewers."}]
    d = snapshot["content"].get("decision", {})
    out: list[dict] = []
    if not d.get("outcomes"):
        out.append({"code": "no_outcomes", "message": "The configuration lists no outcome the Executive Committee may record."})
    return out
