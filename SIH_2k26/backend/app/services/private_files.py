"""Store one uploaded file in the private storage (PDF, PNG, JPEG; size-limited; generated key). Used for official-outcome files and appeal documents.

These files are NOT malware-scanned or text-processed (the Phase 3 pipeline serves application documents only), and callers say so.
"""

import re
from collections.abc import Iterator
from dataclasses import dataclass

from fastapi import UploadFile

from app.config import Settings
from app.processing.documents import sniff
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


def _read_limited(upload: UploadFile, limit: int) -> Iterator[bytes]:
    total = 0
    while chunk := upload.file.read(_CHUNK):
        total += len(chunk)
        if total > limit:
            raise ServiceError(413, "file_too_large", f"The file is larger than the {limit // (1024 * 1024)} MB limit.")
        yield chunk


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
    return StoredFile(key, name, media, size, digest)
