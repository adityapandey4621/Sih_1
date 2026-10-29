"""Structured field extraction driven entirely by configuration.

The engine knows *how* to find a labelled value on a page; it has no idea which values matter.
Which fields to look for, and what labels precede them, come from a checklist item's `extraction`
specification in the rule version. Absence yields NOT_FOUND ("unable to extract"), never a guess,
and low confidence is reported as uncertainty, not as evidence that a document is invalid.
"""

import re
from dataclasses import dataclass, field
from datetime import date
from difflib import SequenceMatcher

from app.domain import ExtractionKind, FieldStatus, PageMethod
from app.processing.ocr import OcrLine

_MONTHS = {m: i for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"], 1)}
_MONTHS.update({m[:3]: i for m, i in list(_MONTHS.items())})
_MONTHS["sept"] = 9


@dataclass(frozen=True)
class FieldSpec:
    field_key: str
    label: str
    kind: str
    anchors: tuple[str, ...]
    required: bool = True
    compare_group: str | None = None
    compare_to_application: str | None = None

    @classmethod
    def from_config(cls, raw: dict) -> "FieldSpec":
        return cls(
            field_key=raw["field_key"], label=raw["label"], kind=raw.get("kind", ExtractionKind.TEXT), anchors=tuple(raw["anchors"]),
            required=raw.get("required", True), compare_group=raw.get("compare_group"), compare_to_application=raw.get("compare_to_application"),
        )


@dataclass
class PageLines:
    page_number: int
    method: str  # PageMethod
    lines: list[OcrLine]
    confidence: float | None = None


@dataclass
class FieldResult:
    field_key: str
    label: str
    kind: str
    status: str
    value: str | None = None
    normalised_value: str | None = None
    page_number: int | None = None
    method: str | None = None
    confidence: float | None = None
    evidence: str | None = None
    note: str | None = None
    alternatives: list[str] = field(default_factory=list)


def normalise_text(s: str) -> str:
    """Case-folded, punctuation-free, single-spaced. '&' reads as 'and'. Transparent and deterministic."""
    s = s.casefold().replace("&", " and ")
    s = re.sub(r"[^\w\s]", " ", s, flags=re.UNICODE).replace("_", " ")
    return re.sub(r"\s+", " ", s).strip()


def parse_date(text: str) -> tuple[str | None, str | None]:
    """First date found in `text` -> (ISO date, note). Day-first for numeric dates (dd/mm/yyyy), as is usual in India."""
    t = text.strip()
    m = re.search(r"\b(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})\b", t)
    if m:
        return _mk(int(m[1]), int(m[2]), int(m[3]))
    m = re.search(r"\b(\d{1,2})[-/. ](\d{1,2})[-/. ](\d{2,4})\b", t)
    if m:
        y = int(m[3]) + (2000 if len(m[3]) == 2 else 0)
        iso, note = _mk(y, int(m[2]), int(m[1]))
        if iso and int(m[1]) <= 12 and int(m[2]) <= 12 and int(m[1]) != int(m[2]):
            note = "numeric date read day-first (dd/mm/yyyy)"
        return iso, note
    m = re.search(r"\b(\d{1,2})(?:st|nd|rd|th)?[\s-]+([A-Za-z]{3,9})\.?,?[\s-]+(\d{4})\b", t)
    if m and m[2].casefold() in _MONTHS:
        return _mk(int(m[3]), _MONTHS[m[2].casefold()], int(m[1]))
    m = re.search(r"\b([A-Za-z]{3,9})\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})\b", t)
    if m and m[1].casefold() in _MONTHS:
        return _mk(int(m[3]), _MONTHS[m[1].casefold()], int(m[2]))
    return None, None


def _mk(y: int, mo: int, d: int) -> tuple[str | None, str | None]:
    try:
        return date(y, mo, d).isoformat(), None
    except ValueError:
        return None, None


def _anchor_regex(anchor: str) -> re.Pattern[str]:
    words = [re.escape(w) for w in re.findall(r"\w+", anchor, flags=re.UNICODE)]
    return re.compile(r"\b" + r"[\W_]+".join(words) + r"\b", re.IGNORECASE | re.UNICODE)


def _fuzzy_end(line: str, anchor: str) -> int | None:
    """OCR often garbles a character or two in a label. Slide a window of the anchor's word count over the line."""
    an = normalise_text(anchor)
    n = len(an.split())
    spans = [m.span() for m in re.finditer(r"\S+", line)]
    for i in range(0, len(spans) - n + 1):
        window = line[spans[i][0] : spans[i + n - 1][1]]
        if SequenceMatcher(None, normalise_text(window), an).ratio() >= 0.88:
            return spans[i + n - 1][1]
    return None


_LEAD = re.compile(r"^[\s:;=\-–—.|]+")


@dataclass
class _Candidate:
    raw: str
    page: PageLines
    line: OcrLine
    fuzzy: bool


def _candidates(spec: FieldSpec, pages: list[PageLines]) -> list[_Candidate]:
    out: list[_Candidate] = []
    for page in pages:
        for idx, line in enumerate(page.lines):
            for anchor in spec.anchors:
                m = _anchor_regex(anchor).search(line.text)
                end, fuzzy = (m.end(), False) if m else (None, False)
                if end is None and page.method == PageMethod.OCR and len(anchor) >= 6:
                    end = _fuzzy_end(line.text, anchor)
                    fuzzy = end is not None
                if end is None:
                    continue
                rest = _LEAD.sub("", line.text[end:]).strip()
                src = line
                if not rest and idx + 1 < len(page.lines):  # label on its own line: value follows
                    src = page.lines[idx + 1]
                    rest = src.text.strip()
                if rest:
                    out.append(_Candidate(rest, page, src, fuzzy))
                break
    return out


def _interpret(spec: FieldSpec, raw: str) -> tuple[str | None, str | None, str | None, str | None]:
    """-> (clean value, normalised value, reason-it-is-uncertain, informational note)."""
    if spec.kind == ExtractionKind.DATE:
        iso, info = parse_date(raw)
        if iso is None:
            return raw[:200], None, "could not be read as a date", None
        return raw[:200], iso, None, info  # day-first reading is stated, not treated as doubt
    if spec.kind == ExtractionKind.IDENTIFIER:
        m = re.search(r"[A-Za-z0-9][A-Za-z0-9/_.\-]{2,}", raw)
        if not m:
            return raw[:200], None, "no identifier-like token found", None
        return m[0], m[0].upper(), None, None
    return raw[:200].strip(), normalise_text(raw[:200]), None, None


def extract_fields(specs: list[FieldSpec], pages: list[PageLines], min_confidence: float) -> list[FieldResult]:
    results: list[FieldResult] = []
    for spec in specs:
        cands = _candidates(spec, pages)
        if not cands:
            results.append(FieldResult(spec.field_key, spec.label, spec.kind, FieldStatus.NOT_FOUND, note="No value was found after the expected label(s)."))
            continue
        interpreted = [(c, *_interpret(spec, c.raw)) for c in cands]
        distinct = {n for _, _, n, _, _ in interpreted if n}
        first, value, norm, doubt, info = interpreted[0]
        status = FieldStatus.EXTRACTED
        notes: list[str] = [info] if info else []
        if doubt:
            status, notes = FieldStatus.UNCERTAIN, notes + [doubt]
        if first.fuzzy:
            status, notes = FieldStatus.UNCERTAIN, notes + ["the label matched only approximately"]
        conf = first.line.confidence
        if conf is not None and conf < min_confidence:
            status, notes = FieldStatus.UNCERTAIN, notes + [f"low recognition confidence ({conf:.0f}%)"]
        if len(distinct) > 1:
            status, notes = FieldStatus.UNCERTAIN, notes + [f"{len(distinct)} different values were found in the document"]
        results.append(FieldResult(
            spec.field_key, spec.label, spec.kind, status, value=value, normalised_value=norm, page_number=first.page.page_number,
            method=first.page.method, confidence=conf, evidence=first.line.text[:300], note="; ".join(notes) or None,
            alternatives=sorted(distinct - {norm}) if len(distinct) > 1 else [],
        ))
    return results
