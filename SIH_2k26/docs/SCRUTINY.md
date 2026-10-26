# Authorised scrutiny, formal notices and re-scrutiny (Phase 4)

> Automated checks identify potential problems. Authorised scrutiny personnel determine findings and decide whether to issue formal deficiency notices. Institutions respond with evidence. Re-scrutiny examines that evidence. Completing scrutiny advances the case to its next permitted stage; it does not grant approval.

Code: `app/models_scrutiny.py`, `app/services/{scrutiny,notices,responses,policy,routing,notifications,scrutiny_texts}.py`, `app/scrutiny_access.py`, `app/scrutiny_presenters.py`, `app/routers/{scrutiny,notices}.py`, `app/domain.py` (state machines), `app/guards.py`. Frontend: `frontend/src/features/scrutiny/`.

## Three kinds of record, never conflated

| Stage | Record | Made by | Visible to the institution |
|---|---|---|---|
| Automated preliminary issue | `preliminary_findings` (Phase 3, `PRELIMINARY_ISSUE` only) | the worker | its own plain wording only |
| Reviewer finding | `scrutiny_findings` | an assigned reviewer, by a deliberate action | never as such; only the wording written for a notice, once issued |
| Formal deficiency notice | `deficiency_notices` + `notice_items` | an authorised issuer (`notice:issue`), after inspecting the exact content | yes, once `ISSUED` |

No processing module can reach the notice tables (asserted by a test that reads the source), and nothing in an automated path creates a finding or notice.

## State machines (all in `app/domain.py`; changed only by compare-and-set transition functions)

**Case** (`scrutiny_cases.state`; the application stays `DRAFT`/`SUBMITTED`):
`AWAITING_ASSIGNMENT → READY_FOR_SCRUTINY → UNDER_SCRUTINY → AWAITING_NOTICE_DECISION → AWAITING_RESPONSE → AWAITING_RESCRUTINY → UNDER_RESCRUTINY → SCRUTINY_COMPLETED`, plus `CONFIGURATION_EXCEPTION`. A returned or withdrawn notice goes back to `UNDER_SCRUTINY` / `UNDER_RESCRUTINY`. Undefined moves are refused (`409 invalid_transition`); a stale view is refused (`409 stale_version`). An ORM guard forbids assigning `state` directly.

**Notice**: `DRAFT → IN_REVIEW → ISSUED → RESPONSE_RECEIVED → CLOSED`; `DRAFT/IN_REVIEW → WITHDRAWN`; `IN_REVIEW → DRAFT` (returned by the issuer). Once `ISSUED`, the notice, its items and its frozen content are immutable (ORM guard + no endpoint).

**Finding**: `OPEN → NOTICED → RESOLVED | UNRESOLVED | NOT_SUPPORTED`; `OPEN → RESOLVED | NOT_SUPPORTED`; `UNRESOLVED → NOTICED | RESOLVED | NOT_SUPPORTED`. `RESOLVED` and `NOT_SUPPORTED` are final. Every conclusion needs a reason and is recorded with its round.

**Rounds**: round 1 is initial scrutiny; each submitted response opens a `RE_SCRUTINY` round, in which reviewers conclude the existing findings but cannot record new ones (handbook cl. 1.8(g)). Earlier rounds are never overwritten (`NOTICE_ISSUED`/`COMPLETED`). Reviewer working notes are per round and internal.

## Who can do what (`app/rbac.py`, then case scope in `app/scrutiny_access.py`)

| Role | Capabilities | Scope |
|---|---|---|
| `scrutiny_coordinator` | queue, assign, determine the next stage when the route needs it | every case's metadata; **no** document contents, findings or notices |
| `scrutiny_officer` (reviewer) | evidence, findings, notices (draft / submit / withdraw), complete a round | only cases with an active assignment |
| `notice_issuer` | inspect and issue / return a prepared notice | only cases that have a notice submitted for issuance |
| `institute_user` | read own notices, upload corrected evidence, respond | own institution only |
| `auditor` | audit log only | no document access |
| EVC, SHC, Executive Committee, SAC, rules roles | none of the above | — |

These are *application capabilities*, not claims about official AICTE designations. To change who may issue notices, edit `ROLE_PERMISSIONS` (`notice:issue`); issuance is never granted implicitly to a reviewer.

## The assignment boundary

`services/scrutiny.selection_info` decides which mechanism may put a reviewer on a case:
- **DEMO_MANUAL** — only for a case whose rule set is `DEMO`-classified, in a deployment where demo rules are enabled. The coordinator picks a fictional reviewer; the record is `is_demo`, names who made it and under which rule version, and the UI and notification say it is *not* the official selection.
- **OFFICIAL_SELECTION** — no interface exists, so assignment is refused (`409 selection_unavailable`). The data model (`source`, `made_by_id` null, `selection_ref`) is ready for an authorised interface to deliver a result.

## Response period: no invented dates (`services/policy.py`)

The official handbook states no response period for these deficiencies (`APH_VERIFICATION.md`). A policy that records exactly that (no duration, verified source) is *settled*: it sets no due date and does not block issuance. A missing, ambiguous or unverified policy still blocks a non-demo notice.

A notice's due date exists only if exactly one policy with trigger `deficiency_notice_issued` states a duration and unit, counts in `CALENDAR` convention, and cites a source that is `VERIFIED_OFFICIAL` (or `DEMO_ONLY` on a `DEMO` case where demo rules are on). Otherwise: `NO_DURATION`, `MISSING`, `AMBIGUOUS`, `UNSUPPORTED` (working days, or no convention), or `UNVERIFIED`.
- A **demo** case may still issue, with no due date and a clear "no deadline established".
- A **non-demo** case cannot issue while the policy is unsettled (missing, ambiguous, unverified, working-day): it is blocked (`409`), the refusal is audited, and a configuration gap (`incomplete`) is recorded. It is also blocked while no approved notice template exists (none does), so a non-demo notice is still not issuable today.
- The policy is read from the application's **frozen** configuration and the result is stored on the notice, so a later rule version never moves an existing date or rebinds a case.
- Lateness is recorded (`after_due_date`) but not enforced: no verified late-response rule exists.

## Issuance

`POST /api/scrutiny/notices/{id}/issue` (issuer only) requires the content hash of exactly what was inspected, a recorded reason, acknowledgement if any item has no established source, and an idempotency key. In one transaction it freezes the content, records the authority and policy resolution, marks the findings `NOTICED`, closes the round, moves the case, writes events and audit, and creates the institution's notifications. A repeated request with the same key returns the same notice (`X-Idempotent-Replay`); a different key is `409 already_issued`; two partial unique indexes make a second live or in-preparation notice per case impossible. If anything fails nothing is issued (tested).

## Source references

A finding's reference is chosen from the case's own frozen rule version (a source, or a checklist item's clause). It is never free text. If none applies the finding is `NEEDS_VERIFICATION` and the notice says "No source reference has been established".

## Corrected evidence

Corrected files reuse the Phase 3 upload pipeline through a narrow `correction=True` mode, reachable only through an issued, open notice item that names a document requirement. A new version is added and the previous one superseded; a guard allows exactly that on a submitted application (a new `ApplicationDocument` row and `superseded_at`) and nothing else. Earlier versions and the submission snapshot are untouched. Duplicate bytes add no version.

## Completion and hand-off

Completion needs: no `OPEN`/`NOTICED` finding, no notice in preparation, no document still being processed, a recorded summary, and explicit acknowledgement if findings are `UNRESOLVED` (they are recorded and carried forward). The next stage is read from the frozen route by `services/routing.py`:

| Route | Result |
|---|---|
| no scrutiny stage, or unusable | `CONFIGURATION_EXCEPTION` (never guessed) |
| next stage not conditional | `QUEUED` to that stage (whatever it is) |
| none after scrutiny | `NO_FURTHER_STAGE` |
| conditional, or several options | `AWAITING_DETERMINATION`: a coordinator chooses among the route's own stages, with a reason |

Nothing is started in the next stage, and the application is never marked approved or rejected.

## Notifications (`notifications`)

Persistent in-app rows, idempotent by `dedupe_key`, written inside a savepoint (PostgreSQL) so a failure never undoes the event that caused it; the failure is audited and `python -m app.reconcile_notifications` re-creates missing issued-notice notifications. `delivery_state = STORED_IN_APP`, `external_delivery = NOT_CONFIGURED`: no email or SMS exists and none is claimed. Events: case entered queue, assigned, notice awaiting issuance, notice issued, response submitted, ready for re-scrutiny, scrutiny completed, handed off.

## Running it

```powershell
cd backend
.\.venv\Scripts\python -m alembic upgrade head          # includes 0004; existing submitted applications get a case
.\.venv\Scripts\python -m app.seed                       # demo users incl. scrutiny.coordinator@ and notice.issuer@
.\.venv\Scripts\python -m app.seed_rules
.\.venv\Scripts\python -m uvicorn app.main:app --port 8000
.\.venv\Scripts\python -m app.worker                     # document processing (needed for uploads and corrections)
cd ..\frontend; npm run dev
```

## Verification gaps and limits (nothing here is verified against the official handbook)

- **G-P4-1 (closed 2026-10-27, see `APH_VERIFICATION.md`)** The official handbook PDF (2024-27) was supplied and read. It states **no** response period for scrutiny deficiencies (cl. 1.8(b), (f), (g)), so none is encoded; the 7-day appeal windows are a different event. Currency against later corrigenda is still open (G-P4-6).
- **G-P4-2** The handbook (cl. 1.8(f)) says deficiencies are communicated through the web portal on the Scrutiny Committee's recommendation, and does not name who issues them. The product still models it as the separate `notice:issue` capability. The handbook's "Letter of Deficiency" (cl. 1.10) is an Executive Committee output, a different instrument that this product does not produce.
- **G-P4-3** The scrutiny stage is identified by the route-stage key `scrutiny` (a convention in the catalogue and seed data, not a verified fact).
- **G-P4-4** No approved notice template, signature or seal exists; only a labelled demo template. Counting conventions (does the issue day count, cut-off times) for due dates are unverified.
- **G-P4-5** The handbook directs queries and clarifications to the CSS portal only (Ch. VIII item 12) and states no cap on scrutiny rounds (Annexure-7 draws one re-scrutiny). Neither a clarification channel nor a maximum is implemented; rounds are uncapped. Re-scrutiny verifies only the deficiencies already raised (cl. 1.8(g)): new findings are refused in that round.
- **G-P4-6** The December 2025 corrigendum (EVC / SAC constitution) is still unverified from a primary source.
- Notice items can request files only for a checklist **document requirement** of the frozen rule version; arbitrary extra attachments are not supported.
- Stored text and responses have no retention policy or encryption at rest (unchanged from Phase 3).
- Not tested: real ClamAV, Docker, multi-instance deployment, very large queues (queue rows are summarised per page with a few queries each).
