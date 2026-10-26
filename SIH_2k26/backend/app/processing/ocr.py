"""OCR boundary. The default implementation drives a local Tesseract executable; nothing leaves the machine."""

import os
import subprocess
from dataclasses import dataclass, field
from statistics import mean
from typing import Protocol

from app.config import Settings
from app.processing.errors import ProcessingError


@dataclass
class OcrLine:
    text: str
    confidence: float | None = None  # mean word confidence 0-100; None for digital text


@dataclass
class OcrPage:
    lines: list[OcrLine] = field(default_factory=list)
    mean_confidence: float | None = None

    @property
    def text(self) -> str:
        return "\n".join(line.text for line in self.lines)


class OcrEngine(Protocol):
    name: str

    def available(self) -> bool: ...

    def recognise(self, image_png: bytes) -> OcrPage: ...


class NullOcr:
    name = "none"

    def available(self) -> bool:
        return False

    def recognise(self, image_png: bytes) -> OcrPage:  # noqa: ARG002
        raise ProcessingError("ocr_unavailable")


def parse_tsv(tsv: str) -> OcrPage:
    """Tesseract TSV -> lines with per-line mean confidence. Words with conf < 0 (structure rows) are ignored."""
    lines: dict[tuple[str, str, str], list[tuple[str, float]]] = {}
    order: list[tuple[str, str, str]] = []
    for row in tsv.splitlines()[1:]:
        cols = row.split("\t")
        if len(cols) < 12 or cols[0] != "5":
            continue
        text = cols[11].strip()
        try:
            conf = float(cols[10])
        except ValueError:
            continue
        if not text or conf < 0:
            continue
        key = (cols[2], cols[3], cols[4])
        if key not in lines:
            lines[key] = []
            order.append(key)
        lines[key].append((text, conf))
    result = [OcrLine(" ".join(w for w, _ in lines[k]), round(mean(c for _, c in lines[k]), 1)) for k in order]
    confs = [c for k in order for _, c in lines[k]]
    return OcrPage(result, round(mean(confs), 1) if confs else None)


class TesseractOcr:
    name = "tesseract"

    def __init__(self, cmd: str, tessdata: str, languages: str, dpi: int, timeout: int) -> None:
        self.cmd = cmd or "tesseract"
        self.tessdata, self.languages, self.dpi, self.timeout = tessdata, languages, dpi, timeout
        self._available: bool | None = None

    def _env(self) -> dict[str, str]:
        env = dict(os.environ)
        if self.tessdata:
            env["TESSDATA_PREFIX"] = self.tessdata
        env["OMP_THREAD_LIMIT"] = "1"  # predictable CPU use per worker
        return env

    def available(self) -> bool:
        if self._available is None:
            try:
                out = subprocess.run([self.cmd, "--list-langs"], capture_output=True, text=True, timeout=15, env=self._env(), check=False)
                have = {ln.strip() for ln in out.stdout.splitlines()[1:]}
                self._available = out.returncode == 0 and set(self.languages.split("+")) <= have
            except (OSError, subprocess.SubprocessError):
                self._available = False
        return self._available

    def recognise(self, image_png: bytes) -> OcrPage:
        if not self.available():
            raise ProcessingError("ocr_unavailable")
        try:
            proc = subprocess.run(
                [self.cmd, "stdin", "stdout", "-l", self.languages, "--dpi", str(self.dpi), "tsv"],
                input=image_png, capture_output=True, timeout=self.timeout, env=self._env(), check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise ProcessingError("ocr_timeout", f"tesseract exceeded {self.timeout}s") from exc
        except OSError as exc:
            raise ProcessingError("ocr_unavailable", type(exc).__name__) from exc
        if proc.returncode != 0:
            raise ProcessingError("ocr_failed", f"tesseract exit {proc.returncode}: {proc.stderr[:200].decode('utf-8', 'replace')}")
        return parse_tsv(proc.stdout.decode("utf-8", "replace"))


def get_ocr_engine(settings: Settings) -> OcrEngine:
    if settings.ocr_engine == "tesseract":
        return TesseractOcr(settings.tesseract_cmd, settings.tessdata_prefix, settings.ocr_languages, settings.ocr_dpi, settings.ocr_timeout_seconds)
    return NullOcr()
