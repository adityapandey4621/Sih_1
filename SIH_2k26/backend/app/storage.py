"""Private attachment storage.

Files are addressed only by generated keys (32 hex chars). User-supplied names are metadata
and never reach the filesystem. Nothing here is ever served statically; downloads go through
an authenticated, access-checked endpoint.
"""

import hashlib
import os
import re
import secrets
from collections.abc import Iterable
from pathlib import Path

_KEY_RE = re.compile(r"^[0-9a-f]{32}$")


class StorageError(RuntimeError):
    pass


class LocalStorage:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def new_key() -> str:
        return secrets.token_hex(16)

    def path_for(self, key: str) -> Path:
        if not _KEY_RE.fullmatch(key):
            raise StorageError("Invalid storage key.")
        path = (self.root / key[:2] / key).resolve()
        if not path.is_relative_to(self.root):  # defence in depth; unreachable with a validated key
            raise StorageError("Storage path escapes the storage root.")
        return path

    def save(self, key: str, chunks: Iterable[bytes]) -> tuple[int, str]:
        """Write atomically (temp file, then rename). Returns (size, sha256). Cleans up on any failure."""
        path = self.path_for(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{key}.part")
        digest, size = hashlib.sha256(), 0
        try:
            with open(tmp, "xb") as fh:  # 'x': never overwrite an existing file
                for chunk in chunks:
                    fh.write(chunk)
                    digest.update(chunk)
                    size += len(chunk)
            if path.exists():
                raise StorageError("Storage key collision.")
            os.replace(tmp, path)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        return size, digest.hexdigest()

    def open(self, key: str):
        return open(self.path_for(key), "rb")

    def delete(self, key: str) -> None:
        self.path_for(key).unlink(missing_ok=True)


def get_storage() -> LocalStorage:
    from app.config import get_settings

    return LocalStorage(get_settings().storage_dir)
