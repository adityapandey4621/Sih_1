"""Error taxonomy for the pipeline.

Retry behaviour follows from the *kind* of failure, so a corrupt file never loops and a flaky
disk is not treated as the institution's fault:

* INPUT      - the file itself is the problem (corrupt, encrypted, malformed). Permanent: no retry.
* CONFIG     - the environment lacks something (no OCR engine, scanning required but absent).
               Not retried automatically; a person may request a retry once it is fixed.
* TRANSIENT  - may succeed later (storage hiccup, OCR timeout). Retried with backoff, then fails visibly.
"""

from dataclasses import dataclass
from enum import StrEnum


class ErrorKind(StrEnum):
    INPUT = "input"
    CONFIG = "config"
    TRANSIENT = "transient"


@dataclass(frozen=True)
class ErrorInfo:
    kind: ErrorKind
    message: str  # shown to people: safe, no paths, no document content


# code -> (kind, message). Messages are deliberately generic about internals.
CATALOGUE: dict[str, ErrorInfo] = {
    "empty_file": ErrorInfo(ErrorKind.INPUT, "The file is empty."),
    "unsupported_format": ErrorInfo(ErrorKind.INPUT, "The file type is not supported."),
    "file_unreadable": ErrorInfo(ErrorKind.INPUT, "The file could not be opened. It may be damaged."),
    "pdf_corrupt": ErrorInfo(ErrorKind.INPUT, "The PDF is damaged or malformed and could not be read."),
    "pdf_encrypted": ErrorInfo(ErrorKind.INPUT, "The PDF is password-protected, so its contents cannot be read. Upload an unprotected copy."),
    "pdf_no_pages": ErrorInfo(ErrorKind.INPUT, "The PDF contains no pages."),
    "too_many_pages": ErrorInfo(ErrorKind.INPUT, "The document has more pages than can be processed."),
    "image_too_large": ErrorInfo(ErrorKind.INPUT, "The image is larger than can be processed safely."),
    "malware_detected": ErrorInfo(ErrorKind.INPUT, "The malware scan reported a problem with this file. It has been quarantined."),
    "ocr_unavailable": ErrorInfo(ErrorKind.CONFIG, "Text recognition (OCR) is not available on this system, so scanned pages could not be read."),
    "scan_required_unavailable": ErrorInfo(ErrorKind.CONFIG, "Malware scanning is required but no scanner is available. The file is held until scanning is configured."),
    "storage_unavailable": ErrorInfo(ErrorKind.TRANSIENT, "The stored file could not be read just now."),
    "ocr_timeout": ErrorInfo(ErrorKind.TRANSIENT, "Text recognition took too long on this document."),
    "ocr_failed": ErrorInfo(ErrorKind.TRANSIENT, "Text recognition failed."),
    "scanner_error": ErrorInfo(ErrorKind.TRANSIENT, "The malware scanner could not be reached or returned an error."),
    "worker_lost": ErrorInfo(ErrorKind.TRANSIENT, "Processing was interrupted and will be retried."),
    "unexpected": ErrorInfo(ErrorKind.TRANSIENT, "An unexpected error occurred while processing."),
}


class ProcessingError(Exception):
    """Raised by pipeline stages. `code` indexes CATALOGUE; `detail` is for logs only (never user-visible)."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail

    @property
    def info(self) -> ErrorInfo:
        return CATALOGUE.get(self.code, CATALOGUE["unexpected"])

    @property
    def kind(self) -> ErrorKind:
        return self.info.kind
