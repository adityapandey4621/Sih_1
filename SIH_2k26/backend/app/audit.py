"""Audit trail writer. Never pass passwords, tokens or invitation links in `detail`."""

import uuid

from fastapi import Request
from sqlalchemy.orm import Session

from app.models import AuditEvent, User
from app.netutil import resolve_client_ip


def client_ip(request: Request) -> str | None:
    # X-Forwarded-For is client-controllable, so it is used only behind TRUSTED_PROXY_COUNT proxies (see app/netutil.py).
    from app.config import get_settings

    return resolve_client_ip(request.client.host if request.client else None, request.headers.get("x-forwarded-for"), get_settings().trusted_proxy_count)


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
