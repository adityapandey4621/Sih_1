# Dossier — AICTE approval workflow automation (SIH 2026, PS 26262)

Integrated approval workflow, compliance monitoring and alert management. The product **supports** the official process; authorised officials make every regulatory decision. It is not an official AICTE service.

**Status: Phase 2 — versioned rule catalogue and application intake**, on top of the Phase 1 foundation (design system, landing page, authentication, role-based shell). Scrutiny, OCR, EVC, SHC, SAC, decisions and the deadline engine are **not** built. See `docs/PROJECT_ROADMAP.md`.

> **No rule in this system is verified against the official AICTE publication.** The only selectable configuration is seeded **DEMO ONLY — NOT VERIFIED AS CURRENT OFFICIAL RULES**. See `docs/RULE_CATALOGUE.md` and the gaps in `docs/REQUIREMENTS.md`.

```
backend/   FastAPI + SQLAlchemy + Alembic (PostgreSQL)
frontend/  Vite + React + TypeScript SPA
docs/      ROADMAP, ARCHITECTURE, REQUIREMENTS (incl. rule-source gaps), RULE_CATALOGUE, API, DESIGN_SYSTEM, DECISIONS
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
.\.venv\Scripts\python -m uvicorn app.main:app --reload --port 8000

# 3. Frontend (new terminal)
cd frontend
npm install
npm run dev                          # http://localhost:5173  (proxies /api to :8000)
```

macOS/Linux: use `source .venv/bin/activate` and `cp`.

Demo accounts (seed): `<role>@demo.example.org`, e.g. `scrutiny.officer@…`, `platform.admin@…`, `auditor@…`; password = your `SEED_PASSWORD`. Roles: institute.user, scrutiny.officer, evc.coordinator, evc.member, shc.member, executive.committee, sac.member, rules.admin (author), rules.approver, auditor, platform.admin.

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
cd backend;  .\.venv\Scripts\python -m pytest -q                 # SQLite by default (175 pass, 1 PostgreSQL-only test skipped)
$env:TEST_DATABASE_URL="postgresql+psycopg://…/sih_test"; pytest  # same suite on PostgreSQL
cd frontend; npm run typecheck; npm test; npm run build        # 63 tests; no ESLint is configured
```

## Operations notes

- **Account provisioning:** only a platform administrator can invite users (`/app/admin/users`) or approve institution requests. No email provider is configured, so the invitation link is shown once to the administrator to pass on securely.
- **Password recovery:** self-service reset is unavailable (no email provider). Operator procedure: `python -m app.set_password user@example.org` (revokes sessions, audited).
- **First administrator:** in a non-seeded environment, create one `platform_admin` row via a one-off script using `app.security.hash_password` (a bootstrap command is a Phase 3 item).
- Production: serve SPA and `/api` from one origin behind TLS; set `ENVIRONMENT=production`.
