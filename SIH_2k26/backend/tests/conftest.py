import os
import tempfile
from pathlib import Path

# Must be set before the app modules are imported (the engine is created at import time).
_tmp = Path(tempfile.mkdtemp(prefix="sih-tests-"))
os.environ["ENVIRONMENT"] = "test"
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or f"sqlite:///{(_tmp / 'test.db').as_posix()}"
os.environ["SEED_PASSWORD"] = ""

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import models  # noqa: E402,F401
from app.db import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Institution, User  # noqa: E402
from app.rbac import Role  # noqa: E402
from app.security import hash_password  # noqa: E402

PASSWORD = "correct horse battery staple"


@pytest.fixture(autouse=True)
def _fresh_db():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield


class Api:
    """TestClient wrapper that performs the CSRF double-submit dance like the SPA does."""

    def __init__(self) -> None:
        self.http = TestClient(app, base_url="http://localhost")

    def _csrf(self) -> dict[str, str]:
        token = self.http.cookies.get("sih_csrf")
        if not token:
            token = self.http.get("/api/auth/csrf").json()["csrf_token"]
        return {"X-CSRF-Token": token}

    def get(self, path: str, **kw):
        return self.http.get(path, **kw)

    def post(self, path: str, json: dict | None = None, **kw):
        return self.http.post(path, json=json, headers=self._csrf(), **kw)

    def login(self, email: str, password: str = PASSWORD):
        return self.post("/api/auth/login", {"email": email, "password": password})


@pytest.fixture
def api() -> Api:
    return Api()


def make_user(role: Role, email: str | None = None, *, active: bool = True, institution: bool = False) -> User:
    with SessionLocal() as db:
        inst = None
        if institution:
            inst = Institution(name="Test Institute")
            db.add(inst)
            db.flush()
        user = User(
            email=email or f"{role.value}@test.example.org",
            full_name=f"Test {role.value}",
            password_hash=hash_password(PASSWORD),
            role=role.value,
            institution_id=inst.id if inst else None,
            is_active=active,
        )
        db.add(user)
        db.commit()
        return user


@pytest.fixture
def make():
    return make_user
