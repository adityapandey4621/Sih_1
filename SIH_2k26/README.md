# Dossier — AICTE approval workflow automation (SIH 2026, PS 26262)

Integrated approval workflow, compliance monitoring and alert management. The product **supports** the official process; authorised officials make every regulatory decision. It is not an official AICTE service.

**Status: Phase 5 — conditional route engine, EVC and SHC** (queues, scoped member workspaces, a selection boundary with a labelled demonstration mode, schedules, visit observations and evidence, versioned reports, SHC compliance / hearing / recommendation, hand-off to the decision stage). Built on **Phase 4 — authorised scrutiny, formal deficiency notices and re-scrutiny** (queue, scoped reviewer workspace, notices issued only by an authorised issuer, institution responses with corrected evidence as new versions, hand-off to the next configured stage, in-app notifications), on top of Phase 3 document processing, the Phase 2 rule catalogue and intake and the Phase 1 foundation. The Executive Committee decision, SAC appeals, the deadline engine and AICTE integration are **not** built; no report, recommendation or completed stage is an approval. See `docs/COMMITTEE.md`, `docs/SCRUTINY.md` and `docs/PROJECT_ROADMAP.md`.

> **The official Approval Process Handbook 2024-27 has been read for the clauses the product relies on** (`docs/APH_VERIFICATION.md`), but **no document checklist is encoded and later corrigenda are unchecked**, so the only selectable configuration is still seeded **DEMO ONLY — NOT VERIFIED AS CURRENT OFFICIAL RULES**. See `docs/RULE_CATALOGUE.md` and the gaps in `docs/REQUIREMENTS.md`.

```
backend/   FastAPI + SQLAlchemy + Alembic (PostgreSQL)
frontend/  Vite + React + TypeScript SPA
docs/      ROADMAP, ARCHITECTURE, REQUIREMENTS (incl. rule-source gaps), RULE_CATALOGUE, DOCUMENT_PROCESSING, SCRUTINY, COMMITTEE, APH_VERIFICATION, API, DESIGN_SYSTEM, DECISIONS
           sources/ = the AICTE reference pack supplied by the team (not official publications)
```

## Run locally

Prerequisites: Node 22+, Python 3.12+, PostgreSQL 15+ (or Docker).

```powershell
# 1. Database (Docker option; otherwise create a database/user yourself)
docker compose up -d db

# 2. Backend
cd backend
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements-dev.txt
copy ..\.env.example .env           # then set SEED_PASSWORD (min. 12 chars) and adjust DATABASE_URL if needed
.\.venv\Scripts\alembic upgrade head
.\.venv\Scripts\python -m app.seed        # demo users, one per role (development only)
.\.venv\Scripts\python -m app.seed_rules  # catalogue: unpublishable OFFICIAL drafts + published DEMO ONLY configuration
# in two terminals:
.\.venv\Scripts\python -m uvicorn app.main:app --reload --port 8000   # API
.\.venv\Scripts\python -m app.worker                                         # document-processing worker (needed for uploads to be read)

# 3. Frontend (new terminal)
cd frontend
npm install
npm run dev                          # http://localhost:5173  (proxies /api to :8000)
```

macOS/Linux: use `source .venv/bin/activate` and `cp`.

Demo accounts (seed): `<role>@demo.example.org`, e.g. `scrutiny.officer@…`, `platform.admin@…`, `auditor@…`; password = your `SEED_PASSWORD`. Roles: institute.user, scrutiny.officer, scrutiny.coordinator, notice.issuer, evc.coordinator, evc.member, shc.coordinator, shc.member, executive.committee, sac.member, rules.admin (author), rules.approver, auditor, platform.admin.

Document processing: with the worker running, upload a PDF in an application's Documents step and watch its state update. After submission, `scrutiny.coordinator@…` assigns a reviewer (demonstration assignment), `scrutiny.officer@…` works the case, `notice.issuer@…` issues a prepared notice, and the institution responds under **Notices**. Details in `docs/SCRUTINY.md`.

Committee work (Phase 5): with a DEMO rule set, `evc.coordinator@…` records a demonstration selection (fictional `evc.member@…`), proposes and confirms a visit; the member records observations and submits the report; the coordinator completes the stage; `shc.coordinator@…` and `shc.member@…` run compliance, review and the recommendation; the institution answers under **Compliance**. Details and the exact start-up list in `docs/COMMITTEE.md`.

Try it: sign in as `institute.user@…` -> Applications -> New application. As `rules.admin@…` open Rule catalogue to author; as `rules.approver@…` open the version left in review and publish it (it needs an explicit supersede). Publishing as the author is refused.

## Environment variables (`backend/.env`, see `.env.example`)

| Variable | Purpose |
|---|---|
| `ENVIRONMENT` | `development` / `test` / `production` (production ⇒ `Secure` cookies, docs off, seeding refused) |
| `DATABASE_URL` | SQLAlchemy URL, `postgresql+psycopg://…` |
| `ALLOWED_ORIGINS` | Origins accepted on state-changing requests |
| `SESSION_IDLE_MINUTES`, `SESSION_ABSOLUTE_HOURS` | Session lifetimes |
| `LOGIN_MAX_FAILURES`, `LOGIN_WINDOW_MINUTES` | Sign-in throttling |
| `INVITATION_VALID_HOURS` | Invitation expiry |
| `STORAGE_DIR` | Private attachment directory (default `storage`, git-ignored). Never served statically |
| `MAX_UPLOAD_MB` | Attachment size limit (default 10) |
| `ALLOW_DEMO_RULES` | Allow DEMO configuration to be published/selected (default true; **always off in production**) |
| `SEED_PASSWORD` | Dev seed only |
| `VITE_API_PROXY_TARGET` (frontend, optional) | Dev proxy target, default `http://127.0.0.1:8000` |

## Checks

```powershell
cd backend;  .\.venv\Scripts\python -m pytest -q                 # SQLite by default (PostgreSQL-only and real-Tesseract tests skip when not configured)
$env:TEST_DATABASE_URL="postgresql+psycopg://…/sih_test"; pytest  # same suite on PostgreSQL
cd frontend; npm run typecheck; npm test; npm run build        # no ESLint is configured
```

## Operations notes

- **Account provisioning:** only a platform administrator can invite users (`/app/admin/users`) or approve institution requests. No email provider is configured, so the invitation link is shown once to the administrator to pass on securely.
- **Password recovery:** self-service reset is unavailable (no email provider). Operator procedure: `python -m app.set_password user@example.org` (revokes sessions, audited).
- **First administrator:** in a non-seeded environment, create one `platform_admin` row via a one-off script using `app.security.hash_password` (a bootstrap command is a Phase 3 item).
- Production: serve SPA and `/api` from one origin behind TLS; set `ENVIRONMENT=production`.
