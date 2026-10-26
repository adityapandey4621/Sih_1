import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, EmailStr, Field, StringConstraints, field_validator

from app.rbac import Role
from app.security import PASSWORD_MAX_LENGTH, PASSWORD_MIN_LENGTH

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=200)]


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


def _check_password(v: str) -> str:
    if len(v) < PASSWORD_MIN_LENGTH:
        raise ValueError(f"Password must be at least {PASSWORD_MIN_LENGTH} characters.")
    if len(v) > PASSWORD_MAX_LENGTH:
        raise ValueError(f"Password must be at most {PASSWORD_MAX_LENGTH} characters.")
    return v


# ---- auth -------------------------------------------------------------------------------------


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=PASSWORD_MAX_LENGTH)


class InstitutionOut(ORMModel):
    id: uuid.UUID
    name: str


class MeOut(BaseModel):
    id: uuid.UUID
    email: str
    full_name: str
    role: str
    role_label: str
    institution: InstitutionOut | None
    permissions: list[str]
    session_expires_at: datetime


class CsrfOut(BaseModel):
    csrf_token: str


class InvitationPreviewIn(BaseModel):
    token: str = Field(min_length=10, max_length=200)


class InvitationPreviewOut(BaseModel):
    email: str
    full_name: str
    role_label: str
    institution_name: str | None


class InvitationAcceptIn(BaseModel):
    token: str = Field(min_length=10, max_length=200)
    password: str

    @field_validator("password")
    @classmethod
    def _pw(cls, v: str) -> str:
        return _check_password(v)


# ---- access requests --------------------------------------------------------------------------


class AccessRequestIn(BaseModel):
    institution_name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=300)]
    institution_identifier: Annotated[str, StringConstraints(strip_whitespace=True, max_length=100)] | None = None
    applicant_name: Name
    applicant_email: EmailStr
    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=20, max_length=2000)]

    @field_validator("institution_identifier")
    @classmethod
    def _blank_to_none(cls, v: str | None) -> str | None:
        return v or None


class AccessRequestReceivedOut(BaseModel):
    status: str = "received"
    message: str = (
        "Your request has been received and is awaiting review by an administrator. "
        "No account has been created yet."
    )


class AccessRequestOut(ORMModel):
    id: uuid.UUID
    institution_name: str
    institution_identifier: str | None
    applicant_name: str
    applicant_email: str
    reason: str
    status: str
    created_at: datetime
    reviewed_at: datetime | None
    review_note: str | None


class AccessRequestRejectIn(BaseModel):
    note: Annotated[str, StringConstraints(strip_whitespace=True, max_length=1000)] | None = None


# ---- admin ------------------------------------------------------------------------------------


class UserOut(BaseModel):
    id: uuid.UUID
    email: str
    full_name: str
    role: str
    role_label: str
    institution_name: str | None
    is_active: bool
    last_login_at: datetime | None


class InvitationCreateIn(BaseModel):
    email: EmailStr
    full_name: Name
    role: Role
    institution_name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=300)] | None = None


class InvitationOut(BaseModel):
    id: uuid.UUID
    email: str
    full_name: str
    role: str
    role_label: str
    institution_name: str | None
    created_at: datetime
    expires_at: datetime
    status: str  # pending | accepted | revoked | expired


class InvitationIssuedOut(BaseModel):
    invitation: InvitationOut
    # Shown exactly once. No email is sent in Phase 1; the administrator must pass it on securely.
    invitation_path: str
    email_delivery: str = "not_configured"


class AuditEventOut(ORMModel):
    id: uuid.UUID
    occurred_at: datetime
    action: str
    actor_label: str | None
    target_type: str | None
    target_id: str | None
    ip_address: str | None
    detail: dict | None


class AuditPage(BaseModel):
    items: list[AuditEventOut]
    total: int
