"""What a deadline policy of the FROZEN rule configuration lets the scheduler do. Nothing here has a default.

  * A due date exists only when exactly the same conditions hold that already govern response periods and appeal windows (services/policy.py,
    services/appeal_rules.py): the policy states a duration in days or hours, counts CALENDAR time, and its source is VERIFIED_OFFICIAL - or
    DEMO_ONLY on a DEMO-classified case where demo rules are enabled. Otherwise the clock is NOT_ESTABLISHED and says why.
  * The alert schedule, the pause rule and the escalation roles live in the policy's `escalation` field and nowhere else:

        {"alerts": {"pre_due_hours": [48, 24], "at_due": true, "overdue_hours": [24, 72]},
         "pause":  {"allowed": true, "reasons": ["awaiting_clarification"]},
         "escalate_to": ["regulation_bureau"]}

    No alerts are raised, no pause is allowed and nobody is escalated to unless the policy says so.
  * A rule version published later never touches a clock that exists: the snapshot taken here is stored on the clock.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from app.config import Settings
from app.domain import NOTIFICATION_ESCALATION_ROLES, ResponsePolicyState, SourceVerification
from app.services import policy as response_policy

MAX_OFFSETS = 6
MAX_HOURS = 24 * 365


@dataclass
class Extras:
    pre_due_hours: list[int] = field(default_factory=list)
    at_due: bool = False
    overdue_hours: list[int] = field(default_factory=list)
    pause_allowed: bool = False
    pause_reasons: list[str] = field(default_factory=list)
    escalate_to: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "alerts": {"pre_due_hours": self.pre_due_hours, "at_due": self.at_due, "overdue_hours": self.overdue_hours},
            "pause": {"allowed": self.pause_allowed, "reasons": self.pause_reasons}, "escalate_to": self.escalate_to,
        }


def _hours(raw: object, name: str, errors: list[str]) -> list[int]:
    if raw is None:
        return []
    if not isinstance(raw, list) or len(raw) > MAX_OFFSETS or any(isinstance(h, bool) or not isinstance(h, int) or not 1 <= h <= MAX_HOURS for h in raw) or len(set(raw)) != len(raw):
        errors.append(f"alerts.{name} must be a list of up to {MAX_OFFSETS} distinct whole numbers of hours between 1 and {MAX_HOURS}.")
        return []
    return sorted(raw, reverse=name == "pre_due_hours")


def parse_extras(raw: object) -> tuple[Extras, list[str]]:
    """Read the alert / pause / escalation settings of a policy. Unknown keys and malformed values are reported; whatever is malformed is ignored (never guessed)."""
    errors: list[str] = []
    if raw in (None, {}):
        return Extras(), errors
    if not isinstance(raw, dict):
        return Extras(), ["escalation must be an object."]
    for key in raw:
        if key not in ("alerts", "pause", "escalate_to"):
            errors.append(f"Unknown setting “{key}”. Allowed: alerts, pause, escalate_to.")
    out = Extras()
    alerts = raw.get("alerts")
    if alerts is not None:
        if not isinstance(alerts, dict) or any(k not in ("pre_due_hours", "at_due", "overdue_hours") for k in alerts):
            errors.append("alerts may only contain pre_due_hours, at_due and overdue_hours.")
        else:
            out.pre_due_hours = _hours(alerts.get("pre_due_hours"), "pre_due_hours", errors)
            out.overdue_hours = _hours(alerts.get("overdue_hours"), "overdue_hours", errors)
            at_due = alerts.get("at_due", False)
            if not isinstance(at_due, bool):
                errors.append("alerts.at_due must be true or false.")
            else:
                out.at_due = at_due
    pause = raw.get("pause")
    if pause is not None:
        if not isinstance(pause, dict) or any(k not in ("allowed", "reasons") for k in pause) or not isinstance(pause.get("allowed", False), bool):
            errors.append("pause may only contain allowed (true/false) and reasons (a list of codes).")
        else:
            reasons = pause.get("reasons", [])
            if not isinstance(reasons, list) or len(reasons) > 12 or any(not isinstance(r, str) or not 1 <= len(r) <= 60 for r in reasons):
                errors.append("pause.reasons must be a list of up to 12 short codes.")
            else:
                out.pause_allowed, out.pause_reasons = bool(pause.get("allowed", False)), reasons
    esc = raw.get("escalate_to")
    if esc is not None:
        if not isinstance(esc, list) or len(esc) > 6 or any(r not in NOTIFICATION_ESCALATION_ROLES for r in esc):
            errors.append(f"escalate_to may list only these roles: {', '.join(NOTIFICATION_ESCALATION_ROLES)}.")
        else:
            out.escalate_to = list(dict.fromkeys(esc))
    return out, errors


def validate_escalation(raw: object) -> list[str]:
    return parse_extras(raw)[1]


@dataclass
class ClockResolution:
    """How one policy applies to one subject. `usable` is the only thing that lets a due date exist."""

    state: ResponsePolicyState
    message: str
    policy_key: str
    label: str
    deadline_type: str
    trigger_event: str
    duration_value: int | None = None
    duration_unit: str | None = None
    calendar_convention: str | None = None
    clause_ref: str | None = None
    source: dict | None = None
    demo: bool = False
    extras: Extras = field(default_factory=Extras)
    note: str | None = None

    @property
    def usable(self) -> bool:
        return self.state in (ResponsePolicyState.COMPUTED, ResponsePolicyState.DEMO_COMPUTED)

    def due_from(self, trigger_at: datetime) -> datetime | None:
        if not self.usable or self.duration_value is None:
            return None
        return trigger_at + (timedelta(days=self.duration_value) if self.duration_unit == "days" else timedelta(hours=self.duration_value))

    def snapshot(self, rule_version_id: object = None) -> dict:
        return {
            "policy_key": self.policy_key, "label": self.label, "deadline_type": self.deadline_type, "trigger_event": self.trigger_event, "state": self.state.value,
            "message": self.message, "duration_value": self.duration_value, "duration_unit": self.duration_unit, "calendar_convention": self.calendar_convention,
            "clause_ref": self.clause_ref, "source": self.source, "demo": self.demo, "note": self.note, "rule_version_id": str(rule_version_id) if rule_version_id else None,
            **self.extras.as_dict(),
        }


def _source_of(config: dict, policy: dict) -> dict | None:
    src = next((s for s in config.get("sources", []) if s["id"] == policy.get("source_id")), None)
    return {"title": src["title"], "verification_status": src["verification_status"], "clause_ref": src.get("clause_ref")} if src else None


def source_usable(config: dict, settings: Settings, source: dict | None) -> tuple[ResponsePolicyState | None, bool]:
    """(COMPUTED | DEMO_COMPUTED | None, demo). None means the source cannot support a date or an alert schedule."""
    status = source["verification_status"] if source else None
    if status == SourceVerification.VERIFIED_OFFICIAL.value:
        return ResponsePolicyState.COMPUTED, False
    if status == SourceVerification.DEMO_ONLY.value and response_policy.is_demo_case(config) and settings.demo_rules_enabled:
        return ResponsePolicyState.DEMO_COMPUTED, True
    return None, False


def resolve_policy(config: dict, settings: Settings, policy: dict) -> ClockResolution:
    extras, _ = parse_extras(policy.get("escalation"))
    src = _source_of(config, policy)
    base = dict(
        policy_key=policy["policy_key"], label=policy.get("label") or policy["policy_key"], deadline_type=policy.get("deadline_type") or "INTERNAL_SLA", trigger_event=policy["trigger_event"],
        duration_value=policy.get("duration_value"), duration_unit=policy.get("duration_unit"), calendar_convention=policy.get("calendar_convention"), clause_ref=policy.get("clause_ref"),
        source=src, extras=extras,
    )
    if policy.get("duration_value") is None:
        return ClockResolution(ResponsePolicyState.NO_DURATION, "The policy states no duration, so no deadline has been set. Nothing is assumed.", **base)
    if policy.get("duration_unit") not in ("days", "hours"):
        return ClockResolution(ResponsePolicyState.UNSUPPORTED, "The policy's unit is not days or hours, so no deadline can be computed honestly.", **base)
    if policy.get("calendar_convention") != "CALENDAR":
        why = "working days (there is no verified calendar to count them with)" if policy.get("calendar_convention") == "WORKING" else "an unstated counting convention"
        return ClockResolution(ResponsePolicyState.UNSUPPORTED, f"The policy counts in {why}, so no deadline can be computed honestly.", **base)
    state, demo = source_usable(config, settings, src)
    if state is None:
        return ClockResolution(ResponsePolicyState.UNVERIFIED, "The policy's source is not verified as official, so it is not used to set a deadline.", **base)
    msg = "DEMO ONLY deadline: invented for demonstration, not an AICTE requirement." if demo else "Deadline from a verified official source."
    return ClockResolution(state, msg, demo=demo, **base)


def resolutions_for(config: dict, settings: Settings, trigger_event: str) -> list[ClockResolution]:
    """One resolution per policy that has this trigger. Distinct policies on the same trigger stay distinct (a statutory and an internal clock are separate)."""
    return [resolve_policy(config, settings, p) for p in sorted(config.get("deadlines", []), key=lambda d: d["policy_key"]) if p.get("trigger_event") == trigger_event]


def alert_policy(config: dict, settings: Settings, trigger_event: str) -> tuple[Extras, dict | None]:
    """The alert schedule attached to a trigger that has no duration of its own (document expiry). Used only if its source supports it."""
    for p in sorted(config.get("deadlines", []), key=lambda d: d["policy_key"]):
        if p.get("trigger_event") == trigger_event:
            extras, _ = parse_extras(p.get("escalation"))
            src = _source_of(config, p)
            state, _demo = source_usable(config, settings, src)
            if state is None:
                return Extras(), {"policy_key": p["policy_key"], "usable": False, "message": "The alert schedule's source is not verified as official, so no alerts are raised.", "source": src}
            return extras, {"policy_key": p["policy_key"], "usable": True, "source": src, "label": p.get("label"), "clause_ref": p.get("clause_ref"), **extras.as_dict()}
    return Extras(), None
