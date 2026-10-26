"""Audit trail writer. Never pass passwords, tokens or invitation links in `detail`."""

import uuid

from fastapi import Request
from sqlalchemy.orm import Session

from app.models import AuditEvent, User


def client_ip(request: Request) -> str | None:
    # Phase 1 runs without a trusted proxy chain; X-Forwarded-For is deliberately ignored
    # because it is client-controllable. Configure a trusted-proxy setting before relying on it.
    return request.client.host if request.client else None


def record(
    db: Session,
    action: str,
    *,
    request: Request | None = None,
    actor: User | None = None,
    actor_label: str | None = None,
    target_type: str | None = None,
    target_id: uuid.UUID | str | None = None,
    detail: dict | None = None,
) -> None:
    db.add(
        AuditEvent(
            action=action,
            actor_user_id=actor.id if actor else None,
            actor_label=actor.email if actor else actor_label,
            target_type=target_type,
            target_id=str(target_id) if target_id else None,
            ip_address=client_ip(request) if request else None,
            detail=detail,
        )
    )
