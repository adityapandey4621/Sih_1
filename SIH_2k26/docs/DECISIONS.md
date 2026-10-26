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

## Phase 2

23. **`rules_approver` is a separate role** from `rules_admin`; the service additionally refuses an approver who contributed to the version.
24. **OFFICIAL vs DEMO classification on the rule set.** OFFICIAL can be published only when every cited source is `VERIFIED_OFFICIAL`; DEMO only where `ALLOW_DEMO_RULES` is true and the environment is not production. This is how "no unverified rule is presented as official" is enforced rather than merely labelled.
25. **Rule content is relational** (checklist items, route stages, policies as tables) rather than one JSON blob, for foreign keys and uniqueness, plus a **frozen JSON snapshot + SHA-256** written at publication. Applications evaluate against the snapshot, never the live rows.
26. **Immutability twice:** service refusals (`409 not_editable`) and an ORM `before_flush` guard. Raw SQL bypasses ORM hooks; a database trigger is a possible later hardening.
27. **Overlap handling:** a published version may not overlap another for the same category/year/dates. Same-rule-set overlap needs explicit `supersede` (retires the old one in the same transaction); different-rule-set overlap always blocks.
28. **Drafts bind to a rule version at creation**; submission re-resolves and refuses (`rule_version_changed`) if a different version now applies, until the user explicitly rebinds. Rebinding never silently deletes answers or files (orphans are reported).
29. **Compare-and-set (`row_version`) rather than long locks** for drafts and rule drafts; submission and publication additionally take row locks.
30. **Attachments:** content sniffing (PDF/PNG/JPEG) must agree with extension and declared type; storage keys are generated; files are never overwritten; replaced draft files are soft-removed; `verification_status` has a DB check constraint allowing only `NOT_VERIFIED`. No malware scanning yet (Phase 3).
31. **Cross-institution access answers 404, not 403**, and is audited. Permission refusals (`403`) are audited centrally in `deps.require_permission`.
32. **Effective-date semantics:** a version's effective range is compared with the date of creation/submission; academic years are matched exactly.
33. **Test environment uses minimal Argon2 cost** (still Argon2id) so the suite stays fast; production is unaffected.
34. **No duplicate-application detection yet** (an institution may open several drafts for the same category/year); planned with Stage 1 hardening in Phase 3.

## Phase 3

35. **Phase renumbering.** Phase 3 became *document processing*; scrutiny and the deficiency process are Phase 4, and later phases shifted by one.
36. **Immutable version record, separate processing state.** `application_documents` stays immutable (Phase 2 guard intact); processing writes to its own tables, so work continues after submission without touching submitted records.
37. **Slot + versions.** One slot per (application, checklist item); versions chained by `previous_version_id`; `superseded_at` marks replaced versions; `current_version_id` points at the live one. Uploads and removals remain draft-only; post-submission correction is Phase 4.
38. **Same-bytes policy:** identical to the current version → no new version/job (200); identical to an earlier one → new version; the same file on two requirements → allowed, with a low-priority finding.
39. **PostgreSQL job table + polling worker**, not Redis/Celery: claim by compare-and-set (+ `SKIP LOCKED`), leases, reaping, bounded retries. One `PROCESS_DOCUMENT` job per version (retries re-queue the same row), coalesced cross-check jobs.
40. **Error taxonomy drives retry** (input → never, config → by a person, transient → bounded backoff). A crashed worker's job is immediately runnable (no backoff); a stale worker cannot write results (ownership check at persist time).
41. **Extraction is configuration** (`checklist_items.extraction`, frozen in snapshots). The engine is label-anchored and deterministic; fuzzy label matching applies only to OCR text and always yields `UNCERTAIN`. No free-form regex in configuration (closed vocabulary: text/date/identifier) so rule authors cannot introduce pathological patterns.
42. **Findings are `PRELIMINARY_ISSUE` only** (DB check constraint), recomputed idempotently from current versions with fingerprints; superseded findings are kept. Wording for institutions comes from one catalogue (`processing/issues.py`); informational items (scanner unavailable) are not shown to institutions.
43. **Server-owned state wording.** The API returns label, explanation, tone and terminal flag; the UI has no enum mapping. Every explanation repeats that checks are technical only.
44. **Scrutiny officers read all submitted applications** in Phase 3 (no assignment concept yet); drafts are never visible to reviewers. Auditors get no document access.
45. **Previews are rasterised PNGs** rendered on demand; PDFs are never served inline.
46. **PyMuPDF (AGPL-3.0)** was chosen as the brief names it; its licence must be reviewed before distribution. Pillow guards images (pixel limit checked from the header; Pillow's process-wide limit is not modified).
47. **Tesseract is driven as a subprocess** (TSV output for per-word confidence), not through a Python wrapper; it is optional at runtime. Tested at 5.4.0 using a portable extraction of the UB-Mannheim build.
48. **Test fixture resets the PostgreSQL schema** instead of `drop_all` (robust to stale schemas).
