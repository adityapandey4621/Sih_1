"""Persisting processing results, deriving preliminary findings, and describing document state in plain words.

Nothing in this module changes an application's status. Processing results are additional records
about documents; they never approve, reject or advance an application.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, selectinload

from app.config import Settings
from app.domain import ExtractionStatus, JobStatus, ScanStatus, TechnicalStatus
from app.jobs import add_event
from app.models import utcnow
from app.models_applications import Application, ApplicationDocument
from app.models_processing import DocumentProcessing, DocumentSlot, ExtractedField, ExtractedPage, PreliminaryFinding, ProcessingJob
from app.processing import PROCESSOR_VERSION, checks
from app.processing.errors import CATALOGUE
from app.processing.fields import FieldSpec
from app.processing.pipeline import PipelineOutput
from app.services.applications import config_of

TECHNICAL_ONLY = "These are technical checks on the file only. They are not verification, and do not mean the document has been accepted."


# ---- extraction configuration -------------------------------------------------------------------


def item_config(config: dict, item_key: str) -> dict | None:
    return next((i for i in config["checklist"] if i["item_key"] == item_key), None)


def field_specs(config: dict, item_key: str) -> list[FieldSpec]:
    item = item_config(config, item_key)
    return [FieldSpec.from_config(f) for f in (item.get("extraction") or [])] if item else []


# ---- persisting a pipeline run ------------------------------------------------------------------


def apply_output(db: Session, doc: ApplicationDocument, job: ProcessingJob, out: PipelineOutput, now: datetime | None = None) -> None:
    """Replace this document version's results with `out`, atomically. Idempotent: reruns replace, never append."""
    now = now or utcnow()
    proc = db.scalar(select(DocumentProcessing).where(DocumentProcessing.document_id == doc.id))
    proc.technical_status = out.technical_status
    proc.technical_code = out.technical_code
    proc.technical_detail = out.technical_detail or None
    if out.scan is not None:
        proc.scan_status, proc.scan_detail = out.scan.status.value, {"scanner": out.scan.detail}
    proc.extraction_status = out.extraction_status
    proc.page_count = out.page_count
    proc.pages_ocr, proc.pages_unreadable = out.pages_ocr, out.pages_unreadable
    proc.text_method = out.text_method
    proc.review_recommended = out.review_recommended
    proc.last_error_code = out.error_code
    proc.last_error_message = CATALOGUE[out.error_code].message if out.error_code in CATALOGUE else None
    proc.processor_version = PROCESSOR_VERSION
    proc.updated_at = proc.completed_at = now

    db.execute(delete(ExtractedPage).where(ExtractedPage.document_id == doc.id))
    db.execute(delete(ExtractedField).where(ExtractedField.document_id == doc.id))
    db.flush()
    for p in out.pages:
        db.add(ExtractedPage(
            document_id=doc.id, page_number=p.page_number, method=p.method, status=p.status, text=p.text, char_count=len(p.text),
            confidence=p.confidence, note=p.note,
        ))
    for f in out.fields:
        db.add(ExtractedField(
            document_id=doc.id, job_id=job.id, field_key=f.field_key, label=f.label, kind=str(f.kind), status=f.status, value=f.value,
            normalised_value=f.normalised_value, page_number=f.page_number, method=f.method, confidence=f.confidence, evidence=f.evidence, note=f.note,
        ))
    add_event(db, doc.id, "processing_finished", {
        "extraction_status": out.extraction_status, "technical_status": out.technical_status, "pages": len(out.pages),
        "fields": len(out.fields), "review_recommended": out.review_recommended, "error_code": out.error_code,
    })


# ---- facts and findings -------------------------------------------------------------------------


def current_documents(db: Session, application_id: uuid.UUID) -> dict[str, ApplicationDocument]:
    slots = db.scalars(select(DocumentSlot).where(DocumentSlot.application_id == application_id)).all()
    ids = [s.current_version_id for s in slots if s.current_version_id]
    if not ids:
        return {}
    docs = db.scalars(select(ApplicationDocument).where(ApplicationDocument.id.in_(ids), ApplicationDocument.removed_at.is_(None)).options(selectinload(ApplicationDocument.processing))).all()
    return {d.item_key: d for d in docs}


def build_facts(db: Session, app: Application) -> checks.AppFacts:
    config = config_of(app)
    docs = current_documents(db, app.id)
    items: list[checks.ItemFact] = []
    for i in config["checklist"]:
        if i["item_type"] != "document_upload":
            continue
        d = docs.get(i["item_key"])
        facts = None
        if d is not None and d.processing is not None:
            p = d.processing
            specs = {f["field_key"]: f for f in (i.get("extraction") or [])}
            pages = db.scalars(select(ExtractedPage).where(ExtractedPage.document_id == d.id).order_by(ExtractedPage.page_number)).all()
            fields = db.scalars(select(ExtractedField).where(ExtractedField.document_id == d.id)).all()
            facts = checks.DocFacts(
                document_id=str(d.id), version_number=d.version_number, sha256=d.sha256, technical_status=p.technical_status, technical_code=p.technical_code,
                scan_status=p.scan_status, extraction_status=p.extraction_status, review_recommended=p.review_recommended, error_code=p.last_error_code,
                pages=[checks.PageFact(x.page_number, x.method, x.status, x.confidence) for x in pages],
                fields=[checks.FieldFact(f.field_key, f.label, f.status, f.value, f.normalised_value, f.page_number, specs.get(f.field_key, {}).get("required", True),
                                         specs.get(f.field_key, {}).get("compare_group"), specs.get(f.field_key, {}).get("compare_to_application")) for f in fields],
            )
        items.append(checks.ItemFact(i["item_key"], i["label"], i["required"], i.get("clause_ref"), True, facts))
    return checks.AppFacts(app.institution.name, app.status == "SUBMITTED", items)


def recompute_findings(db: Session, application_id: uuid.UUID, settings: Settings, now: datetime | None = None) -> dict:
    """Make the active findings equal what the current evidence produces. Old ones are kept, marked SUPERSEDED."""
    now = now or utcnow()
    app = db.scalars(select(Application).where(Application.id == application_id).options(selectinload(Application.institution), selectinload(Application.rule_version))).one()
    drafts = checks.run_checks(build_facts(db, app), settings.ocr_min_confidence)
    want = {d.fingerprint: d for d in drafts}
    active = {f.fingerprint: f for f in db.scalars(select(PreliminaryFinding).where(PreliminaryFinding.application_id == application_id, PreliminaryFinding.superseded_at.is_(None)))}
    gone = [f for fp, f in active.items() if fp not in want]
    for f in gone:
        f.status, f.superseded_at = "SUPERSEDED", now
    created = 0
    for fp, d in want.items():
        if fp in active:
            continue
        db.add(PreliminaryFinding(
            application_id=application_id, issue_type=d.issue_type, check_key=d.check_key, status="PRELIMINARY_ISSUE", priority=d.priority,
            item_key=d.item_key, document_id=uuid.UUID(d.document_id) if d.document_id else None, pages=d.pages, values=d.values,
            explanation=d.explanation, rule_ref=d.rule_ref, review_recommended=d.review_recommended, institute_visible=d.institute_visible, fingerprint=fp, created_at=now,
        ))
        created += 1
    return {"active": len(want), "created": created, "superseded": len(gone)}


# ---- plain-language state -----------------------------------------------------------------------


@dataclass(frozen=True)
class DisplayState:
    code: str
    label: str
    explanation: str
    tone: str  # neutral | info | success | warning | error
    terminal: bool  # nothing further will happen without a person or a new upload
    can_retry: bool = False


def describe(doc: ApplicationDocument, proc: DocumentProcessing | None, job: ProcessingJob | None) -> DisplayState:
    """The single place that turns internal state into the words a person sees."""
    if doc.removed_at is not None:
        return DisplayState("removed", "Removed", "This file was removed from the draft.", "neutral", True)
    if doc.superseded_at is not None:
        return DisplayState("superseded", "Replaced by a newer version", "An earlier version, kept for the record.", "neutral", True)
    if proc is None:
        return DisplayState("received", "File received", "The file is stored and waiting to be checked.", "info", False)
    if proc.scan_status == ScanStatus.INFECTED:
        return DisplayState("quarantined", "File quarantined", CATALOGUE["malware_detected"].message, "error", True)
    if proc.technical_status == TechnicalStatus.FAILED:
        msg = CATALOGUE.get(proc.technical_code or "", CATALOGUE["file_unreadable"]).message
        return DisplayState("technical_failed", "File could not be read", msg + " Upload a corrected file.", "error", True)
    st = proc.extraction_status
    retry = bool(job and job.retry_eligible)
    if st == ExtractionStatus.BLOCKED:
        return DisplayState("held", "Held before processing", (proc.last_error_message or "Processing is on hold."), "warning", True, retry)
    if st == ExtractionStatus.FAILED:
        waiting = "Retry is available." if retry else ""
        return DisplayState("processing_failed", "Processing failed", f"{proc.last_error_message or 'Processing failed.'} {waiting}".strip(), "error", True, retry)
    if st == ExtractionStatus.RUNNING:
        return DisplayState("extraction_running", "Technical checks in progress", "The file is being read. " + TECHNICAL_ONLY, "info", False)
    if st in (ExtractionStatus.QUEUED, ExtractionStatus.NOT_STARTED):
        retrying = bool(job and job.status == JobStatus.RETRY_WAIT.value)
        return DisplayState("extraction_queued", "Extraction queued", ("A temporary problem occurred; processing will be tried again. " if retrying else "Waiting for processing to start. ") + TECHNICAL_ONLY, "info", False)
    if st in (ExtractionStatus.COMPLETE, ExtractionStatus.PARTIAL):
        if proc.review_recommended or st == ExtractionStatus.PARTIAL:
            return DisplayState("review_recommended", "Human review recommended", "Reading finished, but parts were uncertain or could not be read. " + TECHNICAL_ONLY, "warning", True)
        return DisplayState("extraction_complete", "Extraction complete", "Text and values were read. " + TECHNICAL_ONLY, "success", True)
    return DisplayState("received", "File received", TECHNICAL_ONLY, "info", False)


def scan_note(proc: DocumentProcessing | None) -> str | None:
    if proc is None or proc.scan_status == ScanStatus.PENDING:
        return None
    return {
        ScanStatus.CLEAN: "Malware scan: no threat reported by the scanner.",
        ScanStatus.SCANNER_UNAVAILABLE: "Malware scan: NOT performed (no scanner is configured). This file has not been scanned.",
        ScanStatus.INFECTED: "Malware scan: the scanner flagged this file.",
        ScanStatus.ERROR: "Malware scan: the scanner reported an error.",
    }.get(ScanStatus(proc.scan_status))


def reset_processing_row(db: Session, doc: ApplicationDocument) -> DocumentProcessing:
    proc = DocumentProcessing(document_id=doc.id, technical_status=TechnicalStatus.PENDING.value, scan_status=ScanStatus.PENDING.value, extraction_status=ExtractionStatus.QUEUED.value)
    db.add(proc)
    return proc

