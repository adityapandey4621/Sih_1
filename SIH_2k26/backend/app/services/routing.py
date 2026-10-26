"""Next-stage resolution from the application's *frozen* rule configuration.

Nothing here guesses. The route is data (route stages in the rule version the application was submitted
under). If it cannot be applied unambiguously the answer is EXCEPTION or AWAITING_DETERMINATION, never a
default destination. Appearing in a route is not a claim about what AICTE requires: the route is exactly as
trustworthy as the rule version that carries it (see its classification and source verification).
"""

from dataclasses import dataclass, field

from app.domain import STAGE_KEY_SCRUTINY, HandoffStatus


@dataclass
class RouteResolution:
    status: HandoffStatus
    reason: str | None = None
    from_stage_key: str | None = None
    to_stage_key: str | None = None
    to_stage_label: str | None = None
    options: list[dict] = field(default_factory=list)  # candidate next stages (for a person to choose among)
    basis: dict = field(default_factory=dict)


def _stage(stages: list[dict], key: str) -> dict | None:
    return next((s for s in stages if s["stage_key"] == key), None)


def _ref(s: dict) -> dict:
    return {"stage_key": s["stage_key"], "label": s["label"], "is_conditional": bool(s.get("is_conditional")), "condition_description": s.get("condition_description"), "clause_ref": s.get("clause_ref")}


def resolve_after(config: dict, from_key: str) -> RouteResolution:
    """What the frozen route says follows the stage `from_key`. Never a default: ambiguity is EXCEPTION or AWAITING_DETERMINATION."""
    stages: list[dict] = config.get("route") or []
    version = config.get("version", {})
    basis = {"rule_set": config.get("rule_set", {}).get("key"), "rule_version": version.get("number"), "classification": config.get("rule_set", {}).get("classification")}
    here_all = [s for s in stages if s["stage_key"] == from_key]
    if len(here_all) != 1:
        why = f"The approved route has no “{from_key}” stage." if not here_all else f"The approved route has more than one “{from_key}” stage."
        return RouteResolution(HandoffStatus.EXCEPTION, why + " The next stage cannot be determined, and none is assumed.", basis=basis)
    here = here_all[0]
    basis["from_stage"] = _ref(here)
    nxt_keys = list(here.get("next_stage_keys") or [])
    nxt = [_stage(stages, k) for k in nxt_keys]
    if any(s is None for s in nxt):
        return RouteResolution(HandoffStatus.EXCEPTION, f"The “{from_key}” stage names a next stage that does not exist in the route.", here["stage_key"], basis=basis)
    options = [_ref(s) for s in nxt if s is not None]
    basis["options"] = options
    if not options:
        return RouteResolution(HandoffStatus.NO_FURTHER_STAGE, f"The approved route configures no stage after “{from_key}”.", here["stage_key"], basis=basis)
    if len(options) == 1 and not options[0]["is_conditional"]:
        o = options[0]
        return RouteResolution(HandoffStatus.QUEUED, None, here["stage_key"], o["stage_key"], o["label"], options, basis)
    if len(options) == 1:
        o = options[0]
        return RouteResolution(
            HandoffStatus.AWAITING_DETERMINATION,
            f"“{o['label']}” applies only under a condition ({o['condition_description'] or 'not described'}). An authorised person must determine whether it applies.",
            here["stage_key"], None, None, options, basis,
        )
    return RouteResolution(HandoffStatus.AWAITING_DETERMINATION, "The route offers more than one next stage. An authorised person must choose.", here["stage_key"], None, None, options, basis)


def resolve_next(config: dict) -> RouteResolution:
    """What follows scrutiny (Phase 4)."""
    scrutiny = [s for s in (config.get("route") or []) if s["stage_key"] == STAGE_KEY_SCRUTINY]
    if len(scrutiny) != 1:
        version = config.get("version", {})
        basis = {"rule_set": config.get("rule_set", {}).get("key"), "rule_version": version.get("number"), "classification": config.get("rule_set", {}).get("classification")}
        why = "The approved route has no scrutiny stage." if not scrutiny else "The approved route has more than one scrutiny stage."
        return RouteResolution(HandoffStatus.EXCEPTION, why + " The next stage cannot be determined, and none is assumed.", basis=basis)
    return resolve_after(config, STAGE_KEY_SCRUTINY)
