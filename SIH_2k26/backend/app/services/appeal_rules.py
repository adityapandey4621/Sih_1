"""When may an institution appeal, and until when? Answered from the application's FROZEN rule configuration, never from a constant.

The handbook (APH 2024-27, read in the supplied official PDF) says an aggrieved institution may appeal once to the Council, within **7 days**:
  * Ch. I cl. 1.11(a):  from the date of issue of the Letter of Deficiency (LoD)
  * Ch. II cl. 2.24(a): from the date of uploading the Executive Committee's decision
It states neither a counting convention (calendar or working days, whether the first day counts) nor a cut-off time. So:

  * the period comes from a deadline policy on the rule version (`trigger_event`, duration, unit, source, clause), and is used only if exactly one
    policy applies, it states a duration, and its source is verified official (or DEMO_ONLY on a DEMO case with demo rules enabled);
  * WORKING days are refused (there is no verified calendar to count them with);
  * where the policy does not state a convention, elapsed 24-hour periods from the issue instant are used and the result says so;
  * the window is anchored at the FIRST issue of the official outcome and never moves, even if a later correction is issued.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from app.config import Settings
from app.domain import APPEAL_TRIGGER_DECISION_UPLOADED, APPEAL_TRIGGER_LOD_ISSUED, ResponsePolicyState, SourceVerification
from app.services import policy as response_policy

CONVENTION_NOTE = (
    "Counted as elapsed 24-hour periods from the instant the official outcome was first issued. The handbook states neither a counting convention "
    "(calendar or working days, whether the first day counts) nor a cut-off time, so this convention is NOT verified."
)


@dataclass
class AppealWindow:
    state: ResponsePolicyState
    message: str
    policy_key: str | None = None
    trigger_event: str | None = None
    duration_value: int | None = None
    duration_unit: str | None = None
    clause_ref: str | None = None
    source: dict | None = None
    demo: bool = False
    note: str | None = None
    extra: dict = field(default_factory=dict)

    @property
    def usable(self) -> bool:
        return self.state in (ResponsePolicyState.COMPUTED, ResponsePolicyState.DEMO_COMPUTED)

    def deadline(self, trigger_at: datetime) -> datetime | None:
        if not self.usable or self.duration_value is None:
            return None
        delta = timedelta(days=self.duration_value) if self.duration_unit == "days" else timedelta(hours=self.duration_value)
        return trigger_at + delta

    def stable(self) -> dict:
        return {
            "state": self.state.value, "message": self.message, "policy_key": self.policy_key, "trigger_event": self.trigger_event, "duration_value": self.duration_value,
            "duration_unit": self.duration_unit, "clause_ref": self.clause_ref, "source": self.source, "demo": self.demo, "convention_note": self.note,
        }


def resolve_window(config: dict, settings: Settings, outcome: str | None) -> AppealWindow:
    deadlines = config.get("deadlines", [])
    wanted = [APPEAL_TRIGGER_LOD_ISSUED] if outcome == "LOD" else []
    policies = [p for p in deadlines if p.get("trigger_event") in wanted] or [p for p in deadlines if p.get("trigger_event") == APPEAL_TRIGGER_DECISION_UPLOADED]
    if not policies:
        return AppealWindow(ResponsePolicyState.MISSING, "The approved configuration has no appeal-window policy, so no deadline can be established. Nothing is assumed.")
    if len(policies) > 1:
        return AppealWindow(ResponsePolicyState.AMBIGUOUS, "The approved configuration has more than one appeal-window policy for this outcome; none was chosen.")
    p = policies[0]
    src = next((s for s in config.get("sources", []) if s["id"] == p.get("source_id")), None)
    base = dict(
        policy_key=p["policy_key"], trigger_event=p.get("trigger_event"), duration_value=p.get("duration_value"), duration_unit=p.get("duration_unit"), clause_ref=p.get("clause_ref"),
        source={"title": src["title"], "verification_status": src["verification_status"], "clause_ref": src.get("clause_ref")} if src else None,
    )
    if p.get("duration_value") is None:
        return AppealWindow(ResponsePolicyState.NO_DURATION, "The policy exists but no appeal period is established by a verified source. No deadline has been set.", **base)
    if p.get("calendar_convention") == "WORKING":
        return AppealWindow(ResponsePolicyState.UNSUPPORTED, "The policy counts in working days, and there is no verified calendar to count them with.", **base)
    note = None if p.get("calendar_convention") == "CALENDAR" else CONVENTION_NOTE
    status = src["verification_status"] if src else None
    if status == SourceVerification.VERIFIED_OFFICIAL.value:
        return AppealWindow(ResponsePolicyState.COMPUTED, "Appeal period from a verified official source." + (" " + CONVENTION_NOTE if note else ""), note=note, **base)
    if status == SourceVerification.DEMO_ONLY.value and response_policy.is_demo_case(config) and settings.demo_rules_enabled:
        return AppealWindow(ResponsePolicyState.DEMO_COMPUTED, "DEMO ONLY appeal period: invented for demonstration, not an AICTE requirement.", demo=True, note=note or "DEMO ONLY: calendar days.", **base)
    return AppealWindow(ResponsePolicyState.UNVERIFIED, "The appeal period's source is not verified as official, so it is not used to set a deadline.", **base)
