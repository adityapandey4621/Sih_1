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


REVIEWER = "scrutiny@test.example.org"
REVIEWER2 = "scrutiny2@test.example.org"


@dataclass
class World:
    cat: Cat
    a: Api  # institution A (owner)
    b: Api  # institution B
    scrutiny: Api  # the assigned reviewer (scrutiny_officer)
    auditor: Api
    app: dict
    url: str
    coordinator: Api = None  # scrutiny_coordinator
    issuer: Api = None  # notice_issuer
    scrutiny2: Api = None  # a second scrutiny_officer who is not assigned unless a test assigns them

    # ---- scrutiny helpers (Phase 4) ----
    def case_row(self):
        from app.models_scrutiny import ScrutinyCase

        with SessionLocal() as db:
            return db.scalar(select(ScrutinyCase).where(ScrutinyCase.application_id == uuid.UUID(self.app["id"])))

    @property
    def case_id(self) -> str:
        return str(self.case_row().id)

    @property
    def case_url(self) -> str:
        return f"/api/scrutiny/cases/{self.case_id}"

    def assign(self, email: str = REVIEWER, reason: str | None = None):
        from app.models import User

        with SessionLocal() as db:
            uid = db.scalar(select(User.id).where(User.email == email))
        r = self.coordinator.post(f"{self.case_url}/assign", {"user_id": str(uid), "reason": reason, "row_version": self.case_row().row_version})
        assert r.status_code == 200, r.text
        return r.json()

    def case(self, client: Api | None = None) -> dict:
        r = (client or self.scrutiny).get(self.case_url)
        assert r.status_code == 200, r.text
        return r.json()

    def start(self, client: Api | None = None) -> dict:
        r = (client or self.scrutiny).post(f"{self.case_url}/start", {"row_version": self.case_row().row_version})
        assert r.status_code == 200, r.text
        return r.json()

    def finding(self, client: Api | None = None, expect: int = 201, **kw) -> dict:
        body = {"issue_type": "incomplete_information", "description": "The affiliation letter does not show the validity period.", **kw}
        r = (client or self.scrutiny).post(f"{self.case_url}/findings", body)
        assert r.status_code == expect, r.text
        return r.json()

    def eligible_finding(self, **kw) -> dict:
        defaults = {"visibility": "ELIGIBLE_FOR_NOTICE", "institution_text": "Please show the validity period of the affiliation letter.",
                    "requested_correction": "Upload a letter that states its validity period.", "item_key": "doc-main"}
        return self.finding(**{**defaults, **kw})["findings"][-1]

    def make_notice(self, finding_ids: list[str] | None = None, summary: str = "We need the validity period of the affiliation letter.") -> dict:
        ids = finding_ids or [self.eligible_finding()["id"]]
        r = self.scrutiny.post(f"{self.case_url}/notices", {"finding_ids": ids, "summary": summary})
        assert r.status_code == 201, r.text
        return r.json()["notices"][-1]

    def submit_notice(self, notice: dict) -> dict:
        r = self.scrutiny.post(f"/api/scrutiny/notices/{notice['id']}/submit", {"row_version": notice["row_version"]})
        assert r.status_code == 200, r.text
        return next(n for n in r.json()["notices"] if n["id"] == notice["id"])

    def issue(self, notice_id: str, key: str = "issue-key-0001", client: Api | None = None, expect: int = 200, **kw):
        c = client or self.issuer
        pv = c.get(f"/api/scrutiny/notices/{notice_id}/preview").json()
        body = {"content_hash": pv["content_hash"], "issue_reason": "Inspected the notice and the evidence; issuing.", "acknowledge_unsourced": True,
                "idempotency_key": key, "row_version": pv["row_version"], **kw}
        r = c.post(f"/api/scrutiny/notices/{notice_id}/issue", body)
        assert r.status_code == expect, r.text
        return r

    def issued_notice(self) -> dict:
        """Reviewer records a finding, prepares a notice; the issuer issues it. Returns the issued notice as staff see it."""
        self.start()
        n = self.submit_notice(self.make_notice())
        self.issue(n["id"])
        return next(x for x in self.case()["notices"] if x["id"] == n["id"])

    def respond(self, notice_id: str, text: str = "The letter is enclosed.", upload: bool = True) -> dict:
        d = self.a.get(f"/api/notices/{notice_id}").json()
        item = d["items"][0]
        if upload:
            r = self.a.upload(f"/api/notices/{notice_id}/items/{item['id']}/documents", "file", "corrected.pdf", digital_pdf([MAIN_TEXT, ["Valid until: 31/03/2030"]]))
            assert r.status_code in (200, 201), r.text
        d = self.a.get(f"/api/notices/{notice_id}").json()
        r = self.a.put(f"/api/notices/{notice_id}/response", {"explanation": "Please see the corrected letter.", "items": [{"notice_item_id": item["id"], "explanation": text}],
                                                               "row_version": d["response"]["row_version"] if d["response"] else None})
        assert r.status_code == 200, r.text
        d = r.json()
        r = self.a.post(f"/api/notices/{notice_id}/response/submit", {"idempotency_key": "resp-key-0001", "row_version": d["response"]["row_version"]})
        assert r.status_code == 200, r.text
        return r.json()

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

    def submit(self, assign: bool = True) -> dict:
        """Submit the application. By default the standard reviewer is then assigned (the Phase 3 reviewer scenarios need that);
        pass assign=False to leave the case in the queue awaiting assignment."""
        d = self.refresh()
        r = self.a.patch(self.url, {"form_data": {"contact": "R", "kind": "A", "decl": True}, "row_version": d["row_version"]})
        d = r.json()
        out = self.a.post(f"{self.url}/submit", {"row_version": d["row_version"]}).json()
        if assign and out.get("status") == "SUBMITTED":
            self.assign()
        return out


def make_world(**cat_kwargs) -> World:
    cat = build_published(set_key="s1", **cat_kwargs)
    make_user(Role.INSTITUTE_USER, "a@test.example.org", institution=True)
    make_user(Role.INSTITUTE_USER, "b@test.example.org", institution=True)
    make_user(Role.SCRUTINY_OFFICER, REVIEWER)
    make_user(Role.SCRUTINY_OFFICER, REVIEWER2)
    make_user(Role.SCRUTINY_COORDINATOR, "coordinator@test.example.org")
    make_user(Role.NOTICE_ISSUER, "issuer@test.example.org")
    make_user(Role.AUDITOR, "auditor@test.example.org")
    clients = {}
    for key, email in (("a", "a@test.example.org"), ("b", "b@test.example.org"), ("scrutiny", REVIEWER), ("scrutiny2", REVIEWER2), ("coordinator", "coordinator@test.example.org"),
                       ("issuer", "issuer@test.example.org"), ("auditor", "auditor@test.example.org")):
        c = Api()
        assert c.login(email).status_code == 200
        clients[key] = c
    app = clients["a"].post("/api/applications", {"category_id": str(cat.category_id), "academic_year": "2026-27"}).json()
    return World(cat, clients["a"], clients["b"], clients["scrutiny"], clients["auditor"], app, f"/api/applications/{app['id']}",
                 coordinator=clients["coordinator"], issuer=clients["issuer"], scrutiny2=clients["scrutiny2"])


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
