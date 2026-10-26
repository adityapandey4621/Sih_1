import uuid
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app import audit
from app.config import Settings, get_settings
from app.db import get_db
from app.deps import CurrentUser, csrf_protect, require_permission
from app.models import (
    AccessRequest,
    AuditEvent,
    AuthSession,
    Institution,
    Invitation,
    User,
    utcnow,
)
from app.rbac import ROLE_LABELS, Permission, Role
from app.schemas import (
    AccessRequestOut,
    AccessRequestRejectIn,
    AuditEventOut,
    AuditPage,
    InvitationCreateIn,
    InvitationIssuedOut,
    InvitationOut,
    UserOut,
)
from app.security import as_utc, hash_token, new_token, normalise_email

router = APIRouter(prefix="/api", tags=["admin"], dependencies=[Depends(csrf_protect)])

requires_requests = require_permission(Permission.ADMIN_ACCESS_REQUESTS)
requires_users = require_permission(Permission.ADMIN_USERS)
requires_audit = require_permission(Permission.AUDIT_READ)


def _invitation_out(db: Session, inv: Invitation) -> InvitationOut:
    inst = db.get(Institution, inv.institution_id) if inv.institution_id else None
    if inv.accepted_at:
        state = "accepted"
    elif inv.revoked_at:
        state = "revoked"
    elif as_utc(inv.expires_at) <= utcnow():
        state = "expired"
    else:
        state = "pending"
    return InvitationOut(
        id=inv.id,
        email=inv.email,
        full_name=inv.full_name,
        role=inv.role,
        role_label=ROLE_LABELS[Role(inv.role)],
        institution_name=inst.name if inst else None,
        created_at=inv.created_at,
        expires_at=inv.expires_at,
        status=state,
    )


def _issue_invitation(
    db: Session,
    settings: Settings,
    *,
    email: str,
    full_name: str,
    role: Role,
    institution: Institution | None,
    invited_by: User,
    access_request_id: uuid.UUID | None = None,
) -> tuple[Invitation, str]:
    email = normalise_email(email)
    if db.scalar(select(User).where(User.email == email)):
        raise HTTPException(status.HTTP_409_CONFLICT, "A user with this email already exists.")
    now = utcnow()
    # Only one live invitation per email: revoke earlier ones.
    db.execute(
        update(Invitation)
        .where(Invitation.email == email, Invitation.accepted_at.is_(None), Invitation.revoked_at.is_(None))
        .values(revoked_at=now)
    )
    token = new_token()
    inv = Invitation(
        token_hash=hash_token(token),
        email=email,
        full_name=full_name,
        role=role.value,
        institution_id=institution.id if institution else None,
        access_request_id=access_request_id,
        invited_by_id=invited_by.id,
        expires_at=now + timedelta(hours=settings.invitation_valid_hours),
    )
    db.add(inv)
    db.flush()
    return inv, f"/accept-invitation#token={token}"


# ---- access requests --------------------------------------------------------------------------


@router.get("/admin/access-requests", response_model=list[AccessRequestOut])
def list_access_requests(
    status_filter: str | None = Query(None, alias="status", pattern="^(pending|approved|rejected)$"),
    _: CurrentUser = Depends(requires_requests),
    db: Session = Depends(get_db),
) -> list[AccessRequest]:
    q = select(AccessRequest).order_by(AccessRequest.created_at.desc()).limit(200)
    if status_filter:
        q = q.where(AccessRequest.status == status_filter)
    return list(db.scalars(q))


def _pending_request(db: Session, request_id: uuid.UUID) -> AccessRequest:
    req = db.get(AccessRequest, request_id)
    if req is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Access request not found.")
    if req.status != "pending":
        raise HTTPException(status.HTTP_409_CONFLICT, "This request has already been reviewed.")
    return req


@router.post("/admin/access-requests/{request_id}/approve", response_model=InvitationIssuedOut)
def approve_access_request(
    request_id: uuid.UUID,
    request: Request,
    current: CurrentUser = Depends(requires_requests),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> InvitationIssuedOut:
    """Approval always yields an `institute_user` invitation; the role cannot be chosen here."""
    req = _pending_request(db, request_id)
    institution = Institution(name=req.institution_name, external_identifier=req.institution_identifier)
    db.add(institution)
    db.flush()
    inv, path = _issue_invitation(
        db,
        settings,
        email=req.applicant_email,
        full_name=req.applicant_name,
        role=Role.INSTITUTE_USER,
        institution=institution,
        invited_by=current.user,
        access_request_id=req.id,
    )
    req.status = "approved"
    req.reviewed_at = utcnow()
    req.reviewed_by_id = current.user.id
    audit.record(db, "access_request.approved", request=request, actor=current.user, target_type="access_request", target_id=req.id)
    db.commit()
    return InvitationIssuedOut(invitation=_invitation_out(db, inv), invitation_path=path)


@router.post("/admin/access-requests/{request_id}/reject", response_model=AccessRequestOut)
def reject_access_request(
    request_id: uuid.UUID,
    body: AccessRequestRejectIn,
    request: Request,
    current: CurrentUser = Depends(requires_requests),
    db: Session = Depends(get_db),
) -> AccessRequest:
    req = _pending_request(db, request_id)
    req.status = "rejected"
    req.reviewed_at = utcnow()
    req.reviewed_by_id = current.user.id
    req.review_note = body.note
    audit.record(db, "access_request.rejected", request=request, actor=current.user, target_type="access_request", target_id=req.id)
    db.commit()
    return req


# ---- users and invitations --------------------------------------------------------------------


@router.get("/admin/roles")
def list_roles(_: CurrentUser = Depends(requires_users)) -> list[dict[str, str]]:
    """Roles an administrator may invite. Served from rbac.py so the UI carries no role table."""
    return [{"value": r.value, "label": ROLE_LABELS[r]} for r in Role]


@router.get("/admin/users", response_model=list[UserOut])
def list_users(_: CurrentUser = Depends(requires_users), db: Session = Depends(get_db)) -> list[UserOut]:
    users = db.scalars(select(User).order_by(User.created_at.desc()).limit(500))
    return [
        UserOut(
            id=u.id,
            email=u.email,
            full_name=u.full_name,
            role=u.role,
            role_label=ROLE_LABELS.get(Role(u.role), u.role),
            institution_name=u.institution.name if u.institution else None,
            is_active=u.is_active,
            last_login_at=u.last_login_at,
        )
        for u in users
    ]


@router.post("/admin/users/{user_id}/deactivate", status_code=status.HTTP_204_NO_CONTENT)
def deactivate_user(
    user_id: uuid.UUID,
    request: Request,
    current: CurrentUser = Depends(requires_users),
    db: Session = Depends(get_db),
) -> None:
    if user_id == current.user.id:
        raise HTTPException(status.HTTP_409_CONFLICT, "You cannot deactivate your own account.")
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found.")
    user.is_active = False
    db.execute(update(AuthSession).where(AuthSession.user_id == user.id, AuthSession.revoked_at.is_(None)).values(revoked_at=utcnow()))
    audit.record(db, "user.deactivated", request=request, actor=current.user, target_type="user", target_id=user.id)
    db.commit()


@router.post("/admin/users/{user_id}/activate", status_code=status.HTTP_204_NO_CONTENT)
def activate_user(
    user_id: uuid.UUID,
    request: Request,
    current: CurrentUser = Depends(requires_users),
    db: Session = Depends(get_db),
) -> None:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found.")
    user.is_active = True
    audit.record(db, "user.activated", request=request, actor=current.user, target_type="user", target_id=user.id)
    db.commit()


@router.get("/admin/invitations", response_model=list[InvitationOut])
def list_invitations(_: CurrentUser = Depends(requires_users), db: Session = Depends(get_db)) -> list[InvitationOut]:
    invs = db.scalars(select(Invitation).order_by(Invitation.created_at.desc()).limit(200))
    return [_invitation_out(db, i) for i in invs]


@router.post("/admin/invitations", response_model=InvitationIssuedOut, status_code=status.HTTP_201_CREATED)
def create_invitation(
    body: InvitationCreateIn,
    request: Request,
    current: CurrentUser = Depends(requires_users),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> InvitationIssuedOut:
    institution: Institution | None = None
    if body.role is Role.INSTITUTE_USER:
        if not body.institution_name:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "An institution name is required for institution users.")
        institution = db.scalar(select(Institution).where(func.lower(Institution.name) == body.institution_name.lower()))
        if institution is None:
            institution = Institution(name=body.institution_name)
            db.add(institution)
            db.flush()
    inv, path = _issue_invitation(
        db, settings, email=body.email, full_name=body.full_name, role=body.role, institution=institution, invited_by=current.user
    )
    audit.record(
        db, "invitation.created", request=request, actor=current.user, target_type="invitation", target_id=inv.id,
        detail={"role": body.role.value},
    )
    db.commit()
    return InvitationIssuedOut(invitation=_invitation_out(db, inv), invitation_path=path)


@router.post("/admin/invitations/{invitation_id}/revoke", status_code=status.HTTP_204_NO_CONTENT)
def revoke_invitation(
    invitation_id: uuid.UUID,
    request: Request,
    current: CurrentUser = Depends(requires_users),
    db: Session = Depends(get_db),
) -> None:
    inv = db.get(Invitation, invitation_id)
    if inv is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Invitation not found.")
    if inv.accepted_at or inv.revoked_at:
        raise HTTPException(status.HTTP_409_CONFLICT, "This invitation is no longer active.")
    inv.revoked_at = utcnow()
    audit.record(db, "invitation.revoked", request=request, actor=current.user, target_type="invitation", target_id=inv.id)
    db.commit()


# ---- audit ------------------------------------------------------------------------------------


@router.get("/audit/events", response_model=AuditPage)
def list_audit_events(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    action: str | None = Query(None, max_length=80),
    _: CurrentUser = Depends(requires_audit),
    db: Session = Depends(get_db),
) -> AuditPage:
    base = select(AuditEvent)
    if action:
        base = base.where(AuditEvent.action == action)
    total = db.scalar(select(func.count()).select_from(base.subquery())) or 0
    items = db.scalars(base.order_by(AuditEvent.occurred_at.desc()).limit(limit).offset(offset))
    return AuditPage(items=[AuditEventOut.model_validate(i) for i in items], total=total)
