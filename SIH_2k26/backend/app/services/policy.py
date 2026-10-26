"""The response period of a deficiency notice, derived from the case's frozen rule configuration.

The rule is simple and strict: **a due date exists only when a usable, sourced policy says so.** There is no default
duration, none is carried between categories, and a later change to the rule catalogue never moves a date that was
already set (the policy is read from the application's frozen configuration, and the result is stored on the notice).

A policy is usable when all of these hold:
  * exactly one policy has the trigger event `deficiency_notice_issued`;
  * it states a duration and unit;
  * its calendar convention is CALENDAR (WORKING days would need a verified holiday calendar, which does not exist);
  * its source is VERIFIED_OFFICIAL - or DEMO_ONLY on a DEMO-classified case where demo rules are enabled.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from app.config import Settings
from app.domain import NOTICE_ISSUED_TRIGGER, ResponsePolicyState, SourceVerification


@dataclass
class PolicyResolution:
    state: ResponsePolicyState
    message: str
    policy_key: str | None = None
    deadline_type: str | None = None
    duration_value: int | None = None
    duration_unit: str | None = None
    calendar_convention: str | None = None
    source: dict | None = None
    clause_ref: str | None = None
    demo: bool = False
    extra: dict = field(default_factory=dict)

    @property
    def usable(self) -> bool:
        return self.state in (ResponsePolicyState.COMPUTED, ResponsePolicyState.DEMO_COMPUTED)

    @property
    def settled(self) -> bool:
        """Nothing is left unresolved: either a usable duration, or a verified official source that states there is none."""
        return self.usable or (self.state == ResponsePolicyState.NO_DURATION and bool(self.source) and self.source["verification_status"] == SourceVerification.VERIFIED_OFFICIAL.value)

    def due_at(self, issued_at: datetime) -> datetime | None:
        """Issue instant plus the stated period, counted as elapsed time (CALENDAR). The counting convention of the
        official text (whether the day of issue counts, cut-off times) is NOT verified; see docs/DECISIONS.md."""
        if not self.usable or self.duration_value is None:
            return None
        delta = timedelta(days=self.duration_value) if self.duration_unit == "days" else timedelta(hours=self.duration_value)
        return issued_at + delta

    def stable(self) -> dict:
        """The part that is part of what an issuer confirms (contains no moment in time)."""
        return {
            "state": self.state.value, "message": self.message, "policy_key": self.policy_key, "deadline_type": self.deadline_type,
            "duration_value": self.duration_value, "duration_unit": self.duration_unit, "calendar_convention": self.calendar_convention,
            "source": self.source, "clause_ref": self.clause_ref, "demo": self.demo,
        }


def is_demo_case(config: dict) -> bool:
    return config.get("rule_set", {}).get("classification") == "DEMO"


def resolve_response_policy(config: dict, settings: Settings) -> PolicyResolution:
    policies = [p for p in config.get("deadlines", []) if p.get("trigger_event") == NOTICE_ISSUED_TRIGGER]
    if not policies:
        return PolicyResolution(ResponsePolicyState.MISSING, "The approved configuration has no response-period policy for a deficiency notice.")
    if len(policies) > 1:
        return PolicyResolution(ResponsePolicyState.AMBIGUOUS, "The approved configuration has more than one response-period policy for a deficiency notice; none was chosen.")
    p = policies[0]
    src = next((s for s in config.get("sources", []) if s["id"] == p.get("source_id")), None)
    base = dict(
        policy_key=p["policy_key"], deadline_type=p.get("deadline_type"), duration_value=p.get("duration_value"), duration_unit=p.get("duration_unit"),
        calendar_convention=p.get("calendar_convention"), clause_ref=p.get("clause_ref"),
        source={"title": src["title"], "verification_status": src["verification_status"], "clause_ref": src.get("clause_ref")} if src else None,
    )
    if p.get("duration_value") is None:
        if src and src["verification_status"] == SourceVerification.VERIFIED_OFFICIAL.value:
            return PolicyResolution(ResponsePolicyState.NO_DURATION, "A verified official source states no fixed response period for this notice; the next scrutiny date is scheduled by the authority. No due date has been set.", **base)
        return PolicyResolution(ResponsePolicyState.NO_DURATION, "The policy exists but no response period is established by a verified source. No due date has been set.", **base)
    if p.get("calendar_convention") != "CALENDAR":
        why = "working days (there is no verified calendar to count them with)" if p.get("calendar_convention") == "WORKING" else "an unstated counting convention"
        return PolicyResolution(ResponsePolicyState.UNSUPPORTED, f"The policy counts in {why}, so no due date can be computed honestly.", **base)
    status = src["verification_status"] if src else None
    if status == SourceVerification.VERIFIED_OFFICIAL.value:
        return PolicyResolution(ResponsePolicyState.COMPUTED, "Response period from a verified official source.", **base)
    if status == SourceVerification.DEMO_ONLY.value and is_demo_case(config) and settings.demo_rules_enabled:
        return PolicyResolution(ResponsePolicyState.DEMO_COMPUTED, "DEMO ONLY response period: invented for demonstration, not an AICTE requirement.", demo=True, **base)
    return PolicyResolution(ResponsePolicyState.UNVERIFIED, "The response period's source is not verified as official, so it is not used to set a due date.", **base)
