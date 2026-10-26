from datetime import timedelta

from sqlalchemy import select

from app.db import SessionLocal
from app.models import AccessRequest, Invitation, User, utcnow
from app.rbac import Role
from tests.conftest import Api

VALID = {
    "institution_name": "Example College of Engineering",
    "institution_identifier": "",
    "applicant_name": "A. Applicant",
    "applicant_email": "Applicant@College.Example.org",
    "reason": "We would like to submit our institution's applications through this system.",
}


def token_from(path: str) -> str:
    return path.split("#token=")[1]


def test_access_request_is_persisted_as_pending_and_creates_no_user(api):
    r = api.post("/api/access-requests", VALID)
    assert r.status_code == 201
    assert r.json()["status"] == "received"
    assert "No account has been created" in r.json()["message"]
    with SessionLocal() as db:
        req = db.scalar(select(AccessRequest))
        assert req.status == "pending"
        assert req.applicant_email == "applicant@college.example.org"
        assert req.institution_identifier is None
        assert db.scalar(select(User)) is None


def test_access_request_cannot_carry_a_role(api):
    api.post("/api/access-requests", {**VALID, "role": "executive_committee"})
    with SessionLocal() as db:
        assert db.scalar(select(User)) is None


def test_access_request_validation(api):
    assert api.post("/api/access-requests", {**VALID, "applicant_email": "nope"}).status_code == 422
    assert api.post("/api/access-requests", {**VALID, "reason": "too short"}).status_code == 422
    assert api.post("/api/access-requests", {**VALID, "institution_name": " "}).status_code == 422


def test_access_request_requires_csrf(api):
    assert api.http.post("/api/access-requests", json=VALID).status_code == 403


def test_pending_requests_per_email_are_capped_without_revealing_it(api):
    for _ in range(5):
        assert api.post("/api/access-requests", VALID).status_code == 201
    with SessionLocal() as db:
        assert len(list(db.scalars(select(AccessRequest)))) == 3


def test_full_flow_request_approve_accept_login(api, make):
    make(Role.PLATFORM_ADMIN, "admin@test.example.org")
    api.post("/api/access-requests", VALID)

    admin = Api()
    admin.login("admin@test.example.org")
    pending = admin.get("/api/admin/access-requests?status=pending").json()
    assert len(pending) == 1
    issued = admin.post(f"/api/admin/access-requests/{pending[0]['id']}/approve").json()
    assert issued["invitation"]["role"] == "institute_user"
    assert issued["email_delivery"] == "not_configured"
    token = token_from(issued["invitation_path"])

    applicant = Api()
    preview = applicant.post("/api/auth/invitations/preview", {"token": token}).json()
    assert preview["institution_name"] == "Example College of Engineering"
    assert applicant.post("/api/auth/invitations/accept", {"token": token, "password": "short"}).status_code == 422
    assert applicant.post("/api/auth/invitations/accept", {"token": token, "password": "a-very-long-passphrase"}).status_code == 201
    me = applicant.login("applicant@college.example.org", "a-very-long-passphrase").json()
    assert me["role"] == "institute_user"
    assert me["institution"]["name"] == "Example College of Engineering"
    assert me["permissions"] == ["application:create", "application:read_own", "application:submit", "application:update_own", "dashboard:institute", "document:retry"]

    # single use
    assert applicant.post("/api/auth/invitations/accept", {"token": token, "password": "another-long-passphrase"}).status_code == 404
    # request can't be approved twice
    assert admin.post(f"/api/admin/access-requests/{pending[0]['id']}/approve").status_code == 409


def test_reject_access_request(api, make):
    make(Role.PLATFORM_ADMIN, "admin@test.example.org")
    api.post("/api/access-requests", VALID)
    admin = Api()
    admin.login("admin@test.example.org")
    rid = admin.get("/api/admin/access-requests").json()[0]["id"]
    r = admin.post(f"/api/admin/access-requests/{rid}/reject", {"note": "Could not verify the institution."})
    assert r.json()["status"] == "rejected"
    with SessionLocal() as db:
        assert db.scalar(select(Invitation)) is None


def test_admin_invites_official_and_invitation_expires_and_can_be_revoked(api, make):
    make(Role.PLATFORM_ADMIN, "admin@test.example.org")
    admin = Api()
    admin.login("admin@test.example.org")
    issued = admin.post(
        "/api/admin/invitations",
        {"email": "officer@test.example.org", "full_name": "Scrutiny Person", "role": "scrutiny_officer"},
    ).json()
    token = token_from(issued["invitation_path"])
    with SessionLocal() as db:
        inv = db.scalar(select(Invitation))
        assert token not in inv.token_hash and len(inv.token_hash) == 64  # only a hash is stored
        inv.expires_at = utcnow() - timedelta(minutes=1)
        db.commit()
    assert api.post("/api/auth/invitations/accept", {"token": token, "password": "a-very-long-passphrase"}).status_code == 404

    again = admin.post(
        "/api/admin/invitations",
        {"email": "officer@test.example.org", "full_name": "Scrutiny Person", "role": "scrutiny_officer"},
    ).json()
    admin.post(f"/api/admin/invitations/{again['invitation']['id']}/revoke")
    assert api.post("/api/auth/invitations/accept", {"token": token_from(again["invitation_path"]), "password": "a-very-long-passphrase"}).status_code == 404


def test_institute_invitation_needs_institution(api, make):
    make(Role.PLATFORM_ADMIN, "admin@test.example.org")
    admin = Api()
    admin.login("admin@test.example.org")
    r = admin.post("/api/admin/invitations", {"email": "x@test.example.org", "full_name": "Xx", "role": "institute_user"})
    assert r.status_code == 422


def test_non_admins_cannot_invite_or_review(api, make):
    for role in (Role.RULES_ADMIN, Role.AUDITOR, Role.INSTITUTE_USER, Role.EXECUTIVE_COMMITTEE):
        make(role, f"{role.value}@test.example.org", institution=role is Role.INSTITUTE_USER)
        c = Api()
        c.login(f"{role.value}@test.example.org")
        assert c.post("/api/admin/invitations", {"email": "y@test.example.org", "full_name": "Yy", "role": "scrutiny_officer"}).status_code == 403
        assert c.post("/api/admin/access-requests/00000000-0000-0000-0000-000000000000/approve").status_code == 403


def test_deactivating_user_revokes_sessions_and_blocks_login(api, make):
    make(Role.PLATFORM_ADMIN, "admin@test.example.org")
    victim = make(Role.SCRUTINY_OFFICER, "officer@test.example.org")
    v = Api()
    v.login("officer@test.example.org")
    admin = Api()
    admin.login("admin@test.example.org")
    assert admin.post(f"/api/admin/users/{victim.id}/deactivate").status_code == 204
    assert v.get("/api/auth/me").status_code == 401
    assert Api().login("officer@test.example.org").status_code == 401


def test_admin_cannot_deactivate_self(api, make):
    me = make(Role.PLATFORM_ADMIN, "admin@test.example.org")
    api.login("admin@test.example.org")
    assert api.post(f"/api/admin/users/{me.id}/deactivate").status_code == 409


def test_audit_log_visible_to_auditor_and_records_admin_actions(api, make):
    make(Role.PLATFORM_ADMIN, "admin@test.example.org")
    make(Role.AUDITOR, "auditor@test.example.org")
    admin = Api()
    admin.login("admin@test.example.org")
    admin.post("/api/admin/invitations", {"email": "o@test.example.org", "full_name": "Oo", "role": "evc_member"})
    aud = Api()
    aud.login("auditor@test.example.org")
    page = aud.get("/api/audit/events?limit=50").json()
    actions = {e["action"] for e in page["items"]}
    assert {"auth.login", "invitation.created"} <= actions
    assert "token" not in str(page).lower().replace("token_hash", "")
    assert aud.post("/api/admin/invitations", {"email": "p@test.example.org", "full_name": "Pp", "role": "evc_member"}).status_code == 403
