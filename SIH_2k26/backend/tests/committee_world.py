"""Scenario helpers for the EVC / SHC tests: a published DEMO rule version whose route is chosen per test, the committee roles, and the usual steps."""

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.db import SessionLocal
from app.models import User
from app.models_committee import CommitteeCase
from app.rbac import Role
from app.schemas_catalogue import RouteStageIn
from app.services import committee_policy
from tests.conftest import Api, make_user
from tests.evidence_world import World, drain, make_world

EVC_COORD, EVC_M1, EVC_M2 = "evc.coord@test.example.org", "evc.m1@test.example.org", "evc.m2@test.example.org"
SHC_COORD, SHC_M1, SHC_M2 = "shc.coord@test.example.org", "shc.m1@test.example.org", "shc.m2@test.example.org"


def stage(key: str, label: str, nxt: list[str], conditional: bool = False, order: int = 0) -> RouteStageIn:
    return RouteStageIn(stage_key=key, label=label, display_order=order, is_conditional=conditional, condition_description="Table 2.3 AA: depends on earlier outcomes." if conditional else None, next_stage_keys=nxt)


ROUTES = {
    # scrutiny -> EVC (required) -> SHC (conditional, "AA")
    "evc_then_shc_conditional": [stage("scrutiny", "Scrutiny", ["evc"], order=0), stage("evc", "EVC", ["shc"], order=1), stage("shc", "SHC", [], conditional=True, order=2)],
    # scrutiny -> EVC (conditional): the default test route
    "evc_conditional": [stage("scrutiny", "Scrutiny", ["evc"], order=0), stage("evc", "EVC", [], conditional=True, order=1)],
    # scrutiny -> EVC (required) -> nothing more
    "evc_only": [stage("scrutiny", "Scrutiny", ["evc"], order=0), stage("evc", "EVC", [], order=1)],
    # scrutiny -> nothing more
    "scrutiny_only": [stage("scrutiny", "Scrutiny", [], order=0)],
    # scrutiny -> SHC (required) -> nothing more
    "shc_only": [stage("scrutiny", "Scrutiny", ["shc"], order=0), stage("shc", "SHC", [], order=1)],
    # scrutiny -> EVC (required) -> the combined Table 2.3 "SAC / SHC (AA)" stage
    "evc_then_combined": [stage("scrutiny", "Scrutiny", ["evc"], order=0), stage("evc", "EVC", ["sac-shc-aa"], order=1), stage("sac-shc-aa", "SAC / SHC (as applicable)", [], conditional=True, order=2)],
    # a route that names a stage that does not exist
    "broken": [stage("scrutiny", "Scrutiny", ["evc"], order=0)],
}


def future(days: int = 3, hour: int = 10) -> str:
    return (datetime.now(UTC) + timedelta(days=days)).strftime(f"%Y-%m-%dT{hour:02d}:00")


def past(hours: int = 3) -> str:
    return (datetime.now(UTC) - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M")


@dataclass
class CW:
    w: World
    evc_coord: Api = None
    evc1: Api = None
    evc2: Api = None
    shc_coord: Api = None
    shc1: Api = None
    shc2: Api = None
    n: int = field(default=0)

    # ---- plumbing ----
    def key(self, prefix: str = "k") -> str:
        self.n += 1
        return f"{prefix}-{uuid.uuid4().hex[:12]}"

    def user_id(self, email: str) -> str:
        with SessionLocal() as db:
            return str(db.scalar(select(User.id).where(User.email == email)))

    def row(self, stage: str) -> CommitteeCase | None:
        with SessionLocal() as db:
            return db.scalar(select(CommitteeCase).where(CommitteeCase.application_id == uuid.UUID(self.w.app["id"]), CommitteeCase.stage == stage))

    def url(self, stage: str) -> str:
        r = self.row(stage)
        assert r is not None, f"no {stage} case exists"
        return f"/api/{stage.lower()}/cases/{r.id}"

    def case(self, stage: str, client: Api | None = None) -> dict:
        c = client or (self.evc_coord if stage == "EVC" else self.shc_coord)
        r = c.get(self.url(stage))
        assert r.status_code == 200, r.text
        return r.json()

    def rv(self, stage: str) -> int:
        return self.row(stage).row_version

    # ---- scrutiny to the hand-off ----
    def finish_scrutiny(self, *, determine: str | None | bool = False, summary: str = "Scrutiny finished without open findings.", official: bool = False) -> dict:
        """Upload, submit, assign a reviewer, start and complete scrutiny. `determine`: a stage key (or None) the coordinator then determines, when the route needs it.
        `official=True` is for a non-demo rule set, where the scrutiny reviewer can only arrive through the (test double of the) authorised selection."""
        w = self.w
        w.upload()
        drain()
        w.submit(assign=not official)
        if official:
            official_scrutiny_assign(w)
        drain()
        w.start()
        r = w.scrutiny.post(f"{w.case_url}/complete", {"row_version": w.case_row().row_version, "summary": summary})
        assert r.status_code == 200, r.text
        if determine is not False:
            d = w.coordinator.post(f"{w.case_url}/handoff/determine", {"stage_key": determine, "reason": "Determined for the test."})
            assert d.status_code == 200, d.text
        return r.json()

    def evc_select(self, who: tuple[str, ...] = (EVC_M1,), key: str | None = None, expect: int = 200) -> dict:
        body = {"user_ids": [self.user_id(e) for e in who], "idempotency_key": key or self.key("sel")}
        r = self.evc_coord.post(f"{self.url('EVC')}/selection", body)
        assert r.status_code == expect, r.text
        return r.json()

    def schedule(self, stage: str = "EVC", client: Api | None = None, expect: int = 201, **kw) -> dict:
        body = {"start_local": future(), "timezone": "Asia/Kolkata", "mode": "ONLINE", "access_details": "Joining instructions will be shared by the coordinator.", "idempotency_key": self.key("sch"), **kw}
        r = (client or (self.evc_coord if stage == "EVC" else self.shc_coord)).post(f"{self.url(stage)}/schedule", body)
        assert r.status_code == expect, r.text
        return r.json()

    def confirm(self, stage: str = "EVC", expect: int = 200) -> dict:
        d = self.case(stage)
        sch = d["schedule"]["active"]
        c = self.evc_coord if stage == "EVC" else self.shc_coord
        r = c.post(f"{self.url(stage)}/schedule/{sch['id']}/confirm", {"row_version": sch["row_version"]})
        assert r.status_code == expect, r.text
        return r.json()

    def ready_visit(self) -> dict:
        """EVC case selected, scheduled and confirmed; returns the case."""
        self.evc_select()
        self.schedule()
        return self.confirm()

    def start_visit(self, client: Api | None = None, expect: int = 200) -> dict:
        r = (client or self.evc1).post(f"{self.url('EVC')}/visit/start", {"row_version": self.rv("EVC")})
        assert r.status_code == expect, r.text
        return r.json()

    def observe(self, item: str, status: str = "OBSERVED", note: str = "Seen as described.", client: Api | None = None, expect: int = 200, **kw) -> dict:
        d = self.case("EVC", client or self.evc1)
        cur = next((o for o in d["evc"]["observations"] if o["item_key"] == item), None)
        body = {"status": status, "note": note, "row_version": cur["row_version"] if cur else None, **kw}
        r = (client or self.evc1).put(f"{self.url('EVC')}/observations/{item}", body)
        assert r.status_code == expect, r.text
        return r.json()

    def evidence_pdf(self, item: str | None = "demo-equipment", client: Api | None = None, expect: int = 201) -> dict:
        from tests.docs_helpers import digital_pdf

        r = (client or self.evc1).http.post(
            f"{self.url('EVC')}/evidence", files={"file": ("photo-list.pdf", digital_pdf(["Equipment list seen at the visit."]), "application/pdf")}, data={"item_key": item or "", "description": "Equipment list"},
            headers=(client or self.evc1)._csrf())
        assert r.status_code == expect, r.text
        return r.json()

    def complete_checklist(self, with_evidence: bool = True) -> None:
        self.observe("demo-premises", note="Premises match the application.")
        if with_evidence:
            self.evidence_pdf("demo-equipment")
        self.observe("demo-equipment", note="Listed equipment seen.")

    def save_report(self, summary: str = "The visit took place as scheduled.", client: Api | None = None, expect: int = 200, **sections) -> dict:
        c = client or self.evc1
        d = self.case("EVC", c)
        r = c.put(f"{self.url('EVC')}/report", {"sections": {"summary": summary, **sections}, "row_version": d["evc"]["draft"]["row_version"]})
        assert r.status_code == expect, r.text
        return r.json()

    def submit_report(self, key: str | None = None, client: Api | None = None, expect: int = 200) -> dict:
        c = client or self.evc1
        d = self.case("EVC", c)
        r = c.post(f"{self.url('EVC')}/report/submit", {"idempotency_key": key or self.key("rep"), "row_version": d["evc"]["draft"]["row_version"]})
        assert r.status_code == expect, r.text
        return r.json()

    def complete_evc(self, summary: str = "EVC stage recorded as complete.", expect: int = 200) -> dict:
        r = self.evc_coord.post(f"{self.url('EVC')}/complete", {"summary": summary, "row_version": self.rv("EVC")})
        assert r.status_code == expect, r.text
        return r.json()

    def evc_to_report(self) -> dict:
        """From a scheduled EVC case to a submitted report."""
        self.start_visit()
        self.complete_checklist()
        self.save_report()
        return self.submit_report()

    # ---- SHC ----
    def shc_select(self, who: tuple[str, ...] = (SHC_M1,), key: str | None = None, expect: int = 200) -> dict:
        body = {"user_ids": [self.user_id(e) for e in who], "idempotency_key": key or self.key("sel")}
        r = self.shc_coord.post(f"{self.url('SHC')}/selection", body)
        assert r.status_code == expect, r.text
        return r.json()

    def requirement(self, title: str = "Show the validity period", expect: int = 201, **kw) -> dict:
        body = {"title": title, "description": "Please show the validity period of the affiliation letter.", "requested_evidence": "A letter stating its validity.", "item_key": "doc-main", **kw}
        r = self.shc_coord.post(f"{self.url('SHC')}/requirements", body)
        assert r.status_code == expect, r.text
        return r.json()

    def request_compliance(self, instructions: str = "Please respond to each point.", expect: int = 200) -> dict:
        r = self.shc_coord.post(f"{self.url('SHC')}/compliance/request", {"instructions": instructions, "row_version": self.rv("SHC")})
        assert r.status_code == expect, r.text
        return r.json()

    def round_id(self) -> str:
        return self.case("SHC")["shc"]["rounds"][-1]["id"]

    def respond(self, text: str = "The corrected letter is enclosed.", upload: bool = True, client: Api | None = None, key: str | None = None) -> dict:
        from tests.catalogue_helpers import MAIN_TEXT
        from tests.docs_helpers import digital_pdf

        a = client or self.w.a
        rid = self.round_id()
        d = a.get(f"/api/compliance/{rid}").json()
        req = d["requirements"][0]
        if upload:
            r = a.upload(f"/api/compliance/{rid}/requirements/{req['id']}/documents", "file", "compliance.pdf", digital_pdf([MAIN_TEXT, ["Valid until: 31/03/2030"]]))
            assert r.status_code in (200, 201), r.text
        d = a.get(f"/api/compliance/{rid}").json()
        r = a.put(f"/api/compliance/{rid}/response", {"explanation": "Please see the enclosed evidence.", "items": [{"requirement_id": req["id"], "explanation": text}], "row_version": d["response"]["row_version"] if d["response"] else None})
        assert r.status_code == 200, r.text
        d = r.json()
        r = a.post(f"/api/compliance/{rid}/response/submit", {"idempotency_key": key or "comp-key-0001", "row_version": d["response"]["row_version"]})
        assert r.status_code == 200, r.text
        return r.json()

    def start_review(self, expect: int = 200) -> dict:
        r = self.shc_coord.post(f"{self.url('SHC')}/review/start", {"row_version": self.rv("SHC")})
        assert r.status_code == expect, r.text
        return r.json()

    def shc_finding(self, assessment: str = "EVIDENCE_SUPPORTS", text: str = "The letter now states its validity period.", client: Api | None = None, expect: int = 201, requirement_id: str | None = None) -> dict:
        c = client or self.shc1
        rid = requirement_id or self.case("SHC")["shc"]["rounds"][-1]["requirements"][0]["id"]
        r = c.post(f"{self.url('SHC')}/findings", {"requirement_id": rid, "assessment": assessment, "text": text})
        assert r.status_code == expect, r.text
        return r.json()

    def recommend(self, option: str = "RECOMMEND_NEXT_STAGE", client: Api | None = None, expect: int = 200, **sections) -> dict:
        c = client or self.shc1
        d = self.case("SHC", c)
        body = {"option": option, "sections": {"summary": "The review is complete.", "rationale": "The evidence addresses the requirement.", **sections}, "row_version": d["shc"]["draft"]["row_version"]}
        r = c.put(f"{self.url('SHC')}/recommendation", body)
        assert r.status_code == expect, r.text
        return r.json()

    def submit_recommendation(self, key: str | None = None, client: Api | None = None, expect: int = 200) -> dict:
        c = client or self.shc1
        d = self.case("SHC", c)
        r = c.post(f"{self.url('SHC')}/recommendation/submit", {"idempotency_key": key or self.key("rec"), "row_version": d["shc"]["draft"]["row_version"]})
        assert r.status_code == expect, r.text
        return r.json()

    def complete_shc(self, expect: int = 200) -> dict:
        r = self.shc_coord.post(f"{self.url('SHC')}/complete", {"summary": "SHC stage recorded as complete.", "row_version": self.rv("SHC")})
        assert r.status_code == expect, r.text
        return r.json()

    def shc_to_review(self) -> None:
        """SHC case with one requirement answered, members assigned, review started."""
        self.shc_select()
        self.requirement()
        self.request_compliance()
        self.respond()
        self.start_review()


def official_scrutiny_assign(w: World) -> None:
    """What an authorised selection interface would deliver for scrutiny. (No such interface exists; this is a test double.)"""
    from app.models_scrutiny import ScrutinyAssignment, ScrutinyCase
    from app.services import scrutiny as sc
    from app.domain import CaseAction

    with SessionLocal() as db:
        case = db.scalar(select(ScrutinyCase))
        uid = db.scalar(select(User.id).where(User.email == "scrutiny@test.example.org"))
        db.add(ScrutinyAssignment(case_id=case.id, user_id=uid, source="OFFICIAL_SELECTION", is_demo=False, made_by_id=None, made_by_label="authorised-selection-interface (test double)", selection_ref={"external_ref": "SEL-1"}))
        db.flush()
        sc.transition(db, case, CaseAction.ASSIGN)
        db.commit()


def make_cworld(route: str | list[RouteStageIn] = "evc_conditional", policies: bool = True, **kw) -> CW:
    stages_ = ROUTES[route] if isinstance(route, str) else route
    w = make_world(route_stages=stages_, **kw)
    for role, email in ((Role.EVC_COORDINATOR, EVC_COORD), (Role.EVC_MEMBER, EVC_M1), (Role.EVC_MEMBER, EVC_M2), (Role.SHC_COORDINATOR, SHC_COORD), (Role.SHC_MEMBER, SHC_M1), (Role.SHC_MEMBER, SHC_M2)):
        make_user(role, email)
    clients = {}
    for key, email in (("evc_coord", EVC_COORD), ("evc1", EVC_M1), ("evc2", EVC_M2), ("shc_coord", SHC_COORD), ("shc1", SHC_M1), ("shc2", SHC_M2)):
        c = Api()
        assert c.login(email).status_code == 200
        clients[key] = c
    if policies:
        with SessionLocal() as db:
            committee_policy.seed_demo_policies(db)
            db.commit()
    return CW(w, **clients)
