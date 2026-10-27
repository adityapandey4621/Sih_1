# Architecture

```
Browser (Vite SPA, React, TypeScript)
   │  same-origin /api  (Vite proxy in dev, reverse proxy in prod)
   ▼
FastAPI backend ── routers → services → SQLAlchemy models ── PostgreSQL
   │                 │
   │                 └─ rbac.py   (single source of truth for roles → permissions)
   └─ future: email sender, identity provider, AICTE integrations (behind interfaces)
```

## Boundaries

- **Frontend** never decides who a user is. It asks `GET /api/auth/session` and receives the user's role and **permission list**. Navigation, route guards and dashboard selection are derived from those permissions. The backend re-checks every protected call.
- **Backend** owns identity, sessions, permissions, access requests, invitations and the audit log. Routers are thin; security primitives live in `security.py`, permissions in `rbac.py`, request authorisation in `deps.py`.
- **Database** PostgreSQL is authoritative. Schema changes go through Alembic only. Enumerated values are stored as validated strings (not native enums) so adding a role/status is a code change, not a painful migration.
- **Services** (`app/services`) hold business rules and never import FastAPI: `rules` (authoring, gates, snapshots, selection, lifecycle), `applications` (intake, uploads, submission), `checklist` (pure evaluation against a frozen snapshot). Routers are thin; `presenters` shape responses; `guards` is a second, flush-time line of defence for immutability.
- **Attachments** go through `storage.LocalStorage` (private directory, generated keys, atomic writes, never overwrite). Replace it with object storage behind the same interface in a later phase.
- **Integration seams** (not built): `services/email.py` defines an `EmailSender` protocol with a `NotConfiguredSender`; identity is reached only through `deps.get_current_user`, so a future SSO/IdP replaces session lookup without touching dashboards.

## Authentication design

- Opaque 256-bit session token in cookie `sih_session` (`HttpOnly`, `SameSite=Lax`, `Secure` in production, `Path=/`). Only a SHA-256 hash of the token is stored (`sessions.token_hash`).
- Idle timeout and absolute lifetime are configurable; both are enforced server-side on every request. Logout revokes the row.
- CSRF: double-submit token. Cookie `sih_csrf` (JS-readable) must equal header `X-CSRF-Token` on every non-safe method; `Origin` is also checked against allowed origins when present.
- Login throttling is stored in `login_attempts` (per normalised email and per IP). Throttling applies equally to unknown and known emails; the failure message is uniform. A dummy Argon2 verification equalises timing for unknown users.
- Roles are only assigned by an administrator via invitation. The registration form creates an `access_request` row, never a user.

## Permission model

`rbac.py` maps each `Role` to a set of `Permission` strings, e.g. `dashboard:scrutiny`, `admin:users`, `audit:read`. A role never implies another role's permissions. `rules_admin` has no decision permissions; `auditor` has only read permissions. Phase 2+ adds permissions here; the UI picks them up through the same `permissions` array.

## Data model

**Phase 1:** `institutions`, `users`, `sessions`, `login_attempts`, `access_requests`, `invitations`, `audit_events`.

**Phase 2 — rule catalogue:** `rule_sources`, `application_categories`, `rule_sets`, `rule_versions` (+ `rule_version_sources`, `checklist_items`, `route_stages`, `deadline_policies`, `rule_version_events`), `configuration_gaps`. See `RULE_CATALOGUE.md`.

**Phase 2 — intake:** `applications` (DRAFT/SUBMITTED, bound to a rule version, `row_version` for compare-and-set, `submission_snapshot`), `application_documents` (generated storage key, sha256, `NOT_VERIFIED` constraint, one active file per checklist slot via a partial unique index), `application_events`.

Migrations: `0001` (Phase 1), `0002` (Phase 2). `alembic check` reports no drift.

## Repository layout

```
backend/   FastAPI app, Alembic migrations, seed script, tests
frontend/  Vite SPA
docs/      roadmap, architecture, requirements, design system, decisions
docker-compose.yml   PostgreSQL for local development
```

## Phase 2 control flow

- **Selecting a configuration:** category + academic year -> exactly one published, in-force version, else a recorded *gap* and a refusal (never a default route).
- **Evaluating an application:** always against the version's frozen snapshot (`checklist.evaluate`). Submitted applications use the snapshot copied into `submission_snapshot`, so later publication or retirement cannot change them.
- **Concurrency:** drafts and submissions use `UPDATE ... WHERE row_version = :v AND status = 'DRAFT'` (compare-and-set); submission also locks the row (`FOR UPDATE`). Publication locks the version and its category so overlapping versions cannot both pass the overlap check.
- **Scoping:** every application query filters by the *stored* user's institution; another institution's record is `404` and audited.

## Phase 3 — document processing

```
Browser ──► FastAPI (upload: validate, store, enqueue — one transaction)
                │                         ▲ polls state (3 s while non-terminal)
                ▼
          PostgreSQL ◄── python -m app.worker (1..n)  ── LocalStorage (private) / Tesseract (local) / clamd (optional)
          processing_jobs (leases)        pipeline: validate → scan → text/OCR → fields → cross-check
```

- **No new infrastructure.** The queue is a PostgreSQL table; the worker is a plain Python process. No Redis, Celery, Kafka or microservices.
- **Separation:** `app/processing/*` are pure stages (bytes in, results out, no database) and are unit-tested without the website; `app/jobs.py` owns queue semantics; `app/evidence.py` persists results and derives findings; `app/services/documents.py` owns versioned upload and access; routers are thin.
- **Immutable upload record, mutable processing state:** `application_documents` (a version) is never modified by processing, so the Phase 2 guard on submitted applications still holds; everything that changes lives in `document_processing`, `processing_jobs`, `extracted_pages`, `extracted_fields`, `preliminary_findings`, `document_events`.
- **Access:** a document is visible to its owning institution or, read-only, to a reviewer (`evidence:read`) once the application is submitted; everything else is 404 and audited.

### Phase 3 data model (migration `0003`)

`document_slots`, `document_processing`, `processing_jobs`, `extracted_pages`, `extracted_fields`, `preliminary_findings`, `document_events`; `application_documents` gains `slot_id`, `version_number`, `previous_version_id`, `superseded_at`; `checklist_items` gains `extraction` (JSON). The migration backfills slots and version numbers for existing Phase 2 uploads and rebuilds the "one current file per item" index.


## Phase 4 — scrutiny, notices and responses

Full description in `SCRUTINY.md`. Scrutiny state lives in its own tables, so a submitted application and its Phase 3 documents stay immutable. Four lifecycles are kept apart: application (`DRAFT`/`SUBMITTED`), scrutiny case, deficiency notice, finding; each changes only through a transition table in `app/domain.py` applied by a compare-and-set update.

### Phase 4 data model (migration `0004`)

`scrutiny_cases` (one per submitted application; rule version copied, never rebound), `scrutiny_rounds` (initial + one re-scrutiny per response), `scrutiny_assignments` (source, demo flag, who/what assigned, selection reference), `scrutiny_findings` + `scrutiny_finding_events`, `deficiency_notices` (frozen content + SHA-256, authority, policy resolution, due date, idempotency key; partial unique indexes allow one notice in preparation and one live notice per case), `notice_items`, `notice_responses` (one per notice) + `notice_response_items` + `notice_response_documents` (exact document versions), `stage_handoffs`, `scrutiny_events` (append-only; an `institution_visible` flag controls the institution's timeline), `notifications`. The migration backfills a case for every application submitted before it. ORM guards make issued notices, submitted responses and event tables immutable, forbid direct state assignment, and allow a submitted application to gain a document version only through a notice correction.

### Phase 4 control flow

submit → case + round 1 (same transaction) → coordinator assignment (demo, labelled) → reviewer starts, records findings (own conclusions; automated issues are only pointers) → reviewer prepares a notice → issuer inspects the exact content and issues (one transaction, idempotent) → institution sees the notice, uploads corrected evidence (new versions) and submits one response → case moves to re-scrutiny (new round) → reviewer concludes each finding with a reason → completion → hand-off read from the frozen route.


## Phase 5 — route engine, EVC and SHC

Full description in `COMMITTEE.md`. The two committee processes share one `committee_cases` table (unique per application and stage) so integrity is kept by foreign keys, but have separate state tables, services, permissions and screens. The route engine (`services/stages.py`) is the only thing that creates a committee case or a decision hand-off, and it reads the frozen route.

### Phase 5 data model (migration `0005`)

`committee_policies` (versioned, source-referenced configuration; only DEMO exists), `committee_cases` (state, trigger, frozen policy snapshot, blockers), `committee_selection_requests` + `committee_assignments` (the provider boundary's record), `committee_schedules` (visit or hearing; rescheduling adds rows), `committee_evidence` (private files or links), `committee_reports` (EVC report / SHC recommendation versions, immutable once submitted, with a snapshot and hash), `committee_events` (append-only), `evc_observations`, `shc_requirements`, `shc_compliance_rounds`, `shc_responses` + items + documents, `shc_findings`, `shc_proceedings` (append-only), `route_transitions`, `decision_handoffs`; `notifications` gains `committee_case_id`. ORM guards: committee state moves only through compare-and-set transitions; submitted reports, issued requirements, submitted responses, recorded schedules and evidence are immutable; events and proceedings are append-only. Partial unique indexes: one live schedule, one open selection request, one open compliance round and one draft report per case.

### Phase 5 control flow

scrutiny completes -> hand-off row -> `stages.on_scrutiny_handoff` (same transaction) -> EVC / SHC case (idempotent) or a determination by a coordinator -> selection provider -> schedule (propose, confirm) -> visit, observations, evidence, report (immutable version) -> coordinator completes -> `route_transitions` -> next stage, determination, or `decision_handoffs`. SHC: requirements -> compliance round -> institution response (new document versions) -> review (findings, hearing, recommendation) -> completion -> the same route step. Recovery: `python -m app.reconcile_committee`.

## Phase 6 — decisions, Council steps and SAC appeals

Three authorities, three records: `EcDecision` (Executive Committee), `CouncilDecision` (ratification or appeal decision), `Appeal` + the SAC `CommitteeCase`. Their services (`services/decisions.py`, `appeals.py`, `sac.py`) reuse the Phase 5 machinery: compare-and-set transitions (`DECISION_TRANSITIONS`), ORM guards for immutable and append-only records, idempotency keys, `row_version`, in-app notifications, the audit log, the committee policy / frozen-snapshot pattern (stage `DEC`, stage `SAC`) and the selection-provider boundary for SAC members.

### Phase 6 data model (migration `0006`)

`decision_cases` (one per application; unique hand-off), `bureau_observations`, `ec_decisions` (versioned), `official_issuances` (one live per case), `council_decisions` (`is_live`, partial unique indexes), `appeals` (one per application), `appeal_documents`, `external_syncs` (one per subject and kind), `post_decision_handoffs`. The Phase 5 CHECK constraints for policy stage, case stage and report kind are widened for `SAC` and `DEC`.

### Phase 6 control flow

`DecisionHandoff` → `decisions.open_case` → Bureau observations → `record_decision` → `issue_outcome` (+ `portal_sync.record`) → `record_ratification` → [appeal: `appeals.submit` → SAC `CommitteeCase` → `sac.complete` → `appeals.on_sac_completed` → `record_council_decision`] → `close_case` → `PostDecisionHandoff`. `python -m app.reconcile_decisions` runs the route reconcile, retries pending syncs and finalises expired appeal windows. No scheduler is introduced.
