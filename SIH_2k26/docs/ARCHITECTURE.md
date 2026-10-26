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

- **Frontend** never decides who a user is. It asks `GET /api/auth/me` and receives the user's role and **permission list**. Navigation, route guards and dashboard selection are derived from those permissions. The backend re-checks every protected call.
- **Backend** owns identity, sessions, permissions, access requests, invitations and the audit log. Routers are thin; security primitives live in `security.py`, permissions in `rbac.py`, request authorisation in `deps.py`.
- **Database** PostgreSQL is authoritative. Schema changes go through Alembic only. Enumerated values are stored as validated strings (not native enums) so adding a role/status is a code change, not a painful migration.
- **Integration seams** (not built): `services/email.py` defines an `EmailSender` protocol with a `NotConfiguredSender`; identity is reached only through `deps.get_current_user`, so a future SSO/IdP replaces session lookup without touching dashboards.

## Authentication design

- Opaque 256-bit session token in cookie `sih_session` (`HttpOnly`, `SameSite=Lax`, `Secure` in production, `Path=/`). Only a SHA-256 hash of the token is stored (`sessions.token_hash`).
- Idle timeout and absolute lifetime are configurable; both are enforced server-side on every request. Logout revokes the row.
- CSRF: double-submit token. Cookie `sih_csrf` (JS-readable) must equal header `X-CSRF-Token` on every non-safe method; `Origin` is also checked against allowed origins when present.
- Login throttling is stored in `login_attempts` (per normalised email and per IP). Throttling applies equally to unknown and known emails; the failure message is uniform. A dummy Argon2 verification equalises timing for unknown users.
- Roles are only assigned by an administrator via invitation. The registration form creates an `access_request` row, never a user.

## Permission model

`rbac.py` maps each `Role` to a set of `Permission` strings, e.g. `dashboard:scrutiny`, `admin:users`, `audit:read`. A role never implies another role's permissions. `rules_admin` has no decision permissions; `auditor` has only read permissions. Phase 2+ adds permissions here; the UI picks them up through the same `permissions` array.

## Data model (Phase 1)

`institutions`, `users`, `sessions`, `login_attempts`, `access_requests`, `invitations`, `audit_events`.

## Repository layout

```
backend/   FastAPI app, Alembic migrations, seed script, tests
frontend/  Vite SPA
docs/      roadmap, architecture, requirements, design system, decisions
docker-compose.yml   PostgreSQL for local development
```
