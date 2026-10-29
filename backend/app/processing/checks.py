"""Deterministic preliminary checks over an application's *current* document versions.

Inputs are plain dataclasses (built from the database by `app.evidence`), outputs are draft
findings. Comparisons are exact after transparent normalisation; there is no fuzzy or semantic
matching. Each finding says which check produced it, which version and pages it concerns, and
the values compared, so a reviewer can verify it against the source page.

A finding is a *preliminary issue awaiting scrutiny review*. It is never a deficiency notice.
"""

import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass, field

from app.domain import ExtractionStatus, FieldStatus, PageStatus, ScanStatus, TechnicalStatus
from app.processing.fields import normalise_text
from app.processing.issues import ISSUE_TYPES


@dataclass
class PageFact:
    page_number: int
    method: str
    status: str
    confidence: float | None = None


@dataclass
class FieldFact:
    field_key: str
    label: str
    status: str
    value: str | None
    normalised_value: str | None
    page_number: int | None
    required: bool = True
    compare_group: str | None = None
    compare_to_application: str | None = None


@dataclass
class DocFacts:
    document_id: str
    version_number: int
    sha256: str
    technical_status: str
    technical_code: str | None
    scan_status: str
    extraction_status: str
    review_recommended: bool
    error_code: str | None
    pages: list[PageFact] = field(default_factory=list)
    fields: list[FieldFact] = field(default_factory=list)


@dataclass
class ItemFact:
    item_key: str
    label: str
    required: bool
    clause_ref: str | None
    is_document: bool
    doc: DocFacts | None = None


@dataclass
class AppFacts:
    institution_name: str
    submitted: bool
    items: list[ItemFact]


@dataclass
class FindingDraft:
    issue_type: str
    check_key: str
    explanation: str
    item_key: str | None = None
    document_id: str | None = None
    pages: list[int] = field(default_factory=list)
    values: list[dict] = field(default_factory=list)
    rule_ref: str | None = None

    @property
    def priority(self) -> str:
        return ISSUE_TYPES[self.issue_type].priority

    @property
    def institute_visible(self) -> bool:
        return ISSUE_TYPES[self.issue_type].institute_visible

    @property
    def review_recommended(self) -> bool:
        return self.priority != "info"

    @property
    def fingerprint(self) -> str:
        blob = json.dumps(
            [self.issue_type, self.item_key, self.document_id, sorted(self.pages), sorted(json.dumps(v, sort_keys=True) for v in self.values)],
            sort_keys=True,
        )
        return hashlib.sha256(blob.encode()).hexdigest()


def _ref(item: ItemFact) -> str:
    return f"{item.label}" + (f" ({item.clause_ref})" if item.clause_ref else "")


def run_checks(app: AppFacts, min_confidence: float) -> list[FindingDraft]:
    out: list[FindingDraft] = []
    by_hash: dict[str, list[ItemFact]] = defaultdict(list)
    groups: dict[str, list[tuple[ItemFact, DocFacts, FieldFact]]] = defaultdict(list)

    for item in app.items:
        doc = item.doc
        # Missing is not unreadable: only a submitted application with no file for a required item is reported as missing.
        if item.is_document and doc is None:
            if item.required and app.submitted:
                out.append(FindingDraft("required_file_not_supplied", "file_present", "No file is attached to this required checklist item.", item.item_key, rule_ref=_ref(item)))
            continue
        if doc is None:
            continue
        by_hash[doc.sha256].append(item)
        base = dict(item_key=item.item_key, document_id=doc.document_id, rule_ref=_ref(item))

        if doc.scan_status == ScanStatus.INFECTED:
            out.append(FindingDraft("malware_detected", "malware_scan", "The configured malware scanner flagged this file; it was quarantined and not processed.", **base))
            continue
        if doc.scan_status == ScanStatus.SCANNER_UNAVAILABLE and doc.technical_status == TechnicalStatus.PASSED:
            out.append(FindingDraft("scanner_unavailable", "malware_scan", "No malware scanner was available, so this file was not scanned.", **base))
        if doc.technical_status == TechnicalStatus.FAILED:
            out.append(FindingDraft("unsupported_or_corrupt_file", "technical_validation", f"The file could not be inspected ({doc.technical_code or 'unreadable'}). No text or values were extracted from it.", **base))
            continue
        if doc.extraction_status in (ExtractionStatus.QUEUED, ExtractionStatus.RUNNING, ExtractionStatus.NOT_STARTED):
            continue  # nothing to say until processing has finished
        if doc.extraction_status in (ExtractionStatus.BLOCKED, ExtractionStatus.FAILED):
            out.append(FindingDraft("text_extraction_failed", "text_extraction", f"Text could not be extracted from this document ({doc.error_code or 'processing failed'}). Its contents have not been read.", pages=[], **base))
            continue

        low = [p.page_number for p in doc.pages if p.status == PageStatus.LOW_CONFIDENCE]
        if low:
            out.append(FindingDraft("ocr_confidence_low", "ocr_confidence", f"Pages recognised with confidence below {min_confidence:.0f}%: values read from them are uncertain.",
                                    pages=low, values=[{"page": p.page_number, "confidence": p.confidence} for p in doc.pages if p.page_number in low], **base))
        unreadable = [p.page_number for p in doc.pages if p.status in (PageStatus.OCR_UNAVAILABLE, PageStatus.FAILED)]
        empty = [p.page_number for p in doc.pages if p.status == PageStatus.EMPTY]
        if unreadable or empty:
            out.append(FindingDraft("manual_inspection_recommended", "page_coverage", "Some pages could not be read ("
                                    + ("no OCR available" if unreadable else "no text recognised") + "); a person should check them.", pages=sorted(unreadable + empty), **base))
        if doc.pages and all(p.status == PageStatus.EMPTY for p in doc.pages):
            out.append(FindingDraft("text_extraction_failed", "text_extraction", "No text was recognised on any page.", **base))

        for f in doc.fields:
            if f.status == FieldStatus.NOT_FOUND and f.required:
                out.append(FindingDraft("expected_field_not_found", "expected_field", f"“{f.label}” was not found in the document.", **base))
            elif f.status == FieldStatus.UNCERTAIN:
                out.append(FindingDraft("field_value_uncertain", "field_certainty", f"“{f.label}” was found but could not be read with confidence.",
                                        pages=[f.page_number] if f.page_number else [], values=[{"field": f.field_key, "value": f.value, "page": f.page_number}], **base))
            if f.status == FieldStatus.EXTRACTED and f.normalised_value:
                if f.compare_group:
                    groups[f.compare_group].append((item, doc, f))
                if f.compare_to_application == "institution_name" and app.institution_name:
                    expected = normalise_text(app.institution_name)
                    if f.normalised_value != expected:
                        out.append(FindingDraft(
                            "cross_document_inconsistency", "application_record", f"“{f.label}” in this document differs from the institution name on the application record.",
                            pages=[f.page_number] if f.page_number else [],
                            values=[{"source": "document", "document_id": doc.document_id, "page": f.page_number, "value": f.value},
                                    {"source": "application_record", "value": app.institution_name}], **base))

    # Cross-document: values in the same compare group should agree after normalisation.
    for group, entries in sorted(groups.items()):
        distinct = {e[2].normalised_value for e in entries}
        if len(distinct) > 1:
            first = entries[0][0]
            out.append(FindingDraft(
                "cross_document_inconsistency", f"compare_group:{group}", f"Values for “{group}” differ between documents.",
                item_key=None, document_id=None, pages=sorted({e[2].page_number for e in entries if e[2].page_number}),
                values=[{"source": "document", "item_key": i.item_key, "document_id": d.document_id, "version": d.version_number, "page": f.page_number, "value": f.value} for i, d, f in entries],
                rule_ref=_ref(first),
            ))

    # Same bytes attached to different requirements.
    for digest, items in by_hash.items():
        if len({i.item_key for i in items}) > 1:
            for item in items:
                others = ", ".join(sorted(i.label for i in items if i.item_key != item.item_key))
                out.append(FindingDraft("potential_duplicate_file", "duplicate_hash", f"This file is identical to the one attached to: {others}.",
                                        item_key=item.item_key, document_id=item.doc.document_id if item.doc else None, rule_ref=_ref(item)))

    # A document flagged for review by the pipeline always gets at least one explanatory finding.
    explained = {f.document_id for f in out if f.document_id}
    for item in app.items:
        d = item.doc
        if d and d.review_recommended and d.document_id not in explained and d.technical_status == TechnicalStatus.PASSED:
            out.append(FindingDraft("manual_inspection_recommended", "pipeline_review_flag", "Processing flagged this document for human review.", item_key=item.item_key, document_id=d.document_id, rule_ref=_ref(item)))
    return out
