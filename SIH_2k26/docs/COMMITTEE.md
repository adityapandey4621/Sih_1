# EVC and SHC committee processes, and the route engine (Phase 5)

> The route engine decides which process is required according to approved configuration. Authorised mechanisms determine EVC selection. Assigned members record observations and submit reports. SHC handles the compliance / hearing process and its recommendation. **The competent authority makes the later decision.** An EVC report or an SHC recommendation is a record, never an approval.

Code: `app/models_committee.py`, `app/services/{stages,selection,scheduling,evc,shc,committee_core,committee_policy,committee_texts}.py`, `app/committee_access.py`, `app/committee_presenters.py`, `app/routers/committee.py`, `app/domain.py` (state machines), `app/guards.py`. Frontend: `frontend/src/features/committee/`.

## What the official handbook says, and what this phase relies on

Read in the supplied official PDF (APH 2024-27, see `APH_VERIFICATION.md`). Only these clauses are relied on:

| Clause | Statement | Used for |
|---|---|---|
| Ch. I 1.8(a), 1.9(a) | Scrutiny / EVC members are selected through an **automated, portal-based process made available by AICTE** | The selection provider boundary: this system never chooses members |
| Ch. I 1.8 NOTE; 1.9(f), (g) | Scrutiny and EVC are conducted **online**; **offline** only in extraordinary circumstances (including Court directions) | Visit modes: ONLINE, and PHYSICAL only with a recorded circumstance |
| Ch. I 1.9(c) | The EVC verifies readiness against Annexure-3 and 4 and Chapter VI | The checklist source (**not yet encoded**, gap G-P5-2) |
| Ch. I 1.9(h) | The Scrutiny / Re-Scrutiny report and the **EVC report are made available to the institutions** through the portal | The institution sees the EVC report once the stage is completed (the timing is this product's convention, gap G-P5-7) |
| Ch. I 1.9, 2.23.2, Table 2.3 | The route differs per category; "AA" = as applicable depending on the outcome of previous steps | Conditional stages need a recorded human determination |
| Ch. II 2.23.3 | Institutions have a chance to submit compliance for shortcomings from scrutiny / re-scrutiny / EVC before the SHC; the SHC's recommendations go to the Executive Committee | SHC compliance, and the hand-off to the later decision stage |
| Ch. II 2.24 | An appeal goes to the SAC after the Executive Committee's decision | SAC is **not** a pre-decision stage and is not built here |

**Not stated by the handbook** (so nothing is encoded): a response period for SHC compliance; whether or how a hearing is held; a scheduling deadline; an SHC recommendation vocabulary; who coordinates visits. **Not verified**: the reported December 2025 corrigendum (EVC constitution authority, hybrid visits). The official AICTE pages returned HTTP 404 / connection reset on 2026-10-27, so a primary copy could not be retrieved; hybrid visits are therefore refused with that reason.

## The route engine (`services/stages.py`, `services/routing.py`)

Nothing activates because "scrutiny finished". The next stage is read from the application's **frozen** rule-version route (`resolve_after(config, stage_key)`):

| Route says | Result |
|---|---|
| one non-conditional next stage | `QUEUED`: an EVC / SHC case is created (idempotently) |
| next stage conditional (AA) or several options | `AWAITING_DETERMINATION`: an authorised coordinator records which applies, **with a reason** (or that a single conditional stage does not apply) |
| nothing after this stage | `NO_FURTHER_STAGE`: a **decision hand-off** is created (a record only), but only if every stage that was activated is complete |
| stage missing / duplicated / names a missing next stage | `EXCEPTION`: nothing is guessed; the case waits for configuration review (the catalogue also refuses to publish such a route) |

* After scrutiny the resolution is Phase 4's `stage_handoffs` row; after EVC or SHC it is a `route_transitions` row (unique per application and stage).
* EVC and SHC are independent: an EVC being required never makes an SHC required, and the reverse. Tests cover scrutiny-only, EVC-only, SHC-only, EVC then conditional SHC (applies / does not apply), and EVC then the combined `sac-shc-aa` stage.
* The combined Table 2.3 "SAC / SHC (AA)" route key `sac-shc-aa` activates **only the SHC process** (an appeal is a later, post-decision process).
* A case records why it exists: from-stage, route clause, determination, scrutiny outcome (`approved: false`), rule version and, for SHC, the shortcomings left open earlier.
* `create_decision_handoff` refuses while any committee case is incomplete or any transition is undetermined. Activation and hand-off are in the same transaction as the completion that caused them; `python -m app.reconcile_committee` recovers a stage that was named but never created.
* Convention (gap G-P5-8/9): stage keys `evc`, `shc` and `sac-shc-aa` identify the committee stages.

## EVC case

States (`domain.CState`, transitions in `COMMITTEE_TRANSITIONS`; changed only by compare-and-set `committee_core.transition`):

```
SELECTION_PENDING -> SELECTION_REQUESTED -> SCHEDULE_PENDING -> SCHEDULED -> VISIT_IN_PROGRESS -> REPORT_SUBMITTED -> COMPLETED
        ^   failed / cancelled request  |        ^   cancel / reschedule |   ^  open amendment (coordinator)
        |<------------------------------+        +----------------------+   +--- REPORT_SUBMITTED
        +---- reselect (from SCHEDULE_PENDING / SCHEDULED) ----
```

* **Selection** (below). A failed or unusable result leaves the case in `SELECTION_PENDING`.
* **Schedule**: propose, then confirm (a coordinator). Time is entered as local date-time plus IANA zone and stored as an instant plus the zone. Mode must be one the case's frozen policy lists; HYBRID is refused (unverified). PHYSICAL needs a location and the circumstance that justifies it. Rescheduling or cancelling closes the row with a reason and adds another (`supersedes_id`); the one live schedule per case is a partial unique index. Duplicate requests (idempotency key) return the same schedule. The only conflict detected is the same member holding another live schedule at the *exact same instant*; there is no availability data and no duration, so the system never claims anyone is free.
* **Visit**: an **assigned member** starts the visit, records one observation per checklist item (`OBSERVED`, `NOT_OBSERVED`, `EVIDENCE_UNAVAILABLE`, `UNCERTAIN`: no "compliant"), may cite the exact current document versions, keeps an **internal note** apart from the note that goes into the report, and may flag an item for the next determination. Observations are edited with compare-and-set row versions so nobody overwrites a colleague.
* **Evidence**: a file (PDF/PNG/JPEG, size-limited, generated storage key, same private storage; **not malware-scanned or text-processed**, and the UI says so) or a *link* to material held elsewhere (recorded, never fetched). Removal is soft.
* **Report**: one draft per case; sections come from the policy. Submission validates required observations, evidence requirements, required sections, and that every cited document is still the current version (`evidence_superseded` otherwise). It writes an **immutable** `SUBMITTED` version with a content hash and a snapshot (document versions and hashes, observations, evidence, visit, policy, rule version) in one transaction with the state change; a retry with the same key returns the same record; a second record cannot be created. A correction is a **new version**, opened by a coordinator with a reason (`open_amendment`). Earlier versions stay.
* **Completion** is a separate coordinator action; only then does the route engine run and the institution see the report (text and observation notes, never internal notes or evidence files).

## SHC case

```
PREPARATION -> AWAITING_COMPLIANCE -> COMPLIANCE_RECEIVED -> UNDER_REVIEW -> RECOMMENDATION_SUBMITTED -> COMPLETED
      \--(only if the policy does not permit compliance)--/        ^   further round (new requirements + request)   \-- open amendment --> UNDER_REVIEW
```

* The coordinator prepares **requirements** in institution-facing words (internal scrutiny / EVC notes are shown only as pointers). A source reference may be chosen only from the case's frozen rule version, otherwise it is flagged "no source reference established".
* **Request compliance** creates a round and freezes its requirements (ORM guard). Whether compliance is permitted comes from the policy (`PERMITTED`; the handbook's 2.23.3 supports it). The response period is **not** established by any source, so no due date exists: the round stores `NO_DURATION`.
* The institution answers each requirement with an explanation and/or a corrected file (a **new document version** through the Phase 3 pipeline, originals kept) and submits **once** (idempotent). It is *received*, never accepted: no finding is created.
* Members (assigned through the selection boundary) record **findings** (`EVIDENCE_SUPPORTS`, `EVIDENCE_DOES_NOT_SUPPORT`, `INCONCLUSIVE`, with reasons; deliberately not "accepted"), append-only **hearing / sitting records** (naming a confirmed hearing marks it as having taken place), and a versioned **recommendation** (options and sections from the policy). Submission requires every requirement to be assessed, no open round, no pending hearing, and a held hearing if the policy makes one required.
* A hearing is scheduled with the same service as a visit and only if the policy establishes that a hearing is part of the process.
* The institution never sees findings or the recommendation, only its compliance requests, its own responses and the hearing schedule.

## Selection provider contract (`services/selection.py`)

```
provider.availability(config, settings) -> Availability(available, mode, label, notice, demo)
provider.submit(db, case, requested_ids, actor) -> ProviderResult(status, member_ids, external_ref, error_*)
provider.poll(db, request) -> ProviderResult | None          # None = still pending
```

Persisted for every request: `request_ref`, provider/mode, `is_demo`, status (`REQUESTED`, `COMPLETED`, `FAILED`, `INCOMPLETE`, `CANCELLED`), requester, times, result source, external reference, the members, any error, and the rule version and committee policy it was made under. A result is applied only if every member verifies as an active user of the right role and the configured minimum is met; otherwise `INCOMPLETE` and **no assignment exists**. `python -m app.reconcile_committee` polls pending requests.

* **DemoProvider**: only for DEMO-classified rule sets with demo rules enabled; a coordinator records fictional members; everything is flagged `is_demo` and the UI states it is not the official process.
* **ExternalPortalProvider**: the real mechanism. **Not connected**: it reports itself unavailable, makes no call and claims none (`409 selection_unavailable`, audited). A real adapter replaces it by implementing the two methods.
* Tests use a scripted double (`selection.PROVIDER_OVERRIDE`); production code never sets it.

## Configuration (`committee_policies`, `services/committee_policy.py`)

Visit modes, composition minimum, the visit checklist, report sections, SHC compliance / hearing / recommendation options are **data**, versioned with sources and a classification, frozen into the case at activation (a later policy never changes a running case). Only a **DEMO** policy exists (fictional checklist and composition, labelled). For a non-demo case no policy applies: the stage still activates (the route requires it), the case shows the blocker, a configuration gap is recorded and notified, and scheduling / visit / compliance are refused until a verified policy exists. Publishing an OFFICIAL policy has no interface yet (seed or script only).

## Roles and permissions (`app/rbac.py`, scope in `app/committee_access.py`)

| Role | Permissions | Scope |
|---|---|---|
| `evc_coordinator` | `evc:queue`, `evc:coordinate`, `evidence:read` | every EVC case; documents of those cases |
| `evc_member` | `evc:queue`, `evc:work`, `evidence:read` | only cases with an **active assignment** |
| `shc_coordinator` (new) | `shc:queue`, `shc:coordinate`, `evidence:read` | every SHC case |
| `shc_member` | `shc:queue`, `shc:work`, `evidence:read` | only assigned cases |
| `institute_user` | `shc:respond`, `committee:status_own` (+ existing) | its own applications |
| auditor, platform / rules roles, Executive Committee, SAC, scrutiny roles | none of the above | no access; documents of a case are read only through scope |

Out-of-scope access answers 404 and is audited (`committee.access_denied`); a role-by-endpoint matrix test calls every endpoint as every role over HTTP.

## Notifications and deadlines

Persistent in-app notifications (idempotent by `dedupe_key`, savepoint on PostgreSQL, failures audited). Events: stage activated, selection requested / recorded / failed, schedule proposed / confirmed / changed, visit started (report awaited), report / recommendation submitted, stage completed or blocked, compliance requested / submitted, next stage needs a determination, ready for the decision stage. `external_delivery = NOT_CONFIGURED`: no email or SMS exists and none is claimed. "Upcoming visit" is shown on the queue from the stored schedule; **no reminder scheduler exists** (Phase 8). No due dates are invented.

## Hand-off to the decision stage

`decision_handoffs` (one per application, status `READY_FOR_DECISION_STAGE`): rule version, the route as followed, every completed stage with its case, completion time and report / recommendation id, version and hash, and `decided: false`. It is listed for coordinators at `GET /api/committee/decision-handoffs`. Executive Committee decisions, SAC appeals and post-approval compliance are **not** implemented.

## Running it

```powershell
cd backend
.\.venv\Scripts\python -m alembic upgrade head          # includes 0005
.\.venv\Scripts\python -m app.seed                       # demo users incl. shc.coordinator@, evc.coordinator@, evc.member@, shc.member@
.\.venv\Scripts\python -m app.seed_rules                 # also publishes the DEMO committee policies
.\.venv\Scripts\python -m uvicorn app.main:app --port 8000
.\.venv\Scripts\python -m app.worker                     # document processing (uploads and compliance evidence)
.\.venv\Scripts\python -m app.reconcile_committee        # optional: recover un-activated stages and pending selection results
cd ..\frontend; npm run dev
```

## Verification gaps and limits

* **G-P5-1** The December 2025 corrigendum (EVC constitution authority; hybrid visits) is unverified: the official pages could not be retrieved. Hybrid visits are refused; the selection authority is not assumed.
* **G-P5-2** The EVC checklist source (Annexure-3 / 4 and Chapter VI) is not encoded; only a labelled DEMO checklist exists. No OFFICIAL committee policy exists, so non-demo cases are blocked at scheduling.
* **G-P5-3** Committee composition and quorum (the handbook's committees annexure) are not encoded; the demo minimum of one member is invented and labelled.
* **G-P5-4** The portal's automated selection process has no interface; only the boundary and a labelled demonstration exist.
* **G-P5-5** The handbook states no response period for SHC compliance and no hearing format; none is encoded (`NO_DURATION`; hearing `OPTIONAL` in the demo policy only).
* **G-P5-6** No scheduling deadline, notice period or visit duration exists, so overlap and delay cannot be computed.
* **G-P5-7** When the institution may see the EVC report is not stated; this product releases it at stage completion (a convention, per cl. 1.9(h) that reports are made available).
* **G-P5-8** The coordinator determines conditional next stages and authorises amendments: an application capability, not a claimed AICTE designation.
* **G-P5-9** Stage keys `evc` / `shc` / `sac-shc-aa` are conventions in the catalogue.
* Committee evidence files are not malware-scanned or text-processed. There is no retention or encryption-at-rest policy. A single compliance round is answered once; a further round is a new request. Removing a member from an SHC case (as opposed to adding) has no interface.
