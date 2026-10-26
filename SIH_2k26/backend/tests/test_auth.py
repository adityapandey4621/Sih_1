from datetime import timedelta

from sqlalchemy import select

from app.config import get_settings
from app.db import SessionLocal
from app.models import AuditEvent, AuthSession, User, utcnow
from app.rbac import Role
from app.security import hash_password, verify_password
from tests.conftest import PASSWORD


def test_login_success_sets_hardened_cookie_and_returns_permissions(api, make):
    make(Role.SCRUTINY_OFFICER, "officer@test.example.org")
    r = api.login("Officer@Test.Example.org")  # email is case-insensitive
    assert r.status_code == 200
    body = r.json()
    assert body["role"] == "scrutiny_officer"
    assert body["permissions"] == ["dashboard:scrutiny", "document:retry", "evidence:read"]
    set_cookie = r.headers.get_list("set-cookie")
    session_cookie = next(c for c in set_cookie if c.startswith("sih_session="))
    assert "HttpOnly" in session_cookie
    assert "samesite=lax" in session_cookie.lower()
    assert "password" not in r.text.lower()


def test_me_requires_session(api):
    assert api.get("/api/auth/me").status_code == 401


def test_session_probe_is_quiet_when_signed_out_and_full_when_signed_in(api, make):
    r = api.get("/api/auth/session")
    assert r.status_code == 200 and r.json() is None
    make(Role.AUDITOR)
    api.login("auditor@test.example.org")
    assert api.get("/api/auth/session").json()["email"] == "auditor@test.example.org"
    api.post("/api/auth/logout")
    assert api.get("/api/auth/session").json() is None


def test_session_persists_then_logout_revokes(api, make):
    make(Role.AUDITOR)
    api.login("auditor@test.example.org")
    assert api.get("/api/auth/me").json()["email"] == "auditor@test.example.org"
    assert api.post("/api/auth/logout").status_code == 204
    assert api.get("/api/auth/me").status_code == 401
    with SessionLocal() as db:
        assert db.scalar(select(AuthSession)).revoked_at is not None


def test_revoked_token_cannot_be_replayed(api, make):
    make(Role.AUDITOR)
    api.login("auditor@test.example.org")
    token = api.http.cookies.get("sih_session")
    api.post("/api/auth/logout")
    api.http.cookies.set("sih_session", token)
    assert api.get("/api/auth/me").status_code == 401


def test_failure_messages_do_not_reveal_whether_account_exists(api, make):
    make(Role.AUDITOR)
    wrong_pw = api.login("auditor@test.example.org", "wrong-password-123")
    unknown = api.login("nobody@test.example.org", "wrong-password-123")
    assert wrong_pw.status_code == unknown.status_code == 401
    assert wrong_pw.json() == unknown.json()


def test_inactive_user_cannot_sign_in(api, make):
    make(Role.AUDITOR, active=False)
    r = api.login("auditor@test.example.org")
    assert r.status_code == 401
    assert r.json()["detail"] == "Incorrect email or password."


def test_login_is_throttled_after_repeated_failures_even_for_unknown_accounts(api, make):
    make(Role.AUDITOR)
    limit = get_settings().login_max_failures
    for _ in range(limit):
        assert api.login("auditor@test.example.org", "bad-password-123").status_code == 401
    r = api.login("auditor@test.example.org")  # correct password, but locked
    assert r.status_code == 429
    assert "Retry-After" in r.headers
    for _ in range(limit):
        api.login("ghost@test.example.org", "bad-password-123")
    assert api.login("ghost@test.example.org", "bad-password-123").status_code == 429


def test_successful_login_clears_failure_counter(api, make):
    make(Role.AUDITOR)
    for _ in range(get_settings().login_max_failures - 1):
        api.login("auditor@test.example.org", "bad-password-123")
    assert api.login("auditor@test.example.org").status_code == 200
    api.post("/api/auth/logout")
    for _ in range(get_settings().login_max_failures - 1):
        assert api.login("auditor@test.example.org", "bad-password-123").status_code == 401


def test_idle_session_expires(api, make):
    make(Role.AUDITOR)
    api.login("auditor@test.example.org")
    with SessionLocal() as db:
        s = db.scalar(select(AuthSession))
        s.last_seen_at = utcnow() - timedelta(minutes=get_settings().session_idle_minutes + 1)
        db.commit()
    assert api.get("/api/auth/me").status_code == 401


def test_absolute_session_expiry(api, make):
    make(Role.AUDITOR)
    api.login("auditor@test.example.org")
    with SessionLocal() as db:
        s = db.scalar(select(AuthSession))
        s.expires_at = utcnow() - timedelta(seconds=1)
        db.commit()
    assert api.get("/api/auth/me").status_code == 401


def test_deactivated_user_session_stops_working(api, make):
    u = make(Role.AUDITOR)
    api.login("auditor@test.example.org")
    with SessionLocal() as db:
        db.get(User, u.id).is_active = False
        db.commit()
    assert api.get("/api/auth/me").status_code == 401


def test_csrf_required_on_state_changing_requests(api, make):
    make(Role.AUDITOR)
    api.http.get("/api/auth/csrf")
    r = api.http.post("/api/auth/login", json={"email": "auditor@test.example.org", "password": PASSWORD})
    assert r.status_code == 403
    r = api.http.post(
        "/api/auth/login",
        json={"email": "auditor@test.example.org", "password": PASSWORD},
        headers={"X-CSRF-Token": "not-the-cookie-value"},
    )
    assert r.status_code == 403


def test_foreign_origin_rejected(api, make):
    make(Role.AUDITOR)
    csrf = api.http.get("/api/auth/csrf").json()["csrf_token"]
    r = api.http.post(
        "/api/auth/login",
        json={"email": "auditor@test.example.org", "password": PASSWORD},
        headers={"X-CSRF-Token": csrf, "Origin": "https://evil.example.com"},
    )
    assert r.status_code == 403


def test_new_session_token_on_each_login(api, make):
    make(Role.AUDITOR)
    api.login("auditor@test.example.org")
    first = api.http.cookies.get("sih_session")
    api.login("auditor@test.example.org")
    assert api.http.cookies.get("sih_session") != first
    with SessionLocal() as db:
        old = [s for s in db.scalars(select(AuthSession)) if s.revoked_at is not None]
        assert len(old) == 1


def test_passwords_stored_as_argon2id_hashes(make):
    u = make(Role.AUDITOR)
    assert u.password_hash.startswith("$argon2id$")
    assert PASSWORD not in u.password_hash
    assert verify_password(PASSWORD, u.password_hash)
    assert not verify_password("other", u.password_hash)
    assert not verify_password(PASSWORD, None)


def test_password_hash_never_in_any_response(api, make):
    make(Role.PLATFORM_ADMIN)
    api.login("platform_admin@test.example.org")
    assert "argon2" not in api.get("/api/admin/users").text


def test_audit_trail_records_login_events(api, make):
    make(Role.AUDITOR)
    api.login("auditor@test.example.org", "bad-password-123")
    api.login("auditor@test.example.org")
    api.post("/api/auth/logout")
    with SessionLocal() as db:
        actions = [e.action for e in db.scalars(select(AuditEvent).order_by(AuditEvent.occurred_at))]
    assert actions == ["auth.login_failed", "auth.login", "auth.logout"]
    with SessionLocal() as db:
        for e in db.scalars(select(AuditEvent)):
            assert PASSWORD not in str(e.detail) and "bad-password" not in str(e.detail)


def test_password_reset_reports_unavailable_truthfully(api):
    r = api.post("/api/auth/password-reset", {})
    assert r.status_code == 503
    assert "not available" in r.json()["detail"]


def test_hash_password_helper_roundtrip():
    assert verify_password("abc", hash_password("abc"))
