"""Readiness probe: can this instance do its job right now? Booleans only - no connection strings, paths or error text leave the process."""

import logging
from pathlib import Path

from sqlalchemy import text

from app.config import get_settings
from app.db import SessionLocal

log = logging.getLogger("app.ready")


def _head_revision() -> str | None:
    try:
        from alembic.config import Config
        from alembic.script import ScriptDirectory

        cfg = Config(str(Path(__file__).resolve().parent.parent / "alembic.ini"))
        cfg.set_main_option("script_location", str(Path(__file__).resolve().parent.parent / "alembic"))
        return ScriptDirectory.from_config(cfg).get_current_head()
    except Exception:  # noqa: BLE001 - an unreadable migration directory is reported as "unknown", not as a crash
        return None


def readiness() -> dict:
    checks: dict[str, bool | str] = {"database": False, "schema_current": "unknown", "storage_writable": False}
    try:
        with SessionLocal() as db:
            db.execute(text("SELECT 1"))
            checks["database"] = True
            try:
                current = db.execute(text("SELECT version_num FROM alembic_version")).scalar()
                head = _head_revision()
                checks["schema_current"] = "unknown" if head is None else bool(current == head)
            except Exception:  # noqa: BLE001 - a database built without Alembic (the test suite) has no version table
                db.rollback()
                checks["schema_current"] = "unknown"
    except Exception as exc:  # noqa: BLE001
        log.error("readiness: database check failed: %s", type(exc).__name__)
    try:
        root = Path(get_settings().storage_dir)
        root.mkdir(parents=True, exist_ok=True)
        probe = root / ".ready-probe"
        probe.write_bytes(b"ok")
        probe.unlink()
        checks["storage_writable"] = True
    except OSError:
        pass
    ok = checks["database"] is True and checks["storage_writable"] is True and checks["schema_current"] is not False
    return {"status": "ready" if ok else "not_ready", "checks": checks}
