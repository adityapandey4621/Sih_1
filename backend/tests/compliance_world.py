"""Scenario helpers for the Phase 7 tests (compliance, validity, clocks, alerts), on top of the decision world."""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update

from app.config import get_settings
from app.db import SessionLocal
from app.models import User, utcnow
from app.models_clocks import Clock, ScheduledWork
from app.models_compliance import Obligation, ValidityItem, ValidityVersion
from app.models_scrutiny import Notification
from app.rbac import Role
from app.schemas_catalogue import DeadlinePolicyIn
from app.services import scheduler
from tests.catalogue_helpers import MAIN_TEXT, PDF
from tests.conftest import Api, make_user
from tests.decision_world import DW, make_dworld
from tests.docs_helpers import digital_pdf
from tests.evidence_world import drain

OFFICER1, OFFICER2 = "officer1@test.example.org", "officer2@test.example.org"
ALERTS = {"pre_due_hours": [168, 24], "at_due": True, "overdue_hours": [24]}


def pdeadlines(src) -> list[DeadlinePolicyIn]:
    """Test configuration: every number is invented for the test. Statutory and internal clocks on the same trigger are deliberately both present."""
    mk = lambda **kw: DeadlinePolicyIn(source_id=src, clause_ref="test", calendar_convention="CALENDAR", **kw)  # noqa: E731
    return [
        DeadlinePolicyIn(policy_key="resp", label="Response", deadline_type="INTERNAL_SLA", trigger_event="deficiency_notice_issued", source_id=src),
        mk(policy_key="appeal", label="Appeal window", deadline_type="STATUTORY", trigger_event="executive_committee_decision_uploaded", duration_value=7, duration_unit="days", escalation={"alerts": {"pre_due_hours": [48, 24], "at_due": True}}),
        mk(policy_key="scr-sla", label="Scrutiny service level", deadline_type="INTERNAL_SLA", trigger_event="scrutiny_case_opened", duration_value=5, duration_unit="days",
           escalation={"alerts": {"pre_due_hours": [24], "overdue_hours": [24]}, "pause": {"allowed": True, "reasons": ["awaiting_clarification"]}, "escalate_to": ["scrutiny_coordinator"]}),
        mk(policy_key="scr-stat", label="Scrutiny statutory period", deadline_type="STATUTORY", trigger_event="scrutiny_case_opened", duration_value=30, duration_unit="days"),
        mk(policy_key="cmp-30", label="Compliance within 30 days", deadline_type="INTERNAL_SLA", trigger_event="compliance_obligation_defined", duration_value=30, duration_unit="days", escalation={"alerts": ALERTS, "escalate_to": ["regulation_bureau"]}),
        DeadlinePolicyIn(policy_key="cmp-alerts", label="Alerts for a specified date", deadline_type="INTERNAL_SLA", trigger_event="compliance_obligation_due", source_id=src, clause_ref="test", escalation={"alerts": ALERTS, "escalate_to": ["regulation_bureau"]}),
        DeadlinePolicyIn(policy_key="val-alerts", label="Alerts for expiry dates", deadline_type="INTERNAL_SLA", trigger_event="document_expiry", source_id=src, clause_ref="test", escalation={"alerts": {"pre_due_hours": [720, 168, 24], "at_due": True, "overdue_hours": [24]}}),
    ]


@dataclass
class PW:
    d: DW
    officer: Api
    officer2: Api

    @property
    def w(self):
        return self.d.w

    @property
    def bureau(self) -> Api:
        return self.d.bureau

    @property
    def inst(self) -> Api:
        return self.d.w.a

    def app_id(self) -> str:
        return self.w.app["id"]

    def user_id(self, email: str) -> str:
        with SessionLocal() as db:
            return str(db.scalar(select(User.id).where(User.email == email)))

    # ---- getting a case to an issued decision ----
    def to_issued(self, valid_until: str | None = "31/03/2029", outcome: str = "LOA", reference: str = "LOA/2026/0001") -> dict:
        w = self.w
        pages = [MAIN_TEXT, [f"Lease term of the premises. Valid until: {valid_until}"]] if valid_until else [MAIN_TEXT]
        w.upload(data=digital_pdf(pages))
        drain()
        w.submit()
        drain()
        w.start()
        r = w.scrutiny.post(f"{w.case_url}/complete", {"row_version": w.case_row().row_version, "summary": "Scrutiny finished without open findings."})
        assert r.status_code == 200, r.text
        self.d.observe()
        self.d.decide(outcome, **({} if outcome == "LOR" else {"speaking_order": None}))  # a Letter of Rejection needs its speaking order
        return self.d.issue(reference)

    def sync(self, now: datetime | None = None) -> dict:
        with SessionLocal() as db:
            return scheduler.run_cycle(db, "test-scheduler", get_settings(), now=now)

    # ---- obligations ----
    def define(self, *, due: dict | None = None, verifier: str = OFFICER1, expect: int = 201, key: str | None = None, client: Api | None = None, **kw) -> dict:
        body = {"title": "Provide the fire safety certificate", "condition_text": "The institution shall provide a valid fire safety certificate for the premises.",
                "condition_ref": "LoA para 4", "required_evidence": "A current fire safety certificate (PDF).", "verifier_id": self.user_id(verifier), "due": due or {"basis": "NONE"},
                "idempotency_key": key or self.d.key("obl"), **kw}
        r = (client or self.bureau).post(f"/api/post-approval/applications/{self.app_id()}/obligations", body)
        assert r.status_code == expect, r.text
        return r.json()

    def specified_due(self, days: int = 10) -> dict:
        return {"basis": "SPECIFIED", "due_at": (utcnow() + timedelta(days=days)).replace(microsecond=0).isoformat(), "authority": "LoA para 4(b)"}

    def evidence(self, obligation: dict, *, client: Api | None = None, expect: int = 201, files: list[tuple[str, str, bytes, str]] | None = None, key: str | None = None, note: str = "Certificate attached.") -> dict:
        c = client or self.inst
        files = files or [("Fire certificate", "fire.pdf", PDF, "application/pdf")]
        r = c.http.post(
            f"/api/post-approval/obligations/{obligation['id']}/evidence", data={"labels": [f[0] for f in files], "note": note, "idempotency_key": key or self.d.key("ev"), "row_version": str(obligation["row_version"])},
            files=[("files", (f[1], f[2], f[3])) for f in files], headers=c._csrf())
        assert r.status_code == expect, r.text
        return r.json()

    def get_obligation(self, oid: str, client: Api | None = None) -> dict:
        r = (client or self.bureau).get(f"/api/post-approval/obligations/{oid}")
        assert r.status_code == 200, r.text
        return r.json()

    def review(self, obligation: dict, outcome: str = "VERIFIED", *, client: Api | None = None, expect: int = 201, reason: str = "The certificate is current and covers the premises.", internal: str | None = "Checked the issuing office's registry.", key: str | None = None) -> dict:
        o = self.get_obligation(obligation["id"], client or self.officer)
        body = {"outcome": outcome, "submission_id": o["latest_submission_id"], "reason": reason, "internal_note": internal, "row_version": o["row_version"], "idempotency_key": key or self.d.key("rv")}
        r = (client or self.officer).post(f"/api/post-approval/obligations/{obligation['id']}/review", body)
        assert r.status_code == expect, r.text
        return r.json()


def make_pworld(*, validity: bool = True, deadlines=pdeadlines, route: str = "scrutiny_only", **kw) -> PW:
    d = make_dworld(route, appeal_days=None, deadlines=deadlines, validity_field=validity, **kw)
    make_user(Role.COMPLIANCE_OFFICER, OFFICER1)
    make_user(Role.COMPLIANCE_OFFICER, OFFICER2)
    clients = []
    for email in (OFFICER1, OFFICER2):
        c = Api()
        assert c.login(email).status_code == 200
        clients.append(c)
    return PW(d, *clients)


# ---- direct database views for assertions ----


def clocks_of(subject_id: str | uuid.UUID | None = None, kind: str | None = None) -> list[Clock]:
    with SessionLocal() as db:
        q = select(Clock)
        if subject_id is not None:
            q = q.where(Clock.subject_id == uuid.UUID(str(subject_id)))
        if kind:
            q = q.where(Clock.kind == kind)
        return list(db.scalars(q.order_by(Clock.created_at, Clock.policy_key)))


def work_of(clock_id: uuid.UUID) -> list[ScheduledWork]:
    with SessionLocal() as db:
        return list(db.scalars(select(ScheduledWork).where(ScheduledWork.clock_id == clock_id).order_by(ScheduledWork.run_at, ScheduledWork.offset_hours)))


def notifs(clock_id: uuid.UUID) -> list[Notification]:
    """The alert notifications raised for one clock (other clocks of the same application raise their own)."""
    with SessionLocal() as db:
        return list(db.scalars(select(Notification).where(Notification.clock_id == clock_id).order_by(Notification.created_at)))


def notifications_like(prefix: str = "CLOCK_") -> list[Notification]:
    with SessionLocal() as db:
        return list(db.scalars(select(Notification).where(Notification.event_type.like(f"{prefix}%")).order_by(Notification.created_at)))


def obligation_row(oid: str) -> Obligation:
    with SessionLocal() as db:
        return db.get(Obligation, uuid.UUID(str(oid)))


def validity_rows(application_id: str) -> tuple[list[ValidityItem], list[ValidityVersion]]:
    with SessionLocal() as db:
        items = list(db.scalars(select(ValidityItem).where(ValidityItem.application_id == uuid.UUID(str(application_id)))))
        versions = list(db.scalars(select(ValidityVersion).join(ValidityItem, ValidityItem.id == ValidityVersion.item_id).where(ValidityItem.application_id == uuid.UUID(str(application_id))).order_by(ValidityVersion.version_number)))
        return items, versions


def set_clock_due(clock_id: uuid.UUID, due: datetime) -> None:
    """Move a clock's stored due date directly in the database. Tests only: the application refuses to do this (see the guard test)."""
    with SessionLocal() as db:
        db.execute(update(Clock).where(Clock.id == clock_id).values(due_at=due))
        db.commit()


def later(hours: float) -> datetime:
    return datetime.now(UTC) + timedelta(hours=hours)
