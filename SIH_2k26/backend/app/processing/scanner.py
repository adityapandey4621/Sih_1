"""Malware scanning boundary.

If no scanner is configured the result is SCANNER_UNAVAILABLE: the file was NOT scanned, and
nothing may present it as clean. `ClamdScanner` speaks clamd's INSTREAM protocol over TCP.
"""

import socket
import struct
from dataclasses import dataclass
from typing import Protocol

from app.config import Settings
from app.domain import ScanStatus
from app.processing.errors import ProcessingError


@dataclass(frozen=True)
class ScanResult:
    status: ScanStatus
    detail: str | None = None  # signature name when infected; scanner name otherwise


class Scanner(Protocol):
    name: str

    def scan(self, data: bytes) -> ScanResult: ...


class NoScanner:
    name = "none"

    def scan(self, data: bytes) -> ScanResult:  # noqa: ARG002
        return ScanResult(ScanStatus.SCANNER_UNAVAILABLE, "no scanner configured")


class ClamdScanner:
    name = "clamd"
    _CHUNK = 64 * 1024

    def __init__(self, host: str, port: int, timeout: float) -> None:
        self.host, self.port, self.timeout = host, port, timeout

    def scan(self, data: bytes) -> ScanResult:
        try:
            with socket.create_connection((self.host, self.port), timeout=self.timeout) as s:
                s.settimeout(self.timeout)
                s.sendall(b"zINSTREAM\0")
                for i in range(0, len(data), self._CHUNK):
                    chunk = data[i : i + self._CHUNK]
                    s.sendall(struct.pack("!I", len(chunk)) + chunk)
                s.sendall(struct.pack("!I", 0))
                reply = b""
                while not reply.endswith(b"\0") and len(reply) < 4096:
                    part = s.recv(1024)
                    if not part:
                        break
                    reply += part
        except OSError as exc:
            raise ProcessingError("scanner_error", f"clamd connection failed: {type(exc).__name__}") from exc
        text = reply.rstrip(b"\0\n").decode("utf-8", "replace")
        if text.endswith("OK"):
            return ScanResult(ScanStatus.CLEAN, "clamd")
        if text.endswith("FOUND"):
            signature = text.removeprefix("stream:").removesuffix("FOUND").strip()
            return ScanResult(ScanStatus.INFECTED, signature[:100] or "signature")
        raise ProcessingError("scanner_error", f"unexpected clamd reply: {text[:80]}")


def get_scanner(settings: Settings) -> Scanner:
    if settings.scanner == "clamd":
        return ClamdScanner(settings.clamd_host, settings.clamd_port, settings.clamd_timeout_seconds)
    return NoScanner()
