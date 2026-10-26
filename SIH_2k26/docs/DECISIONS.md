# Decisions

Format: decision — rationale. Reversible unless stated.

1. **Keep the existing Vite + React + TypeScript + React Router stack; do not migrate to Next.js.** The repo already used it. The brief says to reuse a suitable stack. An SPA with a separate FastAPI API is a good fit (all authenticated content is client-state driven, SEO matters only for the landing page).
2. **Replace the existing "Halcyon" prototype UI.** It was dark/violet/grain/3D-oriented, which the brief explicitly forbids, and was fictional ("Edition 2026"). The code remains in git history (`f2385ae`). Reused: tooling and the font packages.
3. **Monorepo layout (`frontend/`, `backend/`, `docs/`).** The root previously held the frontend only.
4. **Plain CSS with design tokens instead of Tailwind.** No Tailwind existed; adding it would not buy anything for a small, token-driven system. Revisit if the component count grows.
5. **Lucide icons** (`lucide-react`) as the single icon library.
6. **No animation library.** The only motion is a CSS/React-driven marker; `motion` and `three` were dropped.
7. **Server-side sessions, not JWTs.** Revocable, simple expiry, no tokens in JS-accessible storage.
8. **Double-submit CSRF + Origin check**, because the SPA and API share an origin through a proxy and cookies are `SameSite=Lax`.
9. **Throttling in PostgreSQL (`login_attempts`)**, not in memory or Redis: survives restarts, works across workers, no new infrastructure.
10. **Strings, not native DB enums**, for roles/statuses (see ARCHITECTURE).
11. **`platform_admin` role added** for provisioning and access-request review (see REQUIREMENTS).
12. **Permissions are returned by the backend**, and the frontend maps permission → navigation/dashboard spec. The role→permission table exists only once, in `backend/app/rbac.py`.
13. **Invitations are shown to the administrator as a one-time link** because no email provider is configured. The UI says so explicitly; nothing claims an email was sent.
14. **Password policy:** minimum 12 characters, maximum 128, no composition rules (NIST 800-63B style).
15. **Tests run on SQLite for speed; the app is verified against PostgreSQL** (see README for the exact checks run). Models avoid dialect-specific types so both work.
17. **Product name "Dossier" is a placeholder wordmark** (typographic only, no emblem). Change in `Wordmark.tsx`, `index.html`, `landing.content.ts`.
18. **`GET /api/auth/session` returns 200 + `null` when signed out**, used for the initial probe so public pages don't log 401 errors. `/api/auth/me` still returns 401.
19. **Invitation token lives in the URL fragment** (`#token=…`), so it never reaches server logs, proxies or Referer headers; it is removed from the address bar after use.
20. **Operator CLI `app.set_password`** is the interim recovery path (no email provider). Throttling can be used to lock a victim out for the window (known trade-off of per-email throttling; window is short).
21. **Dev tooling:** vitest 5 (needed for Vite 8 typings), `lucide-react` 1.x.
22. **Dashboard section structures live in `frontend/src/features/dashboard/dashboardSpecs.ts`.** They describe layout/labels only, never rules, durations or counts.
