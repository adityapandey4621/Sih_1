"""Reading document bytes: file-type sniffing, PDF/image opening, page text and page rendering.

All inputs here are untrusted. Every failure becomes a `ProcessingError` with a stable code;
nothing from the document or from the exception text reaches users.
"""

import io
from dataclasses import dataclass

import pymupdf
from PIL import Image, ImageOps

from app.config import Settings
from app.processing.errors import ProcessingError
from app.processing.ocr import OcrLine

# (signature, media type, allowed extensions). Extension and browser-declared type are never trusted on their own.
SIGNATURES: list[tuple[bytes, str, tuple[str, ...]]] = [
    (b"%PDF-", "application/pdf", (".pdf",)),
    (b"\x89PNG\r\n\x1a\n", "image/png", (".png",)),
    (b"\xff\xd8\xff", "image/jpeg", (".jpg", ".jpeg")),
]
IMAGE_TYPES = {"image/png", "image/jpeg"}


def sniff(head: bytes) -> tuple[str, tuple[str, ...]] | None:
    for sig, media, exts in SIGNATURES:
        if head.startswith(sig):
            return media, exts
    return None


@dataclass
class TechnicalReport:
    media_type: str
    page_count: int
    notes: dict


def open_pdf(data: bytes, settings: Settings) -> "pymupdf.Document":
    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception as exc:  # PyMuPDF raises several types for malformed input
        raise ProcessingError("pdf_corrupt", type(exc).__name__) from exc
    try:
        if doc.needs_pass:
            doc.close()
            raise ProcessingError("pdf_encrypted")
        if doc.page_count == 0:
            doc.close()
            raise ProcessingError("pdf_no_pages")
        if doc.page_count > settings.max_pdf_pages:
            pages = doc.page_count
            doc.close()
            raise ProcessingError("too_many_pages", f"{pages} pages")
        doc.load_page(0)  # forces parsing of the page tree: catches many truncated files
    except ProcessingError:
        raise
    except Exception as exc:
        doc.close()
        raise ProcessingError("pdf_corrupt", type(exc).__name__) from exc
    return doc


def load_image(data: bytes, settings: Settings) -> Image.Image:
    """Decode an untrusted image. The pixel limit is checked from the header *before* any pixels are decoded; Pillow's own
    decompression-bomb guard remains as a backstop. (Pillow's process-wide limit is deliberately not modified.)"""
    try:
        with Image.open(io.BytesIO(data)) as probe:
            probe.verify()
        img = Image.open(io.BytesIO(data))
        if img.width * img.height > settings.max_image_pixels:
            raise ProcessingError("image_too_large", f"{img.width}x{img.height}")
        img = ImageOps.exif_transpose(img)
        img.load()
        return img
    except ProcessingError:
        raise
    except Image.DecompressionBombError as exc:
        raise ProcessingError("image_too_large", "decompression bomb") from exc
    except Exception as exc:
        raise ProcessingError("file_unreadable", type(exc).__name__) from exc


def image_png_bytes(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="PNG")
    return buf.getvalue()


def technical_validate(data: bytes, media_type: str, settings: Settings) -> TechnicalReport:
    """Readability and structure only. Says nothing about whether the content is right."""
    if not data:
        raise ProcessingError("empty_file")
    kind = sniff(data[:16])
    if kind is None or kind[0] != media_type:
        raise ProcessingError("unsupported_format")
    if media_type == "application/pdf":
        doc = open_pdf(data, settings)
        try:
            return TechnicalReport(media_type, doc.page_count, {"pdf_permissions_restricted": bool(doc.is_encrypted)})
        finally:
            doc.close()
    img = load_image(data, settings)
    return TechnicalReport(media_type, 1, {"width": img.width, "height": img.height})


def pdf_page_lines(page: "pymupdf.Page", max_chars: int) -> list[OcrLine]:
    text = page.get_text("text") or ""
    return [OcrLine(ln.strip()) for ln in text[:max_chars].splitlines() if ln.strip()]


def render_page_png(page: "pymupdf.Page", dpi: int, max_pixels: int) -> bytes:
    rect = page.rect
    scale = dpi / 72
    while rect.width * scale * rect.height * scale > max_pixels and scale > 0.3:
        scale *= 0.8  # very large pages are rendered smaller rather than exhausting memory
    pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)
    return pix.tobytes("png")


def render_preview(data: bytes, media_type: str, page_number: int, settings: Settings, dpi: int = 110) -> bytes:
    """PNG of one page for the reviewer. Always rasterised: a PDF is never served inline to be executed by a viewer."""
    if media_type in IMAGE_TYPES:
        if page_number != 1:
            raise ProcessingError("file_unreadable", "page out of range")
        return image_png_bytes(load_image(data, settings))
    doc = open_pdf(data, settings)
    try:
        if not 1 <= page_number <= doc.page_count:
            raise ProcessingError("file_unreadable", "page out of range")
        return render_page_png(doc.load_page(page_number - 1), dpi, settings.max_image_pixels)
    finally:
        doc.close()
