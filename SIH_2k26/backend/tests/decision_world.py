"""Scenario helpers for the decision / ratification / appeal tests, on top of the committee world."""

import uuid
from dataclasses import dataclass

from sqlalchemy import select

from app.db import SessionLocal
from app.models_decisions import Appeal, DecisionCase
from app.rbac import Role
from tests.committee_world import CW, make_cworld
from tests.conftest import Api, make_user

EC, BUREAU, COUNCIL, SAC_COORD, SAC_M1 = ("ec@test.example.org", "bureau@test.example.org", "council@test.example.org", "sac.coord@test.example.org", "sac.m1@test.example.org")


@dataclass
class DW:
    c: CW
    ec: Api = None
    bureau: Api = None
    council: Api = None
    sac_coord: Api = None
    sac1: Api = None

    @property
    def w(self):
        return self.c.w

    def case_row(self) -> DecisionCase | None:
        with SessionLocal() as db:
            return db.scalar(select(DecisionCase).where(DecisionCase.application_id == uuid.UUID(self.w.app["id"])))

    def url(self) -> str:
        r = self.case_row()
        assert r is not None, "no decision case"
        return f"/api/decisions/cases/{r.id}"

    def case(self, client: Api | None = None) -> dict:
        r = (client or self.bureau).get(self.url())
        assert r.status_code == 200, r.text
        return r.json()

    def rv(self) -> int:
        return self.case_row().row_version

    def key(self, p: str = "k") -> str:
        return self.c.key(p)

    # ---- the steps ----
    def to_queue(self) -> dict:
        self.c.finish_scrutiny()
        return self.case()

    def observe(self, text: str = "The Regulation Bureau has examined the record.", expect: int = 201, client: Api | None = None, key: str | None = None) -> dict:
        r = (client or self.bureau).post(f"{self.url()}/observations", {"text": text, "idempotency_key": key or self.key("obs")})
        assert r.status_code == expect, r.text
        return r.json()

    def decide(self, outcome: str = "LOR", expect: int = 201, client: Api | None = None, key: str | None = None, **kw) -> dict:
        d = self.case()
        body = {"outcome": outcome, "reasons": "The Executive Committee considered the record and decided accordingly.", "speaking_order": "Speaking order: the reasons for this outcome are set out here in full.",
                "meeting_reference": "EC meeting 1", "considered": d["considerable"], "idempotency_key": key or self.key("dec"), "row_version": self.rv(), **kw}
        r = (client or self.ec).post(f"{self.url()}/decision", body)
        assert r.status_code == expect, r.text
        return r.json()

    def correct(self, outcome: str = "LOD", reason: str = "A clerical error in the outcome was found.", expect: int = 201, client: Api | None = None, **kw) -> dict:
        d = self.case()
        body = {"outcome": outcome, "reasons": "Corrected reasons recorded by the Executive Committee.", "speaking_order": "Speaking order for the corrected decision, in full.", "considered": d["considerable"],
                "correction_reason": reason, "idempotency_key": self.key("cor"), "row_version": self.rv(), **kw}
        r = (client or self.ec).post(f"{self.url()}/decision/correct", body)
        assert r.status_code == expect, r.text
        return r.json()

    def issue(self, reference: str | None = "LOR/2026/0001", expect: int = 201, client: Api | None = None, key: str | None = None, file: tuple | None = None) -> dict:
        data = {"idempotency_key": key or self.key("iss"), "row_version": str(self.rv())}
        if reference:
            data["document_reference"] = reference
        c = client or self.bureau
        r = c.http.post(f"{self.url()}/issue", data=data, files={"file": file} if file else None, headers=c._csrf())
        assert r.status_code == expect, r.text
        return r.json()

    def ratify(self, outcome: str = "RATIFIED", expect: int = 201, client: Api | None = None, **kw) -> dict:
        body = {"outcome": outcome, "reasons": "The Council has considered the decision.", "reference": "Council minute 1", "idempotency_key": self.key("rat"), **kw}
        r = (client or self.council).post(f"{self.url()}/ratification", body)
        assert r.status_code == expect, r.text
        return r.json()

    def close(self, expect: int = 200, client: Api | None = None) -> dict:
        r = (client or self.bureau).post(f"{self.url()}/close", {"row_version": self.rv()})
        assert r.status_code == expect, r.text
        return r.json()

    # ---- the institution ----
    def status(self, client: Api | None = None) -> dict:
        r = (client or self.w.a).get(f"/api/applications/{self.w.app['id']}/decision")
        assert r.status_code == 200, r.text
        return r.json()

    def appeal(self, grounds: str = "The institution is aggrieved by the decision and sets out its grounds here.", expect: int = 201, client: Api | None = None, key: str | None = None) -> dict:
        r = (client or self.w.a).post(f"/api/applications/{self.w.app['id']}/appeal", {"grounds": grounds, "idempotency_key": key or self.key("apl")})
        assert r.status_code == expect, r.text
        return r.json()

    def appeal_row(self) -> Appeal | None:
        with SessionLocal() as db:
            return db.scalar(select(Appeal).where(Appeal.application_id == uuid.UUID(self.w.app["id"])))

    # ---- SAC ----
    def sac_url(self) -> str:
        ap = self.appeal_row()
        assert ap is not None and ap.sac_case_id
        return f"/api/sac/cases/{ap.sac_case_id}"

    def sac_case(self, client: Api | None = None) -> dict:
        r = (client or self.sac_coord).get(self.sac_url())
        assert r.status_code == 200, r.text
        return r.json()

    def sac_select(self, key: str | None = None, expect: int = 200) -> dict:
        from sqlalchemy import select as sel

        from app.models import User

        with SessionLocal() as db:
            uid = str(db.scalar(sel(User.id).where(User.email == SAC_M1)))
        r = self.sac_coord.post(f"{self.sac_url()}/selection", {"user_ids": [uid], "idempotency_key": key or self.key("sel")})
        assert r.status_code == expect, r.text
        return r.json()

    def sac_recommend(self, option: str = "RECOMMEND", expect: int = 200) -> dict:
        d = self.sac_case(self.sac1)
        body = {"option": option, "sections": {"summary": "The SAC reviewed the appeal.", "rationale": "The reasons are recorded here."}, "row_version": d["sac"]["draft"]["row_version"]}
        r = self.sac1.put(f"{self.sac_url()}/recommendation", body)
        assert r.status_code == expect, r.text
        return r.json()

    def sac_submit(self, expect: int = 200) -> dict:
        d = self.sac_case(self.sac1)
        r = self.sac1.post(f"{self.sac_url()}/recommendation/submit", {"idempotency_key": self.key("srec"), "row_version": d["sac"]["draft"]["row_version"]})
        assert r.status_code == expect, r.text
        return r.json()

    def sac_complete(self, expect: int = 200) -> dict:
        d = self.sac_case()
        r = self.sac_coord.post(f"{self.sac_url()}/complete", {"summary": "SAC stage recorded as complete.", "row_version": d["row_version"]})
        assert r.status_code == expect, r.text
        return r.json()

    def council_decide_appeal(self, outcome: str = "LOA", expect: int = 201, client: Api | None = None, **kw) -> dict:
        ap = self.appeal_row()
        body = {"outcome": outcome, "reasons": "The Council considered the appeal and the SAC's recommendation.", "speaking_order": "Speaking order stating the reasons in full.", "reference": "Council minute 7",
                "idempotency_key": self.key("cad"), **kw}
        r = (client or self.council).post(f"/api/decisions/appeals/{ap.id}/council-decision", body)
        assert r.status_code == expect, r.text
        return r.json()

    def full_appeal_path(self) -> None:
        """Appeal submitted, reviewed by the SAC and decided by the Council (LoA on appeal)."""
        self.appeal()
        self.sac_select()
        self.sac_recommend()
        self.sac_submit()
        self.sac_complete()
        self.council_decide_appeal()


def make_dworld(route: str | list = "scrutiny_only", appeal_days: int | None = 7, **kw) -> DW:
    c = make_cworld(route, appeal_days=appeal_days, **kw)
    for role, email in ((Role.EXECUTIVE_COMMITTEE, EC), (Role.REGULATION_BUREAU, BUREAU), (Role.COUNCIL_MEMBER, COUNCIL), (Role.SAC_COORDINATOR, SAC_COORD), (Role.SAC_MEMBER, SAC_M1)):
        make_user(role, email)
    clients = {}
    for key, email in (("ec", EC), ("bureau", BUREAU), ("council", COUNCIL), ("sac_coord", SAC_COORD), ("sac1", SAC_M1)):
        a = Api()
        assert a.login(email).status_code == 200
        clients[key] = a
    return DW(c, **clients)
