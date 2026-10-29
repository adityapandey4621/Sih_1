"""The state of the integration boundary, for staff. Shows what is configured, what has and has not been confirmed, and the call log. Never a secret or a body.

Nothing here claims the integration works with AICTE: the status says plainly that no AICTE interface is known to exist and what the mode means.
"""

import uuid

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import audit
from app.config import Settings, get_settings
from app.db import get_db
from app.deps import CurrentUser, csrf_protect, require_permission
from app.integration.aicte import factory
from app.integration.aicte.contract import CONTRACT_LABEL, CONTRACT_VERSION, json_schemas
from app.models_applications import Application
from app.models_decisions import ExternalSync
from app.models_integration import IntegrationLog
from app.rbac import Permission
from app.services import portal_sync
from app.services.errors import not_found

router = APIRouter(tags=["integration"], dependencies=[Depends(csrf_protect)])
can_read = require_permission(Permission.INTEGRATION_READ)
can_manage = require_permission(Permission.INTEGRATION_MANAGE)

MODE_WORDS = {
    "none": "No integration is configured. Nothing is sent to any external system, and every record says NOT_CONFIGURED.",
    "mock": "DEMO ONLY mock adapter. It stands in for an external system; it proves nothing about AICTE.",
    "http": "The HTTP adapter speaks this project's own contract to the configured URL. UNVERIFIED: it has not been tested against any authorised AICTE system.",
}


def _row(db: Session, r: ExternalSync) -> dict:
    app = db.get(Application, r.application_id)
    return {"id": r.id, "kind": r.kind, "status": r.status, "application_reference": app.reference if app else None, "attempts": r.attempts, "last_error": r.last_error, "last_outcome": r.last_outcome,
            "next_attempt_at": r.next_attempt_at, "last_attempt_at": r.last_attempt_at, "confirmed_at": r.confirmed_at, "external_ref": r.external_ref, "adapter": r.adapter, "created_at": r.created_at}


@router.get("/api/integration/status")
def status(current: CurrentUser = Depends(can_read), db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> dict:
    adapter = factory.get_adapter(settings)
    counts = dict(db.execute(select(ExternalSync.status, func.count()).group_by(ExternalSync.status)).all())
    recent = db.scalars(select(ExternalSync).order_by(ExternalSync.created_at.desc()).limit(50)).all()
    logs = db.scalars(select(IntegrationLog).order_by(IntegrationLog.at.desc()).limit(30)).all()
    return {
        "mode": settings.aicte_integration_mode, "mode_note": MODE_WORDS.get(settings.aicte_integration_mode, "Unknown mode."), "adapter": adapter.describe() if adapter else None,
        "verified": False, "verification_note": "No AICTE API, credential or specification has been supplied to this project. Real AICTE integration remains UNVERIFIED until an authorised connection has actually been tested.",
        "contract": {"version": CONTRACT_VERSION, "label": CONTRACT_LABEL}, "counts": counts, "recent": [_row(db, r) for r in recent],
        "log": [{"at": g.at, "sync_id": g.sync_id, "adapter": g.adapter, "operation": g.operation, "attempt_no": g.attempt_no, "outcome": g.outcome, "http_status": g.http_status, "latency_ms": g.latency_ms} for g in logs],
        "limits": {"max_attempts": settings.sync_max_attempts, "backoff_seconds": settings.sync_retry_backoff_seconds},
    }


@router.get("/api/integration/contract")
def contract(current: CurrentUser = Depends(can_read)) -> dict:
    """The message contract this project would send and expect back. For whoever would build or review the receiving side. A project-defined contract, not an AICTE specification."""
    return json_schemas()


@router.post("/api/integration/syncs/{sync_id}/retry")
def retry(sync_id: uuid.UUID, request: Request, current: CurrentUser = Depends(can_manage), db: Session = Depends(get_db)) -> dict:
    """An operator's decision: a FAILED or RECONCILIATION_REQUIRED record gets a fresh bounded set of attempts, and one is made now."""
    row = db.get(ExternalSync, sync_id)
    if row is None:
        raise not_found("Sync record")
    audit.record(db, "sync.retry_requested", request=request, actor=current.user, target_type=row.subject_type, target_id=row.subject_id, detail={"from": row.status})
    portal_sync.retry_now(db, row)
    db.commit()
    return _row(db, row)
