# PACE — AICTE approval workflow automation (SIH 2026, PS 26262)

Integrated approval workflow, compliance monitoring and alert management. The product **supports** the official process; authorised officials make every regulatory decision. It is not an official AICTE service.

**Status: Phase 8 — integration boundary, hardening, end-to-end validation and deployment** on top of Phase 7 (post-approval compliance, document validity, the persistent deadline engine and alerts), Phase 6 (Executive Committee decisions, Council steps, SAC appeals), Phase 5 (conditional route engine, EVC and SHC), Phase 4 (authorised scrutiny and deficiency notices), Phase 3 (document processing and OCR), Phase 2 (rule catalogue and intake) and Phase 1 (foundation).

The software never decides: authorised bodies decide and the system records. No report, recommendation, upload, passing deadline or completed stage is an approval, a rejection, a verification or a penalty.

> **What is NOT verified.** The official Approval Process Handbook 2024-27 has been read for the clauses the product relies on (`docs/APH_VERIFICATION.md`), but no document checklist is encoded and later corrigenda are unchecked, so the only selectable configuration is seeded **DEMO ONLY — NOT VERIFIED AS CURRENT OFFICIAL RULES**.
> **No AICTE API, credential, webhook or specification has been supplied**, so the AICTE integration is a project-defined boundary with a mock and a demo receiver: **real integration is UNVERIFIED** (`docs/INTEGRATION.md`).
> Nothing has been deployed to any host. Read `docs/ACCEPTANCE_REPORT.md` for what was actually run and `docs/GAPS_AND_BLOCKERS.md` for what remains.

```
backend/   FastAPI + SQLAlchemy + Alembic (PostgreSQL); app/integration/aicte = the integration boundary
frontend/  Vite + React + TypeScript SPA (+ nginx image)
ops/       backup / restore-verify scripts
docs/      ARCHITECTURE, DEPLOYMENT, OPERATIONS, INTEGRATION, SECURITY_REVIEW, ACCEPTANCE_REPORT, GAPS_AND_BLOCKERS, COMPLIANCE_AND_DEADLINES, DECISIONS_AND_APPEALS,
           COMMITTEE, SCRUTINY, DOCUMENT_PROCESSING, RULE_CATALOGUE, REQUIREMENTS (incl. rule-source gaps), APH_VERIFICATION, API, DESIGN_SYSTEM, DECISIONS, PROJECT_ROADMAP
           sources/ = the AICTE reference pack supplied by the team (not official publications)
```

## Run it with Docker (demonstration)

```powershell
copy .env.example .env                 # set SEED_PASSWORD (12+ characters)
docker compose up --build -d           # db, migrate, api, worker, scheduler, web  ->  http://localhost:8080
docker compose --profile demo run --rm seed
```

Health: `/api/health` (liveness) and `/api/ready` (database, schema, storage). Production, secrets, TLS and hosting limits: `docs/DEPLOYMENT.md`. Operating, backups, recovery: `docs/OPERATIONS.md`.

## Run it locally (development)

Prerequisites: Node 22+, Python 3.13 (3.12+), PostgreSQL 15+ (or Docker), Tesseract (optional, for scanned pages).

```powershell
# 1. Database (Docker option; otherwise create a database/user yourself)
docker compose up -d db

# 2. Backend
cd backend
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements-dev.txt
copy ..\.env.example .env           # then set SEED_PASSWORD (min. 12 chars) and adjust DATABASE_URL if needed
.\.venv\Scripts\python -m alembic upgrade head
.\.venv\Scripts\python -m app.seed        # demo users, one per role (development only)
.\.venv\Scripts\python -m app.seed_rules  # catalogue: unpublishable OFFICIAL drafts + published DEMO ONLY configuration (incl. DEMO deadline policies)
# in three terminals:
.\.venv\Scripts\python -m uvicorn app.main:app --reload --port 8000   # API
.\.venv\Scripts\python -m app.worker                                         # document processing (needed for uploads to be read)
.\.venv\Scripts\python -m app.scheduler                                      # deadlines and alerts (needed for reminders)

# 3. Frontend (new terminal)
cd frontend
npm install
npm run dev                          # http://localhost:5173  (proxies /api to :8000)
```

macOS/Linux: use `source .venv/bin/activate` and `cp`.

Demo accounts (seed): `<role>@demo.example.org`, e.g. `scrutiny.officer@…`, `regulation.bureau@…`, `compliance.officer@…`, `platform.admin@…`; password = your `SEED_PASSWORD`. Roles: institute.user, scrutiny.officer, scrutiny.coordinator, notice.issuer, evc.coordinator, evc.member, shc.coordinator, shc.member, executive.committee, regulation.bureau, council.member, sac.coordinator, sac.member, compliance.officer, rules.admin (author), rules.approver, auditor, platform.admin.

Walk-through by phase (what each role does) is in the phase documents: intake and documents (`docs/DOCUMENT_PROCESSING.md`), scrutiny and notices (`docs/SCRUTINY.md`), EVC and SHC (`docs/COMMITTEE.md`), decisions and appeals (`docs/DECISIONS_AND_APPEALS.md`), obligations, validity and deadlines (`docs/COMPLIANCE_AND_DEADLINES.md`), integration (`docs/INTEGRATION.md`).

## Environment variables

See `.env.example` for the full annotated list and `docs/DEPLOYMENT.md` §3.2 for production. The most important:

| Variable | Purpose |
|---|---|
| `ENVIRONMENT` | `development` / `test` / `production` (production ⇒ refuses development settings, `Secure` cookies, HSTS, docs off, seeding refused, demo rules off, malware scan required) |
| `DATABASE_URL` / `DATABASE_URL_FILE` | SQLAlchemy URL, `postgresql+psycopg://…` |
| `ALLOWED_ORIGINS` | Origins accepted on state-changing requests |
| `TRUSTED_PROXY_COUNT` | reverse proxies in front of the API (0 = ignore `X-Forwarded-For`) |
| `STORAGE_DIR`, `MAX_UPLOAD_MB`, `SCANNER`, `REQUIRE_MALWARE_SCAN` | private attachment storage and malware scanning |
| `AICTE_INTEGRATION_MODE` | `none` (default) / `mock` (DEMO ONLY) / `http` — see `docs/INTEGRATION.md` |
| `SEED_PASSWORD` | development seed only |

## Checks

```powershell
cd backend;  .\.venv\Scripts\python -m pytest -q                 # SQLite by default (PostgreSQL-only and real-Tesseract tests skip when not configured)
$env:TEST_DATABASE_URL="postgresql+psycopg://…/sih_test"; pytest  # same suite on PostgreSQL (also runs the append-only-trigger and concurrency tests)
.\.venv\Scripts\python -m alembic check                          # models match migrations
cd frontend; npm run typecheck; npm test; npm run build        # no ESLint is configured
```

## Operations notes

- **First administrator** of a new deployment: `python -m app.create_admin --email … --name "…"` (prompts for the password; never taken from the command line). Further accounts are provisioned by that administrator (`/app/admin/users`): the invitation link is shown once, because no email provider is configured.
- **Password recovery:** self-service reset is unavailable (no email provider). Operator procedure: `python -m app.set_password user@example.org` (revokes sessions, audited).
- **Recovery after an outage:** start the worker and the scheduler again; both recover abandoned work by themselves. `python -m app.reconcile_decisions` retries unconfirmed external syncs.
- **No email or SMS is ever sent.** Notifications are in-app and say so.
