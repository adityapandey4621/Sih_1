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
17. **Product name is "PACE"** (typographic wordmark, no emblem). Configured in `Wordmark.tsx`, `index.html`, `landing.content.ts`.
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


## Phase 4

- **Scrutiny state is separate from application state.** The application guard (submitted = immutable) is untouched; a case, rounds, findings and notices are their own tables.
- **Two new application roles, not AICTE designations:** `scrutiny_coordinator` (assignment, route determination; no document contents) and `notice_issuer` (the only way to issue a notice). Reviewers (`scrutiny_officer`) can prepare but never issue. Capabilities are edited in `rbac.py`.
- **Reviewer scope = assignment.** The Phase 3 `/api/review/*` endpoints (every reviewer saw every submitted application) were retired in favour of `/api/scrutiny/*`; document endpoints now check scope.
- **Selection boundary.** Demo assignment only for DEMO-classified cases; official selection returns "unavailable". A recorded determination, not a default, resolves conditional or multiple next stages.
- **No invented regulatory behaviour.** No response duration, deadline, template, round limit or issuing designation is encoded; each is a documented gap (G-P4-1…6). Source references can only be chosen from the frozen rule version.
- **Notice issuance is idempotent and confirmable.** Hash of the inspected content, reason, idempotency key, compare-and-set transitions and partial unique indexes.
- **Corrected evidence = narrow exception.** A `correction` mode of the existing upload pipeline plus an ORM guard that allows only a new version and `superseded_at` on a submitted application.
- **Notifications cannot undo their cause.** Savepoint (PostgreSQL), audit on failure, reconciliation command. SQLite (tests) writes without a savepoint because its driver cannot do them reliably.
- **Stage identification by convention.** The route stage key `scrutiny` marks the scrutiny stage; if it is absent the case goes to the exception queue.
- **Informal corrections rejected.** No clarification channel outside formal notices (no source for one).- **Re-scrutiny cannot raise new findings.** Handbook Ch. I cl. 1.8(g): the Re-Scrutiny Committee verifies only the deficiencies already pointed out. Enforced in the service (`409 rescrutiny_scope`) and mirrored in the UI; supersedes the Phase 4 behaviour that allowed new findings in round 2.
- **"No period stated" is a settled answer, not a gap.** A policy with no duration whose source is `VERIFIED_OFFICIAL` resolves as `NO_DURATION` and does not block issuance or raise a configuration gap; it still sets no due date. The same policy on an unverified source, or no policy at all, still blocks a non-demo notice.
- **The product's notice is not an LoD.** The handbook's Letter of Deficiency (cl. 1.10) is an Executive Committee output; the scrutiny-stage notice is the cl. 1.8(f) communication of deficiencies.
- **APH sources verified for one edition.** `VERIFIED_OFFICIAL` means read in the supplied 2024-27 PDF (hash recorded); corrigenda are unchecked and the caveat is stored on the source.
- **One route engine, one place that creates stages.** `services/stages.py` is the only creator of committee cases and decision hand-offs, always from the frozen route, in the transaction of the completion that caused it. A repeated request creates nothing new (unique application + stage).
- **Conditional means a person decides, on record.** "AA" stages are never evaluated by code. The coordinator's determination (with a reason) is stored and cited by the case it activates.
- **EVC and SHC are separate processes on a shared table.** One `committee_cases` table keeps foreign keys intact; separate state tables, services, permissions and UI keep the processes distinct. The combined Table 2.3 "SAC / SHC" key activates only SHC; SAC is a post-decision appeal.
- **The selection boundary is a contract, not an algorithm.** No member-choosing logic exists. The real provider reports itself unavailable; the demo provider is labelled and limited to DEMO rule sets.
- **Committee configuration is data.** Modes, checklist, sections and SHC options live in versioned `committee_policies`, frozen into a case. No OFFICIAL policy exists until its sources are verified; non-demo cases are blocked visibly and a gap is recorded.
- **Hybrid visits are refused.** They come from an unverified corrigendum; the refusal states why.
- **Reports are immutable versions with a snapshot.** Corrections are new versions opened by a coordinator with a reason; the submitted hash and the document versions it rests on are kept.
- **Stage completion is its own action.** Submitting a report or recommendation completes nothing; the coordinator completes the stage, and only then does the route advance and the institution see the EVC report.
- **Scheduling never claims availability.** Only an exact-instant clash between a member's own live schedules is detected; no duration or deadline is invented.
- **The decision hand-off is a record.** It is created only when no required stage is outstanding and it says `decided: false`.

## Phase 6 decisions

1. **Separate roles for the Bureau and the Council** (`regulation_bureau`, `council_member`), as application capabilities, so that the person who observes, decides, issues and ratifies are different permissions. They are not claimed AICTE designations.
2. **Appeal windows encoded per clause**, anchored at the first issue: 7 days from the LoD issue (Ch. I 1.11(a)) or from the upload of the Executive Committee decision (Ch. II 2.24(a)). Calendar days are used and flagged unverified (the handbook states neither the counting convention nor the cut-off time). The deadline policy comes from the application's frozen rule version, never from code. Working-day counting is refused.
3. **Reference only for official outputs**: an issued outcome is a reference and/or an uploaded PDF. The system generates no letter text, signature or seal.
4. **A decision is a new versioned record**, never an edit: corrections carry a reason and supersede the earlier row, which stays visible. The Council's ratification and its decision on an appeal use `is_live` with partial unique indexes so exactly one live row exists.
5. **`SYNCED` only on provider confirmation.** With no provider the state is `NOT_CONFIGURED`; a failed or unconfirmed push is `SYNC_PENDING` and retried; a sync problem never undoes a decision.
6. **The SAC reuses `committee_cases`** (stage `SAC`) rather than a new case table, so assignment scoping, scheduling, proceedings, versioned reports and the selection boundary behave exactly as for EVC / SHC.
7. **Non-demo applications are blocked** at the decision stage until an OFFICIAL decision policy exists, rather than defaulting to demo vocabulary.
8. **Administrators carry no decision powers.**

## Phase 7 decisions

1. **Verification is a separate act by a separate role** (`compliance_officer`, assigned per obligation). Evidence submission is an institution permission; the Bureau defines obligations but cannot verify them.
2. **No duration without a usable policy.** A clock exists only for a policy that exists in the application's frozen configuration; an unusable one yields a visible `NOT_ESTABLISHED` clock (a database CHECK forbids a date on it). A date an authorised person takes from a decision is a `SPECIFIED` clock with its citation, never labelled statutory.
3. **Clocks are immutable records of a decision made at the time** (trigger, original due date, policy snapshot). Approved pause time is added separately. A new rule version cannot move them.
4. **Pause only where the policy says.** The pause rule is data in the policy's `escalation` field, validated at authoring time; no seeded statutory policy allows it.
5. **Alerts re-check state.** Work is planned from a clock but decided at send time from the database; a nearer alert supersedes earlier late ones after an outage.
6. **PostgreSQL is the only queue.** No Redis, no in-memory state: a restart is a non-event.
7. **Validity alerts depend on a person's confirmation**, not on OCR; the expiry moment is the end of the stated date in UTC (convention flagged unverified).
8. **An obligation follows exactly one decision** (an Executive Committee decision version, or the Council's decision on an appeal), chosen by the person recording it, never inferred.
9. **API prefix `/api/post-approval`** (not `/api/compliance`) to avoid the Phase 5 SHC compliance routes — found by a failing test, not by inspection.

## Phase 8 decisions

1. **The integration boundary is ours.** Because no AICTE interface exists for us, we define a strict contract, label it DEMO ONLY, and make a real endpoint a replacement of one module (`INTEGRATION.md`). Nothing assumes AICTE will accept it.
2. **`SYNCED` remains the stored value for "confirmed"** (no data migration of meaning); the UI says "Confirmed". `FAILED` and `RECONCILIATION_REQUIRED` are new states with stricter rules than `SYNC_PENDING`.
3. **Fail closed in production** rather than warn: a start with development settings is refused.
4. **Append-only in the database**, in addition to the ORM guards, for the audit trail and recorded decisions; the limit (a superuser can drop a trigger) is documented rather than hidden.
5. **Rate limiting is in-process and says so**; the supplied nginx adds a limit in front; a distributed limiter is a documented gap.
6. **The first administrator is created by an operator command**, not by a seed, a default password or a public sign-up.
7. **Docker images install `requirements.lock`** (the exact versions the suites ran against) so builds are repeatable; `requirements.txt` states minimums.
