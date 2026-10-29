"""Synthetic test documents. Nothing here resembles a real institutional document."""

import io
import os
import shutil

import pymupdf
from PIL import Image, ImageFilter

from app.processing.ocr import OcrEngine, OcrLine, OcrPage, TesseractOcr


def _page_pdf(pages: list[list[str]]) -> "pymupdf.Document":
    doc = pymupdf.open()
    for lines in pages:
        page = doc.new_page(width=595, height=842)
        y = 90
        for line in lines:
            page.insert_text((60, y), line, fontsize=15, fontname="helv")
            y += 28
    return doc


def digital_pdf(pages: list[list[str]]) -> bytes:
    doc = _page_pdf(pages)
    data = doc.tobytes()
    doc.close()
    return data


def page_png(lines: list[str], dpi: int = 200) -> bytes:
    doc = _page_pdf([lines])
    png = doc.load_page(0).get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY).tobytes("png")
    doc.close()
    return png


def _scan_jpeg(lines: list[str], dpi: int = 200) -> bytes:
    """What a scanner produces: a grayscale JPEG of the page (small, like a real scan)."""
    img = Image.open(io.BytesIO(page_png(lines, dpi))).convert("L")
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


def scanned_pdf(pages: list[list[str]], dpi: int = 200) -> bytes:
    """A PDF whose pages are pictures of text: no extractable text layer, like a scan."""
    out = pymupdf.open()
    for lines in pages:
        page = out.new_page(width=595, height=842)
        page.insert_image(page.rect, stream=_scan_jpeg(lines, dpi))
    data = out.tobytes()
    out.close()
    return data


def mixed_pdf(digital_page: list[str], scanned_page: list[str]) -> bytes:
    out = pymupdf.open()
    p1 = out.new_page(width=595, height=842)
    y = 90
    for line in digital_page:
        p1.insert_text((60, y), line, fontsize=15, fontname="helv")
        y += 28
    p2 = out.new_page(width=595, height=842)
    p2.insert_image(p2.rect, stream=_scan_jpeg(scanned_page))
    data = out.tobytes()
    out.close()
    return data


def encrypted_pdf() -> bytes:
    doc = _page_pdf([["Secret"]])
    data = doc.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="user-pass", owner_pw="owner-pass")
    doc.close()
    return data


def corrupt_pdf() -> bytes:
    good = digital_pdf([["Name of the Institution: Broken"]])
    return good[: len(good) // 3]


def blurry_png(lines: list[str]) -> bytes:
    """Deliberately poor scan: small, blurred, noisy."""
    img = Image.open(io.BytesIO(page_png(lines, dpi=40))).convert("L").filter(ImageFilter.GaussianBlur(1.4))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def jpeg_of(lines: list[str]) -> bytes:
    img = Image.open(io.BytesIO(page_png(lines))).convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


class FakeOcr:
    """Scripted OCR: returns canned pages (consumed in order) or raises. Records every call."""

    name = "fake"

    def __init__(self, pages: list[OcrPage] | None = None, *, available: bool = True, error: Exception | None = None) -> None:
        self.pages = list(pages or [])
        self._available = available
        self.error = error
        self.calls = 0

    def available(self) -> bool:
        return self._available

    def recognise(self, image_png: bytes) -> OcrPage:
        self.calls += 1
        if self.error:
            raise self.error
        return self.pages.pop(0) if self.pages else OcrPage([], None)


def ocr_page(*lines: tuple[str, float]) -> OcrPage:
    ls = [OcrLine(t, c) for t, c in lines]
    return OcrPage(ls, round(sum(c for _, c in lines) / len(lines), 1) if lines else None)


def real_tesseract() -> OcrEngine | None:
    """A real Tesseract if one is configured (TEST_TESSERACT_CMD / TEST_TESSDATA) or on PATH; otherwise None."""
    cmd = os.environ.get("TEST_TESSERACT_CMD") or shutil.which("tesseract") or ""
    if not cmd:
        return None
    engine = TesseractOcr(cmd, os.environ.get("TEST_TESSDATA", ""), "eng", 200, 120)
    return engine if engine.available() else None
