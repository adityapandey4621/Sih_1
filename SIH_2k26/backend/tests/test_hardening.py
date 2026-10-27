"""Phase 8 hardening: security headers, request size, rate limiting, error responses, client IPs, readiness, secrets and production refusal."""

import os
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app import main as app_main
from app.config import Settings, get_settings
from app.db import SessionLocal, engine
from app.http_hardening import SlidingWindow
from app.netutil import resolve_client_ip
from tests.conftest import PASSWORD, make_user
from app.rbac import Role


def settings(**kw) -> Settings:
    return Settings(**{"environment": "test", "database_url": "sqlite://", **kw})


def build(monkeypatch, **kw) -> TestClient:
    s = settings(**kw)
    monkeypatch.setattr(app_main, "get_settings", lambda: s)
    return TestClient(app_main.create_app(), base_url="http://localhost", raise_server_exceptions=False)


# ---- headers and request ids --------------------------------------------------------------------------------------------


def test_every_api_response_carries_the_security_headers_and_a_request_id(monkeypatch):
    c = build(monkeypatch)
    r = c.get("/api/health")
    h = r.headers
    assert h["x-content-type-options"] == "nosniff" and h["x-frame-options"] == "DENY" and h["referrer-policy"] == "no-referrer"
    assert h["content-security-policy"].startswith("default-src 'none'") and "frame-ancestors 'none'" in h["content-security-policy"]
    assert h["cache-control"] == "no-store" and "camera=()" in h["permissions-policy"] and h["cross-origin-resource-policy"] == "same-origin"
    assert "strict-transport-security" not in h  # only over HTTPS in production
    assert len(h["x-request-id"]) >= 8
    assert c.get("/api/health", headers={"X-Request-ID": "trace-abc-12345"}).headers["x-request-id"] == "trace-abc-12345"
    bad = c.get("/api/health", headers={"X-Request-ID": "x y\"<script>"}).headers["x-request-id"]
    assert bad != 'x y"<script>' and "<" not in bad  # an inbound id is accepted only if it is a plain token
    docs = c.get("/api/docs")
    assert docs.status_code == 200 and "content-security-policy" not in docs.headers  # the dev API docs need scripts; they are off in production


def test_hsts_is_sent_in_production_only(monkeypatch):
    c = build(monkeypatch, environment="production", database_url="postgresql+psycopg://sih:real-secret@db/sih", allowed_origins=["https://app.example.org"])
    r = c.get("/api/health")
    assert r.headers["strict-transport-security"].startswith("max-age=31536000")
    assert c.get("/api/docs").status_code == 404  # no API documentation in production


# ---- error responses ------------------------------------------------------------------------------------------------------


def test_a_validation_error_never_echoes_what_was_submitted(monkeypatch):
    c = build(monkeypatch)
    csrf = c.get("/api/auth/csrf").json()["csrf_token"]
    r = c.post("/api/auth/login", json={"email": "not-an-email", "password": "my-very-secret-passphrase-123"}, headers={"X-CSRF-Token": csrf})
    assert r.status_code == 422
    assert "my-very-secret-passphrase-123" not in r.text and "not-an-email" not in r.text
    item = r.json()["detail"][0]
    assert set(item) == {"loc", "msg", "type"}  # where and why, not what


def test_an_unexpected_error_gives_a_generic_answer_an_id_and_no_internals(monkeypatch, caplog):
    c = build(monkeypatch)

    @c.app.get("/api/boom")
    def boom() -> None:
        raise RuntimeError("SELECT * FROM users WHERE password = 'hunter2'")

    with caplog.at_level("ERROR", logger="app.access"):
        r = c.get("/api/boom")
    assert r.status_code == 500 and r.json()["code"] == "internal_error" and r.json()["request_id"] == r.headers["x-request-id"]
    assert "hunter2" not in r.text and "SELECT" not in r.text and "Traceback" not in r.text
    assert r.headers["x-content-type-options"] == "nosniff"  # even an error carries the headers
    logged = " ".join(rec.getMessage() for rec in caplog.records)
    assert "RuntimeError" in logged and "hunter2" not in logged and r.headers["x-request-id"] in logged  # the log says where and what kind, never the message


def test_the_access_log_records_method_path_status_and_nothing_private(monkeypatch, caplog):
    c = build(monkeypatch)
    with caplog.at_level("INFO", logger="app.access"):
        c.get("/api/health?token=abc123&email=a@b.c", headers={"Cookie": "sih_session=SESSIONSECRET"})
    line = " ".join(r.getMessage() for r in caplog.records)
    assert "GET /api/health 200" in line and "abc123" not in line and "SESSIONSECRET" not in line and "a@b.c" not in line


# ---- size ------------------------------------------------------------------------------------------------------------------


def test_an_oversized_request_is_refused_before_it_is_read(monkeypatch):
    c = build(monkeypatch, max_request_mb=1)
    r = c.post("/api/auth/login", content=b"x" * (2 * 1024 * 1024), headers={"Content-Type": "application/octet-stream"})
    assert r.status_code == 413 and r.json()["code"] == "request_too_large"
    ok = c.post("/api/auth/login", json={"email": "a@b.co", "password": "x" * 20}, headers={"X-CSRF-Token": "t"})
    assert ok.status_code != 413


def test_a_streamed_body_without_a_declared_length_is_cut_off_at_the_cap(monkeypatch):
    c = build(monkeypatch, max_request_mb=1)

    def chunks():
        for _ in range(4):
            yield b"x" * (400 * 1024)  # no Content-Length: the size is only known as the body arrives

    r = c.post("/api/auth/login", content=chunks(), headers={"Content-Type": "application/octet-stream"})
    assert r.status_code == 413 and r.json()["code"] == "request_too_large" and "x-request-id" in r.headers  # one clear answer, not FastAPI's 400 "error parsing the body"


# ---- rate limiting ----------------------------------------------------------------------------------------------------------


def test_writes_are_rate_limited_per_client_with_retry_after(monkeypatch):
    c = build(monkeypatch, rate_limit_enabled=True, rate_limit_write_per_minute=3, rate_limit_read_per_minute=50, rate_limit_auth_per_minute=100)
    csrf = c.get("/api/auth/csrf").json()["csrf_token"]
    codes = [c.post("/api/auth/logout", headers={"X-CSRF-Token": csrf}).status_code for _ in range(5)]
    assert codes.count(429) == 2 and codes[:3] == [204, 204, 204]
    r = c.post("/api/auth/logout", headers={"X-CSRF-Token": csrf})
    assert r.status_code == 429 and int(r.headers["retry-after"]) >= 1 and r.json()["code"] == "rate_limited" and "x-request-id" in r.headers
    assert c.get("/api/health").status_code == 200  # probes are never limited
    assert c.get("/api/auth/csrf").status_code == 200  # a separate bucket for reads


def test_sign_in_attempts_are_limited_per_address_independently_of_the_account_throttle(monkeypatch):
    c = build(monkeypatch, rate_limit_enabled=True, rate_limit_auth_per_minute=4)
    csrf = c.get("/api/auth/csrf").json()["csrf_token"]
    codes = [c.post("/api/auth/login", json={"email": f"user{i}@test.example.org", "password": "wrong-password-12345"}, headers={"X-CSRF-Token": csrf}).status_code for i in range(6)]
    assert codes[:4] == [401] * 4 and codes[4:] == [429, 429]  # guessing across many accounts from one address is stopped too


def test_the_window_forgets_and_stays_small():
    w = SlidingWindow()
    assert [w.allow("k", 2, now=0.0)[0], w.allow("k", 2, now=1.0)[0], w.allow("k", 2, now=2.0)[0]] == [True, True, False]
    assert w.allow("k", 2, now=61.0)[0]  # a minute later the oldest hit has aged out
    for i in range(30000):
        w.allow(f"client-{i}", 5, now=100.0)
    w.allow("late", 5, now=500.0)  # a sweep drops idle clients: memory stays bounded
    assert len(w.hits) < 100


# ---- who is calling -----------------------------------------------------------------------------------------------------------


def test_forwarded_for_is_ignored_unless_proxies_are_trusted_and_cannot_be_spoofed():
    assert resolve_client_ip("10.0.0.5", "1.2.3.4", 0) == "10.0.0.5"  # no trusted proxy: the header is just a claim
    assert resolve_client_ip("10.0.0.5", "1.2.3.4, 198.51.100.7", 1) == "198.51.100.7"  # the address OUR proxy saw; the client-supplied 1.2.3.4 is ignored
    assert resolve_client_ip("10.0.0.5", "198.51.100.7", 2) == "10.0.0.5"  # fewer hops than expected: not produced by our proxies
    assert resolve_client_ip("10.0.0.5", "1.2.3.4, not-an-ip", 1) == "10.0.0.5"
    assert resolve_client_ip("10.0.0.5", "2001:db8::1", 1) == "2001:db8::1"
    assert resolve_client_ip(None, None, 0) is None


# ---- readiness ------------------------------------------------------------------------------------------------------------------


def test_readiness_reports_booleans_only(monkeypatch):
    c = build(monkeypatch)
    r = c.get("/api/ready")
    assert r.status_code == 200 and r.json()["status"] == "ready" and r.json()["checks"]["database"] is True and r.json()["checks"]["storage_writable"] is True
    assert c.get("/api/health").json() == {"status": "ok"}
    blocker = Path(tempfile.mkdtemp(prefix="sih-ready-")) / "a-file"
    blocker.write_text("not a directory")
    monkeypatch.setattr(get_settings(), "storage_dir", str(blocker / "sub"))
    r = c.get("/api/ready")
    assert r.status_code == 503 and r.json()["status"] == "not_ready" and r.json()["checks"]["storage_writable"] is False
    assert str(blocker) not in r.text  # no path leaks


# ---- secrets and production --------------------------------------------------------------------------------------------------------


def test_secrets_can_come_from_files_and_a_direct_value_wins(monkeypatch, tmp_path):
    f = tmp_path / "db_url"
    f.write_text("postgresql+psycopg://sih:from-file@db/sih\n")
    monkeypatch.setenv("DATABASE_URL_FILE", str(f))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert Settings(_env_file=None).database_url == "postgresql+psycopg://sih:from-file@db/sih"  # trailing newline stripped
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://sih:direct@db/sih")
    assert Settings(_env_file=None).database_url.endswith(":direct@db/sih")
    key = tmp_path / "key"
    key.write_text("api-key-from-file")
    monkeypatch.setenv("AICTE_API_KEY_FILE", str(key))
    monkeypatch.delenv("AICTE_API_KEY", raising=False)
    assert Settings(_env_file=None).aicte_api_key == "api-key-from-file"


def test_allowed_origins_accepts_a_comma_list_and_a_json_list(monkeypatch):
    """Found by running the Docker stack: a plain `ALLOWED_ORIGINS=http://localhost:8080` (the form .env.example and docker-compose use) crashed the process at start-up,
    because pydantic-settings tried to JSON-parse it before the validator saw it."""
    monkeypatch.setenv("ALLOWED_ORIGINS", "http://localhost:8080")
    assert Settings(_env_file=None).allowed_origins == ["http://localhost:8080"]
    monkeypatch.setenv("ALLOWED_ORIGINS", "http://localhost:8080, https://dossier.example.org ,")
    assert Settings(_env_file=None).allowed_origins == ["http://localhost:8080", "https://dossier.example.org"]
    monkeypatch.setenv("ALLOWED_ORIGINS", '["http://a.example.org", "https://b.example.org"]')
    assert Settings(_env_file=None).allowed_origins == ["http://a.example.org", "https://b.example.org"]
    monkeypatch.delenv("ALLOWED_ORIGINS")
    assert Settings(_env_file=None).allowed_origins == ["http://localhost:5173", "http://127.0.0.1:5173"]


def test_production_refuses_to_start_with_development_settings(monkeypatch):
    bad = [
        settings(environment="production"),  # sqlite
        settings(environment="production", database_url="postgresql+psycopg://sih:sih_dev_password@db/sih", allowed_origins=["https://x.example.org"]),
        settings(environment="production", database_url="postgresql+psycopg://sih:real@db/sih", allowed_origins=["http://localhost:5173"]),
        settings(environment="production", database_url="postgresql+psycopg://sih:real@db/sih", allowed_origins=["https://x.example.org"], seed_password="demo-passphrase-2026"),
        settings(environment="production", database_url="postgresql+psycopg://sih:real@db/sih", allowed_origins=["https://x.example.org"], aicte_integration_mode="mock"),
        settings(environment="production", database_url="postgresql+psycopg://sih:real@db/sih", allowed_origins=["https://x.example.org"], aicte_integration_mode="http", aicte_base_url="http://aicte.example.org", aicte_api_key="k", aicte_hmac_secret="s"),
        settings(environment="production", database_url="postgresql+psycopg://sih:real@db/sih", allowed_origins=["https://x.example.org"], aicte_integration_mode="http", aicte_base_url="https://aicte.example.org"),
    ]
    for s in bad:
        assert s.production_problems(), s
        monkeypatch.setattr(app_main, "get_settings", lambda s=s: s)
        with pytest.raises(RuntimeError, match="Refusing to start in production"):
            app_main.create_app()
    good = settings(environment="production", database_url="postgresql+psycopg://sih:real@db/sih", allowed_origins=["https://x.example.org"])
    assert good.production_problems() == [] and good.demo_rules_enabled is False and good.malware_scan_required is True


def test_the_mock_adapter_is_never_used_in_production():
    from app.integration.aicte import factory

    factory.reset()
    s = settings(environment="production", aicte_integration_mode="mock")
    assert factory.get_adapter(s) is None
    factory.reset()


# ---- database-level protection of the audit trail (PostgreSQL only) --------------------------------------------------------------------


@pytest.mark.skipif(engine.dialect.name != "postgresql", reason="append-only triggers are a PostgreSQL feature; the fast SQLite suite has no equivalent")
def test_the_audit_trail_cannot_be_rewritten_even_with_raw_sql():
    from sqlalchemy.exc import DBAPIError

    from app import db_protect
    from app.models import AuditEvent

    with engine.begin() as conn:
        db_protect.install(conn)
        assert db_protect.missing(conn) == []
    make_user(Role.PLATFORM_ADMIN, "admin@test.example.org")
    with SessionLocal() as db:
        db.add(AuditEvent(action="test.event", actor_label="x"))
        db.commit()
    for sql in ("UPDATE audit_events SET action = 'tampered'", "DELETE FROM audit_events", "TRUNCATE audit_events"):
        with engine.connect() as conn:
            with pytest.raises(DBAPIError, match="append-only"):
                conn.execute(text(sql))
                conn.commit()
    with SessionLocal() as db:
        assert db.query(AuditEvent).filter(AuditEvent.action == "test.event").count() == 1
    with engine.begin() as conn:
        db_protect.uninstall(conn)
        assert len(db_protect.missing(conn)) == len(db_protect.APPEND_ONLY_TABLES)


_ = (PASSWORD, os)
