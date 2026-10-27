"""Store one uploaded file in the private storage (PDF, PNG, JPEG; size-limited; generated key). Used for official-outcome files, appeal documents,
compliance evidence and renewal uploads.

Malware scanning follows the same rule as application documents: with a scanner configured, an infected file is refused and deleted; with none configured
the file's `scan_status` is NOT_SCANNED - never "clean" - and in production (or when REQUIRE_MALWARE_SCAN is set) it is refused outright, because a file nobody
can scan must not be accepted as evidence. These files are not text-processed (the Phase 3 pipeline serves application documents only).
"""

import re
from collections.abc import Iterator
from dataclasses import dataclass

from fastapi import UploadFile

from app.config import Settings
from app.domain import ScanStatus
from app.processing.documents import sniff
from app.processing.errors import ProcessingError
from app.processing.scanner import get_scanner
from app.services.errors import ServiceError, invalid
from app.storage import LocalStorage, StorageError

_CHUNK = 1024 * 1024


@dataclass
class StoredFile:
    key: str
    name: str
    media_type: str
    size: int
    sha256: str
    scan_status: str = "NOT_SCANNED"


def _read_limited(upload: UploadFile, limit: int) -> Iterator[bytes]:
    total = 0
    while chunk := upload.file.read(_CHUNK):
        total += len(chunk)
        if total > limit:
            raise ServiceError(413, "file_too_large", f"The file is larger than the {limit // (1024 * 1024)} MB limit.")
        yield chunk


def _scan(key: str, settings: Settings, storage: LocalStorage) -> str:
    scanner = get_scanner(settings)
    if scanner.name == "none":
        if settings.malware_scan_required:
            storage.delete(key)
            raise ServiceError(503, "scanner_required", "Files cannot be accepted right now because malware scanning is required and no scanner is available. Nothing was saved.")
        return "NOT_SCANNED"
    with storage.open(key) as fh:
        data = fh.read()
    try:
        res = scanner.scan(data)
    except ProcessingError as exc:
        storage.delete(key)
        raise ServiceError(503, "scanner_unavailable", "The file could not be scanned just now. Nothing was saved; please try again.") from exc
    if res.status == ScanStatus.INFECTED:
        storage.delete(key)
        raise ServiceError(422, "file_rejected", "The file was rejected by the malware scan. Nothing was saved.")
    return res.status.value


def store(upload: UploadFile, settings: Settings, storage: LocalStorage) -> StoredFile:
    name = re.sub(r"[\x00-\x1f\x7f]", "", re.split(r"[\\/]", upload.filename or "")[-1]).strip().strip(".")[:255] or "file"
    head = upload.file.read(16)
    upload.file.seek(0)
    kind = sniff(head)
    if kind is None:
        raise ServiceError(415, "type_not_allowed", "Only PDF, PNG and JPEG files are accepted, and the file's content must match its type.")
    media, exts = kind
    ext = "." + name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if ext not in exts:
        raise ServiceError(415, "type_mismatch", "The file's name and content do not agree. Upload the original PDF, PNG or JPEG file.")
    key = storage.new_key()
    try:
        size, digest = storage.save(key, _read_limited(upload, settings.max_upload_bytes))
    except ServiceError:
        raise
    except (StorageError, OSError) as exc:
        raise ServiceError(503, "storage_unavailable", "The file could not be stored. Nothing was saved; please try again.") from exc
    if size == 0:
        storage.delete(key)
        raise invalid("The file is empty.")
    return StoredFile(key, name, media, size, digest, _scan(key, settings, storage))
