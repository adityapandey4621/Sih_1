from dataclasses import dataclass
from datetime import timedelta

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit
from app.config import Settings, get_settings
from app.db import get_db
from app.models import AuthSession, User, utcnow
from app.rbac import Permission, permissions_for
from app.security import as_utc, hash_token, tokens_match

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


@dataclass
class CurrentUser:
    user: User
    session: AuthSession
    permissions: frozenset[Permission]


def csrf_protect(request: Request, settings: Settings = Depends(get_settings)) -> None:
    """Double-submit CSRF check, applied to every non-safe method (router-level dependency)."""
    if request.method in SAFE_METHODS:
        return
    origin = request.headers.get("origin")
    if origin and origin not in settings.allowed_origins:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Request origin not allowed.")
    cookie = request.cookies.get(settings.csrf_cookie_name)
    header = request.headers.get("x-csrf-token")
    if not cookie or not header or not tokens_match(cookie, header):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "CSRF validation failed.")


def _load_session(request: Request, db: Session, settings: Settings) -> AuthSession | None:
    token = request.cookies.get(settings.session_cookie_name)
    if not token:
        return None
    sess = db.scalar(select(AuthSession).where(AuthSession.token_hash == hash_token(token)))
    if sess is None or sess.revoked_at is not None:
        return None
    now = utcnow()
    idle_limit = as_utc(sess.last_seen_at) + timedelta(minutes=settings.session_idle_minutes)
    if as_utc(sess.expires_at) <= now or idle_limit <= now:
        sess.revoked_at = now
        db.commit()
        return None
    return sess


def get_optional_user(
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> CurrentUser | None:
    sess = _load_session(request, db, settings)
    if sess is None or not sess.user.is_active:
        return None
    now = utcnow()
    # Touch at most once a minute to avoid a write on every request.
    if now - as_utc(sess.last_seen_at) > timedelta(minutes=1):
        sess.last_seen_at = now
        db.commit()
    return CurrentUser(sess.user, sess, permissions_for(sess.user.role))


def get_current_user(current: CurrentUser | None = Depends(get_optional_user)) -> CurrentUser:
    if current is None:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Not signed in or the session has expired.",
            headers={"WWW-Authenticate": "Session"},
        )
    return current


def _deny(request: Request, db: Session, current: CurrentUser, needed: str) -> None:
    """Record the refusal (who, what, where), then answer 403. Never includes request bodies."""
    audit.record(
        db, "security.permission_denied", request=request, actor=current.user,
        detail={"required": needed, "method": request.method, "path": request.url.path},
    )
    db.commit()
    raise HTTPException(status.HTTP_403_FORBIDDEN, "You do not have access to this resource.")


def require_permission(permission: Permission):
    def checker(request: Request, current: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)) -> CurrentUser:
        if permission not in current.permissions:
            _deny(request, db, current, permission.value)
        return current

    return checker


def require_any_permission(*permissions: Permission):
    def checker(request: Request, current: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)) -> CurrentUser:
        if not any(p in current.permissions for p in permissions):
            _deny(request, db, current, "|".join(p.value for p in permissions))
        return current

    return checker
