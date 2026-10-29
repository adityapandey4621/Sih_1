from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app import audit
from app.config import Settings, get_settings
from app.db import get_db
from app.deps import CurrentUser, csrf_protect, get_current_user, get_optional_user
from app.email_service import get_email_sender
from app.models import AuthSession, Institution, Invitation, LoginAttempt, User, utcnow
from app.rbac import ROLE_LABELS, Role, permissions_for
from app.schemas import (
    CsrfOut,
    InstitutionOut,
    InvitationAcceptIn,
    InvitationPreviewIn,
    InvitationPreviewOut,
    LoginIn,
    MeOut,
)
from app.security import (
    as_utc,
    hash_password,
    hash_token,
    new_token,
    normalise_email,
    password_needs_rehash,
    verify_password,
)

router = APIRouter(prefix="/api/auth", tags=["auth"], dependencies=[Depends(csrf_protect)])

INVALID_CREDENTIALS = "Incorrect email or password."
INVALID_INVITATION = "This invitation link is invalid, has expired or was already used."


def _set_csrf_cookie(response: Response, settings: Settings, token: str) -> None:
    response.set_cookie(
        settings.csrf_cookie_name,
        token,
        httponly=False,  # must be readable by the SPA to echo in X-CSRF-Token
        secure=settings.is_production,
        samesite="lax",
        path="/",
    )


def _me(current: CurrentUser) -> MeOut:
    u = current.user
    return MeOut(
        id=u.id,
        email=u.email,
        full_name=u.full_name,
        role=u.role,
        role_label=ROLE_LABELS.get(Role(u.role), u.role),
        institution=InstitutionOut.model_validate(u.institution) if u.institution else None,
        permissions=sorted(p.value for p in current.permissions),
        session_expires_at=current.session.expires_at,
    )


@router.get("/csrf", response_model=CsrfOut)
def csrf(request: Request, response: Response, settings: Settings = Depends(get_settings)) -> CsrfOut:
    token = request.cookies.get(settings.csrf_cookie_name) or new_token()
    _set_csrf_cookie(response, settings, token)
    response.headers["Cache-Control"] = "no-store"
    return CsrfOut(csrf_token=token)


@router.get("/me", response_model=MeOut)
def me(response: Response, current: CurrentUser = Depends(get_current_user)) -> MeOut:
    response.headers["Cache-Control"] = "no-store"
    return _me(current)


@router.get("/session", response_model=MeOut | None)
def session_probe(response: Response, current: CurrentUser | None = Depends(get_optional_user)) -> MeOut | None:
    """Like /me, but answers 200 with null when signed out, so public pages can check quietly."""
    response.headers["Cache-Control"] = "no-store"
    return _me(current) if current else None


def _throttled(db: Session, settings: Settings, email: str, ip: str | None) -> bool:
    since = utcnow() - timedelta(minutes=settings.login_window_minutes)
    by_email = db.scalar(
        select(func.count()).select_from(LoginAttempt).where(LoginAttempt.email == email, LoginAttempt.attempted_at >= since)
    )
    if (by_email or 0) >= settings.login_max_failures:
        return True
    if ip:
        by_ip = db.scalar(
            select(func.count()).select_from(LoginAttempt).where(LoginAttempt.ip_address == ip, LoginAttempt.attempted_at >= since)
        )
        return (by_ip or 0) >= settings.login_ip_max_failures
    return False


@router.post("/login", response_model=MeOut)
def login(
    body: LoginIn,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> MeOut:
    email = normalise_email(body.email)
    ip = audit.client_ip(request)

    if _throttled(db, settings, email, ip):
        audit.record(db, "auth.login_throttled", request=request, actor_label=email)
        db.commit()
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Too many sign-in attempts. Please wait a few minutes and try again.",
            headers={"Retry-After": str(settings.login_window_minutes * 60)},
        )

    user = db.scalar(select(User).where(User.email == email))
    ok = verify_password(body.password, user.password_hash if user else None)
    if not (user and ok and user.is_active):
        db.add(LoginAttempt(email=email, ip_address=ip))
        audit.record(db, "auth.login_failed", request=request, actor_label=email)
        db.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, INVALID_CREDENTIALS)

    # Fresh session token on every sign-in (prevents session fixation).
    old = request.cookies.get(settings.session_cookie_name)
    if old:
        stale = db.scalar(select(AuthSession).where(AuthSession.token_hash == hash_token(old)))
        if stale and stale.revoked_at is None:
            stale.revoked_at = utcnow()

    if password_needs_rehash(user.password_hash):
        user.password_hash = hash_password(body.password)
    db.execute(delete(LoginAttempt).where(LoginAttempt.email == email))
    # Opportunistic pruning of old rows.
    db.execute(delete(LoginAttempt).where(LoginAttempt.attempted_at < utcnow() - timedelta(days=1)))

    token = new_token()
    now = utcnow()
    session = AuthSession(
        token_hash=hash_token(token),
        user_id=user.id,
        expires_at=now + timedelta(hours=settings.session_absolute_hours),
        ip_address=ip,
        user_agent=(request.headers.get("user-agent") or "")[:300] or None,
    )
    db.add(session)
    user.last_login_at = now
    audit.record(db, "auth.login", request=request, actor=user)
    db.commit()

    response.set_cookie(
        settings.session_cookie_name,
        token,
        max_age=settings.session_absolute_hours * 3600,
        httponly=True,
        secure=settings.is_production,
        samesite="lax",
        path="/",
    )
    _set_csrf_cookie(response, settings, new_token())
    response.headers["Cache-Control"] = "no-store"
    return _me(CurrentUser(user, session, permissions_for(user.role)))


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Response:
    token = request.cookies.get(settings.session_cookie_name)
    if token:
        sess = db.scalar(select(AuthSession).where(AuthSession.token_hash == hash_token(token)))
        if sess and sess.revoked_at is None:
            sess.revoked_at = utcnow()
            audit.record(db, "auth.logout", request=request, actor=sess.user)
            db.commit()
    response.delete_cookie(settings.session_cookie_name, path="/")
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.post("/password-reset", status_code=status.HTTP_503_SERVICE_UNAVAILABLE)
def password_reset_unavailable() -> None:
    """Truthful placeholder: self-service reset needs an email provider, which is not configured."""
    if not get_email_sender().is_configured:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Password reset by email is not available because no email provider is configured. "
            "Please contact your platform administrator.",
        )


def _valid_invitation(db: Session, token: str) -> Invitation | None:
    inv = db.scalar(select(Invitation).where(Invitation.token_hash == hash_token(token)))
    if inv is None or inv.accepted_at or inv.revoked_at or as_utc(inv.expires_at) <= utcnow():
        return None
    return inv


@router.post("/invitations/preview", response_model=InvitationPreviewOut)
def invitation_preview(body: InvitationPreviewIn, db: Session = Depends(get_db)) -> InvitationPreviewOut:
    inv = _valid_invitation(db, body.token)
    if inv is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, INVALID_INVITATION)
    inst = db.get(Institution, inv.institution_id) if inv.institution_id else None
    return InvitationPreviewOut(
        email=inv.email,
        full_name=inv.full_name,
        role_label=ROLE_LABELS[Role(inv.role)],
        institution_name=inst.name if inst else None,
    )


@router.post("/invitations/accept", status_code=status.HTTP_201_CREATED)
def invitation_accept(body: InvitationAcceptIn, request: Request, db: Session = Depends(get_db)) -> dict:
    inv = _valid_invitation(db, body.token)
    if inv is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, INVALID_INVITATION)
    if db.scalar(select(User).where(User.email == inv.email)):
        raise HTTPException(status.HTTP_409_CONFLICT, INVALID_INVITATION)
    user = User(
        email=inv.email,
        full_name=inv.full_name,
        password_hash=hash_password(body.password),
        role=inv.role,
        institution_id=inv.institution_id,
    )
    db.add(user)
    db.flush()
    inv.accepted_at = utcnow()
    audit.record(db, "invitation.accepted", request=request, actor=user, target_type="invitation", target_id=inv.id)
    db.commit()
    return {"status": "account_created"}
