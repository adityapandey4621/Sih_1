"""Shape processing records for the API. The viewer decides what is shown: owners see their own evidence and
institution-appropriate wording; reviewers additionally see priorities, check names and the institution."""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.config import Settings
from app.evidence import DisplayState, current_documents, describe, scan_note
from app.models_applications import Application, ApplicationDocument
from app.models_processing import DocumentEvent, ExtractedField, ExtractedPage, PreliminaryFinding, ProcessingJob
from app.processing.issues import ISSUE_TYPES, NOT_A_DEFICIENCY
from app.processing.ocr import get_ocr_engine
from app.processing.scanner import get_scanner
from app.schemas_evidence import (
    ApplicationEvidenceOut,
    DocumentDetailOut,
    DocumentStateOut,
    EventOut,
    ExtractionOut,
    FieldOut,
    FindingOut,
    ItemEvidenceOut,
    PageOut,
    VersionOut,
)
from app.services.applications import config_of


def state_out(doc: ApplicationDocument, job: ProcessingJob | None) -> DocumentStateOut:
    proc = doc.processing
    s: DisplayState = describe(doc, proc, job)
    return DocumentStateOut(
        code=s.code, label=s.label, explanation=s.explanation, tone=s.tone, terminal=s.terminal, can_retry=s.can_retry,
        technical_status=proc.technical_status if proc else None, scan_status=proc.scan_status if proc else None, scan_note=scan_note(proc),
        extraction_status=proc.extraction_status if proc else None, page_count=proc.page_count if proc else None, text_method=proc.text_method if proc else None,
        review_recommended=bool(proc and proc.review_recommended), error_code=proc.last_error_code if proc else None,
    )


def _jobs_by_doc(db: Session, doc_ids: list[uuid.UUID]) -> dict[uuid.UUID, ProcessingJob]:
    if not doc_ids:
        return {}
    rows = db.scalars(select(ProcessingJob).where(ProcessingJob.document_id.in_(doc_ids), ProcessingJob.job_type == "PROCESS_DOCUMENT"))
    return {j.document_id: j for j in rows}


def version_out(doc: ApplicationDocument, job: ProcessingJob | None, current_id: uuid.UUID | None) -> VersionOut:
    return VersionOut(
        id=doc.id, item_key=doc.item_key, version_number=doc.version_number, original_filename=doc.original_filename, media_type=doc.media_type,
        size_bytes=doc.size_bytes, sha256=doc.sha256, uploaded_at=doc.uploaded_at, uploaded_by=doc.uploaded_by.full_name if doc.uploaded_by else None,
        is_current=doc.id == current_id, previous_version_id=doc.previous_version_id, state=state_out(doc, job),
    )


def finding_out(f: PreliminaryFinding, viewer: str, versions: dict[uuid.UUID, int]) -> FindingOut:
    t = ISSUE_TYPES.get(f.issue_type)
    reviewer = viewer == "reviewer"
    return FindingOut(
        id=f.id, issue_type=f.issue_type, label=t.label if t else f.issue_type, status=f.status, priority=f.priority if reviewer else None,
        check_key=f.check_key if reviewer else None, item_key=f.item_key, document_id=f.document_id, version_number=versions.get(f.document_id) if f.document_id else None,
        pages=list(f.pages or []), values=list(f.values or []), explanation=f.explanation, plain_text=(t.institute_text if t else "") or f.explanation,
        rule_ref=f.rule_ref, review_recommended=f.review_recommended, created_at=f.created_at, disclaimer=NOT_A_DEFICIENCY,
    )


def _field_out(f: ExtractedField, version: int) -> FieldOut:
    return FieldOut(
        field_key=f.field_key, label=f.label, kind=f.kind, status=f.status, value=f.value, normalised_value=f.normalised_value, page_number=f.page_number,
        method=f.method, confidence=f.confidence, evidence=f.evidence, note=f.note, document_id=f.document_id, version_number=version,
    )


def _page_out(p: ExtractedPage, with_text: bool = False) -> PageOut:
    return PageOut(page_number=p.page_number, method=p.method, status=p.status, confidence=p.confidence, char_count=p.char_count, note=p.note, text=p.text if with_text else None)


def application_evidence(db: Session, app: Application, viewer: str, settings: Settings) -> ApplicationEvidenceOut:
    config = config_of(app)
    docs_current = current_documents(db, app.id)
    all_docs = db.scalars(
        select(ApplicationDocument).where(ApplicationDocument.application_id == app.id, ApplicationDocument.removed_at.is_(None))
        .options(selectinload(ApplicationDocument.processing), selectinload(ApplicationDocument.uploaded_by)).order_by(ApplicationDocument.item_key, ApplicationDocument.version_number)
    ).all()
    jobs = _jobs_by_doc(db, [d.id for d in all_docs])
    version_of = {d.id: d.version_number for d in all_docs}
    findings = db.scalars(select(PreliminaryFinding).where(PreliminaryFinding.application_id == app.id, PreliminaryFinding.superseded_at.is_(None)).order_by(PreliminaryFinding.created_at)).all()
    if viewer != "reviewer":
        findings = [f for f in findings if f.institute_visible]

    items: list[ItemEvidenceOut] = []
    for i in config["checklist"]:
        if i["item_type"] != "document_upload":
            continue
        key = i["item_key"]
        cur = docs_current.get(key)
        versions = [version_out(d, jobs.get(d.id), cur.id if cur else None) for d in all_docs if d.item_key == key]
        fields: list[FieldOut] = []
        pages: list[PageOut] = []
        if cur is not None:
            fields = [_field_out(f, cur.version_number) for f in db.scalars(select(ExtractedField).where(ExtractedField.document_id == cur.id).order_by(ExtractedField.field_key))]
            pages = [_page_out(p) for p in db.scalars(select(ExtractedPage).where(ExtractedPage.document_id == cur.id).order_by(ExtractedPage.page_number))]
        current_version = next((v for v in versions if v.is_current), None)
        items.append(ItemEvidenceOut(
            item_key=key, label=i["label"], explanation=i["explanation"], required=i["required"], clause_ref=i.get("clause_ref"),
            configured_fields=[f["label"] for f in (i.get("extraction") or [])],
            slot_state=current_version.state if current_version else None, current=current_version, versions=versions, fields=fields, pages=pages,
            findings=[finding_out(f, viewer, version_of) for f in findings if f.item_key == key],
        ))
    ocr, scanner = get_ocr_engine(settings), get_scanner(settings)
    return ApplicationEvidenceOut(
        application_id=app.id, reference=app.reference, status=app.status, academic_year=app.academic_year, category_name=app.category.name,
        institution_name=app.institution.name if viewer == "reviewer" else None, viewer=viewer, items=items,
        cross_document_findings=[finding_out(f, viewer, version_of) for f in findings if f.item_key is None],
        disclaimer=NOT_A_DEFICIENCY, ocr_available=ocr.available(), malware_scanner=scanner.name,
    )


def versions_for_application(db: Session, app: Application) -> list[VersionOut]:
    cur = current_documents(db, app.id)
    docs = db.scalars(select(ApplicationDocument).where(ApplicationDocument.application_id == app.id, ApplicationDocument.removed_at.is_(None)).options(selectinload(ApplicationDocument.processing), selectinload(ApplicationDocument.uploaded_by)).order_by(ApplicationDocument.item_key, ApplicationDocument.version_number)).all()
    jobs = _jobs_by_doc(db, [d.id for d in docs])
    return [version_out(d, jobs.get(d.id), cur[d.item_key].id if d.item_key in cur else None) for d in docs]


def extraction(db: Session, doc: ApplicationDocument, job: ProcessingJob | None) -> ExtractionOut:
    pages = db.scalars(select(ExtractedPage).where(ExtractedPage.document_id == doc.id).order_by(ExtractedPage.page_number)).all()
    fields = db.scalars(select(ExtractedField).where(ExtractedField.document_id == doc.id).order_by(ExtractedField.field_key)).all()
    return ExtractionOut(
        document_id=doc.id, version_number=doc.version_number, state=state_out(doc, job),
        pages=[_page_out(p, with_text=True) for p in pages], fields=[_field_out(f, doc.version_number) for f in fields],
    )


def document_detail(db: Session, doc: ApplicationDocument, app: Application, viewer: str) -> DocumentDetailOut:
    siblings = db.scalars(select(ApplicationDocument).where(ApplicationDocument.slot_id == doc.slot_id).options(selectinload(ApplicationDocument.processing), selectinload(ApplicationDocument.uploaded_by)).order_by(ApplicationDocument.version_number)).all()
    jobs = _jobs_by_doc(db, [d.id for d in siblings])
    current = max((d for d in siblings if d.removed_at is None and d.superseded_at is None), key=lambda d: d.version_number, default=None)
    cid = current.id if current else None
    label = next((i["label"] for i in config_of(app)["checklist"] if i["item_key"] == doc.item_key), doc.item_key)
    me = next(d for d in siblings if d.id == doc.id)
    return DocumentDetailOut(
        version=version_out(me, jobs.get(me.id), cid), application_id=app.id, reference=app.reference, item_label=label,
        versions=[version_out(d, jobs.get(d.id), cid) for d in siblings if d.removed_at is None], viewer=viewer,
    )


def history(db: Session, doc: ApplicationDocument) -> list[EventOut]:
    rows = db.scalars(select(DocumentEvent).where(DocumentEvent.document_id == doc.id).options(selectinload(DocumentEvent.actor)).order_by(DocumentEvent.at))
    return [EventOut(at=e.at, actor=e.actor.full_name if e.actor else "System", action=e.action, detail=e.detail) for e in rows]
