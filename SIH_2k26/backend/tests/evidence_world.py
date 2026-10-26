"""Shared scenario: a published demo rule version, two institutions, a reviewer, an auditor."""

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select

from app.config import get_settings
from app.db import SessionLocal
from app.models import utcnow
from app.models_applications import Application, ApplicationDocument
from app.models_processing import DocumentProcessing, ProcessingJob
from app.rbac import Role
from app.storage import LocalStorage
from app.worker import process_one, run_until_idle
from tests.catalogue_helpers import MAIN_TEXT, Cat, build_published
from tests.conftest import Api, make_user
from tests.docs_helpers import digital_pdf


@dataclass
class World:
    cat: Cat
    a: Api  # institution A (owner)
    b: Api  # institution B
    scrutiny: Api
    auditor: Api
    app: dict
    url: str

    def upload(self, item: str = "doc-main", data: bytes | None = None, name: str = "main.pdf", client: Api | None = None, expect: tuple[int, ...] | None = (200, 201)):
        data = data if data is not None else digital_pdf([MAIN_TEXT])
        r = (client or self.a).upload(f"{self.url}/documents", item, name, data)
        if expect is not None:
            assert r.status_code in expect, f"upload answered {r.status_code}: {r.text[:200]}"
        return r

    def refresh(self) -> dict:
        return self.a.get(self.url).json()

    def evidence(self, client: Api | None = None) -> dict:
        return (client or self.a).get(f"{self.url}/evidence").json()

    def submit(self) -> dict:
        d = self.refresh()
        r = self.a.patch(self.url, {"form_data": {"contact": "R", "kind": "A", "decl": True}, "row_version": d["row_version"]})
        d = r.json()
        return self.a.post(f"{self.url}/submit", {"row_version": d["row_version"]}).json()


def make_world() -> World:
    cat = build_published(set_key="s1")
    make_user(Role.INSTITUTE_USER, "a@test.example.org", institution=True)
    make_user(Role.INSTITUTE_USER, "b@test.example.org", institution=True)
    make_user(Role.SCRUTINY_OFFICER, "scrutiny@test.example.org")
    make_user(Role.AUDITOR, "auditor@test.example.org")
    clients = {}
    for key, email in (("a", "a@test.example.org"), ("b", "b@test.example.org"), ("scrutiny", "scrutiny@test.example.org"), ("auditor", "auditor@test.example.org")):
        c = Api()
        assert c.login(email).status_code == 200
        clients[key] = c
    app = clients["a"].post("/api/applications", {"category_id": str(cat.category_id), "academic_year": "2026-27"}).json()
    return World(cat, clients["a"], clients["b"], clients["scrutiny"], clients["auditor"], app, f"/api/applications/{app['id']}")


def storage() -> LocalStorage:
    return LocalStorage(get_settings().storage_dir)


def drain(settings=None, **kw) -> list[str]:
    """Run the worker until nothing is runnable (the test equivalent of `python -m app.worker --once`)."""
    return run_until_idle(settings or get_settings(), worker_id="test-worker", storage=storage(), **kw)


def step(now: datetime | None = None, worker: str = "w1", settings=None, **kw) -> str | None:
    return process_one(settings or get_settings(), worker_id=worker, storage=storage(), now=now, **kw)


def later(seconds: int) -> datetime:
    return utcnow() + timedelta(seconds=seconds)


def doc_rows(application_id) -> list[ApplicationDocument]:
    with SessionLocal() as db:
        return list(db.scalars(select(ApplicationDocument).where(ApplicationDocument.application_id == uuid.UUID(str(application_id))).order_by(ApplicationDocument.item_key, ApplicationDocument.version_number)))


def job_for(document_id) -> ProcessingJob:
    with SessionLocal() as db:
        return db.scalar(select(ProcessingJob).where(ProcessingJob.document_id == document_id, ProcessingJob.job_type == "PROCESS_DOCUMENT"))


def proc_for(document_id) -> DocumentProcessing:
    with SessionLocal() as db:
        return db.scalar(select(DocumentProcessing).where(DocumentProcessing.document_id == document_id))


def application_status(application_id) -> str:
    with SessionLocal() as db:
        return db.get(Application, uuid.UUID(str(application_id))).status
