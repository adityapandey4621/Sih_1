"""The processing pipeline for one document version: validate -> scan -> read text (PDF text, OCR) -> extract fields.

`run_pipeline` is pure with respect to the database: it takes bytes and collaborators and returns a
result object. Persistence, retries and job ownership live in `app.jobs`, so every stage here can be
unit-tested without a website or a database.
"""

from collections.abc import Callable
from dataclasses import dataclass, field

from app.config import Settings
from app.domain import ExtractionStatus, PageMethod, PageStatus, ScanStatus, TechnicalStatus
from app.processing import documents
from app.processing.errors import ProcessingError
from app.processing.fields import FieldResult, FieldSpec, PageLines, extract_fields
from app.processing.ocr import OcrEngine
from app.processing.scanner import ScanResult, Scanner


@dataclass
class PageResult:
    page_number: int
    method: str
    status: str
    lines: list
    confidence: float | None = None
    note: str | None = None

    @property
    def text(self) -> str:
        return "\n".join(line.text for line in self.lines)


@dataclass
class PipelineOutput:
    technical_status: str = TechnicalStatus.PENDING
    technical_code: str | None = None
    technical_detail: dict = field(default_factory=dict)
    scan: ScanResult | None = None
    extraction_status: str = ExtractionStatus.NOT_STARTED
    error_code: str | None = None
    page_count: int | None = None
    pages: list[PageResult] = field(default_factory=list)
    fields: list[FieldResult] = field(default_factory=list)
    text_method: str | None = None
    review_recommended: bool = False

    @property
    def pages_ocr(self) -> int:
        return sum(1 for p in self.pages if p.method == PageMethod.OCR)

    @property
    def pages_unreadable(self) -> int:
        return sum(1 for p in self.pages if p.status in (PageStatus.OCR_UNAVAILABLE, PageStatus.FAILED))


def run_pipeline(
    data: bytes,
    media_type: str,
    specs: list[FieldSpec],
    settings: Settings,
    ocr: OcrEngine,
    scanner: Scanner,
    heartbeat: Callable[[], None] = lambda: None,
) -> PipelineOutput:
    out = PipelineOutput()

    # A. technical validation: a file that fails here never reaches OCR
    try:
        report = documents.technical_validate(data, media_type, settings)
    except ProcessingError as exc:
        out.technical_status, out.technical_code = TechnicalStatus.FAILED, exc.code
        out.extraction_status, out.error_code = ExtractionStatus.FAILED, exc.code
        return out
    out.technical_status = TechnicalStatus.PASSED
    out.page_count = report.page_count
    out.technical_detail = report.notes
    heartbeat()

    # B. malware scan: never reported as clean unless a scanner said so
    out.scan = scanner.scan(data)
    if out.scan.status == ScanStatus.INFECTED:
        out.extraction_status, out.error_code = ExtractionStatus.BLOCKED, "malware_detected"
        return out
    if out.scan.status == ScanStatus.SCANNER_UNAVAILABLE and settings.malware_scan_required:
        out.extraction_status, out.error_code = ExtractionStatus.BLOCKED, "scan_required_unavailable"
        return out
    heartbeat()

    # C. text: digital PDF text where present, OCR where a page has too little
    if media_type == "application/pdf":
        doc = documents.open_pdf(data, settings)
        try:
            for i in range(doc.page_count):
                out.pages.append(_read_pdf_page(doc.load_page(i), i + 1, settings, ocr))
                heartbeat()
        finally:
            doc.close()
    else:
        out.pages.append(_ocr_page(documents.image_png_bytes(documents.load_image(data, settings)), 1, settings, ocr))
        heartbeat()

    methods = {p.method for p in out.pages if p.method != PageMethod.NONE}
    out.text_method = "MIXED" if len(methods) > 1 else (next(iter(methods)) if methods else PageMethod.NONE)
    readable = [p for p in out.pages if p.status not in (PageStatus.OCR_UNAVAILABLE, PageStatus.FAILED)]
    if not readable:
        out.extraction_status, out.error_code = ExtractionStatus.FAILED, "ocr_unavailable"
        out.review_recommended = True
        return out

    # D. structured fields, driven by the checklist configuration
    out.fields = extract_fields(
        specs, [PageLines(p.page_number, p.method, p.lines, p.confidence) for p in readable], settings.ocr_min_confidence,
    )
    out.extraction_status = ExtractionStatus.PARTIAL if out.pages_unreadable else ExtractionStatus.COMPLETE
    out.review_recommended = bool(
        out.pages_unreadable
        or any(p.status in (PageStatus.LOW_CONFIDENCE, PageStatus.EMPTY) for p in out.pages)
        or any(f.status == "UNCERTAIN" or (f.status == "NOT_FOUND" and _required(specs, f.field_key)) for f in out.fields)
    )
    return out


def _required(specs: list[FieldSpec], key: str) -> bool:
    return next((s.required for s in specs if s.field_key == key), False)


def _read_pdf_page(page, number: int, settings: Settings, ocr: OcrEngine) -> PageResult:
    lines = documents.pdf_page_lines(page, settings.max_page_text_chars)
    if sum(len(line.text) for line in lines) >= settings.pdf_min_text_chars:
        return PageResult(number, PageMethod.PDF_TEXT, PageStatus.OK, lines)
    if not ocr.available():
        return PageResult(number, PageMethod.NONE, PageStatus.OCR_UNAVAILABLE, [], note="Scanned page; no OCR engine is available.")
    return _ocr_page(documents.render_page_png(page, settings.ocr_dpi, settings.max_image_pixels), number, settings, ocr)


def _ocr_page(png: bytes, number: int, settings: Settings, ocr: OcrEngine) -> PageResult:
    if not ocr.available():
        return PageResult(number, PageMethod.NONE, PageStatus.OCR_UNAVAILABLE, [], note="No OCR engine is available.")
    page = ocr.recognise(png)  # may raise a transient ProcessingError; the job layer retries it
    lines = [ln for ln in page.lines if ln.text.strip()]
    if not lines:
        return PageResult(number, PageMethod.OCR, PageStatus.EMPTY, [], page.mean_confidence, "No text was recognised.")
    status = PageStatus.LOW_CONFIDENCE if (page.mean_confidence is not None and page.mean_confidence < settings.ocr_min_confidence) else PageStatus.OK
    note = f"Recognised with {page.mean_confidence:.0f}% mean confidence." if page.mean_confidence is not None else None
    return PageResult(number, PageMethod.OCR, status, lines, page.mean_confidence, note)
