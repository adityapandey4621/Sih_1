"""Pure functions that evaluate an application against a frozen rule-version configuration.

`config` is always a rule-version snapshot dict (see services.rules.build_snapshot), so a
submitted application is evaluated against exactly what applied when it was submitted.
"Satisfied" here means *the item has been supplied in a valid form*. It never means that
the content has been checked against any regulation.
"""

from dataclasses import dataclass
from datetime import date
from typing import Any

from app.domain import FieldKind, ItemType


@dataclass
class ItemState:
    item: dict
    applicable: bool
    status: str  # supplied | missing | invalid | not_applicable
    error: str | None = None
    document_id: str | None = None

    def as_dict(self) -> dict:
        i = self.item
        return {
            "item_key": i["item_key"], "label": i["label"], "explanation": i["explanation"], "required": i["required"],
            "item_type": i["item_type"], "field_kind": i["field_kind"], "options": i["options"], "validation": i["validation"], "applies_when": i.get("applies_when"),
            "clause_ref": i["clause_ref"], "source_id": i["source_id"], "applicable": self.applicable,
            "status": self.status, "error": self.error, "document_id": self.document_id,
        }


def _blank(v: Any) -> bool:
    return v is None or (isinstance(v, str) and not v.strip())


def condition_met(applies_when: dict | None, form_data: dict) -> bool:
    if not applies_when:
        return True
    value = form_data.get(applies_when["item_key"])
    if _blank(value):
        return False  # a dependent item does not apply until the item it depends on is answered
    if "in" in applies_when:
        return value in applies_when["in"]
    return value == applies_when.get("equals")


def validate_value(item: dict, value: Any) -> str | None:
    """Error message for a supplied value, or None. Blank values are handled by the caller."""
    kind = item.get("field_kind")
    rules = item.get("validation") or {}
    if item["item_type"] == ItemType.DECLARATION:
        return None if value is True else "Declarations must be confirmed (true)."
    if kind in (FieldKind.TEXT, FieldKind.LONG_TEXT):
        if not isinstance(value, str):
            return "Enter text."
        n = len(value.strip())
        if "min_length" in rules and n < rules["min_length"]:
            return f"Enter at least {int(rules['min_length'])} characters."
        limit = rules.get("max_length", 200 if kind == FieldKind.TEXT else 4000)
        if n > limit:
            return f"Enter at most {int(limit)} characters."
    elif kind == FieldKind.NUMBER:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return "Enter a number."
        if "min" in rules and value < rules["min"]:
            return f"Must be at least {rules['min']}."
        if "max" in rules and value > rules["max"]:
            return f"Must be at most {rules['max']}."
    elif kind == FieldKind.SELECT:
        if value not in (item.get("options") or []):
            return "Choose one of the listed options."
    elif kind == FieldKind.DATE:
        try:
            date.fromisoformat(str(value))
        except ValueError:
            return "Enter a valid date (YYYY-MM-DD)."
    return None


def input_items(config: dict) -> dict[str, dict]:
    """Items whose values arrive through the form (as opposed to file uploads)."""
    return {i["item_key"]: i for i in config["checklist"] if i["item_type"] in (ItemType.FORM_FIELD, ItemType.DECLARATION)}


def evaluate(config: dict, form_data: dict, documents: dict[str, str]) -> list[ItemState]:
    """`documents` maps item_key -> id of the active uploaded file."""
    states: list[ItemState] = []
    for item in config["checklist"]:
        key = item["item_key"]
        if not condition_met(item.get("applies_when"), form_data):
            states.append(ItemState(item, False, "not_applicable"))
            continue
        if item["item_type"] == ItemType.DOCUMENT_UPLOAD:
            doc = documents.get(key)
            states.append(ItemState(item, True, "supplied" if doc else "missing", document_id=doc))
            continue
        value = form_data.get(key)
        if _blank(value) or (item["item_type"] == ItemType.DECLARATION and value is False):
            states.append(ItemState(item, True, "missing"))
            continue
        err = validate_value(item, value)
        states.append(ItemState(item, True, "invalid" if err else "supplied", error=err))
    return states


def outstanding_required(states: list[ItemState]) -> list[ItemState]:
    return [s for s in states if s.applicable and s.item["required"] and s.status != "supplied"]


def summarise(states: list[ItemState]) -> dict:
    required = [s for s in states if s.applicable and s.item["required"]]
    return {
        "required_total": len(required),
        "required_supplied": sum(1 for s in required if s.status == "supplied"),
        "optional_total": sum(1 for s in states if s.applicable and not s.item["required"]),
        "optional_supplied": sum(1 for s in states if s.applicable and not s.item["required"] and s.status == "supplied"),
    }
