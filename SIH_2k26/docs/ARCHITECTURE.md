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
