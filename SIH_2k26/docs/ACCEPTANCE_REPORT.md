# Acceptance report — Phases 7 and 8

Every statement below is something that was **actually executed** on the development machine, with the result. Where something was *not* run, it is listed as **NOT RUN** or **UNVERIFIED** — never as passed.
No performance figure is reported because none was measured.

## 1. The machine and what it lacked

Windows 11 laptop; Python 3.13.1; Node 22; PostgreSQL 17 (portable, port 54329, used for the "PostgreSQL" runs); Docker Desktop 29.7 (used for the image builds and the stack smoke test below); Tesseract 5.x (portable, used for the "with Tesseract" runs);
real Chrome driven by Playwright. **Not available:** any cloud account or hosting platform, GitHub Actions, ClamAV, a TLS terminator, any AICTE interface, an independent reviewer.

## 2. Automated test results

| Suite | Result |
|---|---|
| Backend, **PostgreSQL 17**, real Tesseract (all tests including the PostgreSQL-only concurrency and append-only-trigger tests; none skipped) | **755 passed, 0 failed, 0 skipped** (34 min 36 s) — final run on the finished code |
| Backend, SQLite (the fast suite; PostgreSQL-only and Tesseract tests skip) | **743 passed, 0 failed, 12 skipped** (59 min 27 s) — final run |
| Frontend `tsc --noEmit` | clean |
| Frontend `vitest` | **188 passed (12 files), 0 failed** |
| Frontend `npm run build` | succeeded |
| `alembic upgrade head` → `alembic check` → `downgrade 0005` → `upgrade head` → `alembic check` (PostgreSQL) | all clean; `python -m app.db_protect`: triggers present after each upgrade, absent after the downgrade past `0008` |
| Lint | **No linter is configured for the frontend** (NOT RUN). Backend: `pyflakes` was run on the new modules during Phase 6; not re-run as a gate in Phase 8 (NOT RUN) |
| `npm audit` | 0 vulnerabilities |
| `pip-audit` (OSV) on the 45 pinned backend packages | no known vulnerabilities |

New in these phases: Phase 7 — compliance (14), validity (10), clocks / scheduler / alerts (25); Phase 8 — integration adapter and demo receiver over a **real socket** (29), sync state machine (12), hardening (16, one PostgreSQL-only), end-to-end journeys (7); frontend — post-approval (18), integration (5). The backend suite now collects **754** tests (it was 631 before Phase 7).
Tests the phases forced us to change are listed in §6 (nothing was weakened to make it pass; each change is explained).

## 3. End-to-end journeys (brief §3) and where each is verified

All run through the real HTTP API with synthetic data (`tests/test_e2e_journeys.py` unless stated). OCR: when a real Tesseract is present the document is a *scanned image read by OCR* and the test asserts the extraction method is `OCR`; otherwise it is a digital PDF and the test asserts `PDF_TEXT` (the method is recorded, so the two cannot be confused). **With Tesseract (the PostgreSQL run above) the OCR path was exercised.**

| # | Journey | Verified by | Result |
|---|---|---|---|
| 1 | Login and role permissions | `test_journey_1_…`, plus the per-phase role matrices | see §2 |
| 2 | Draft, category checklist, submission (incomplete blocked and named; frozen after submit; duplicate submit; cross-institution 404) | `test_journey_2_…` | see §2 |
| 3 | Secure upload, actual PDF/OCR processing, document versions (new version, old kept, downloads scoped) | `test_journey_3_…` | see §2 |
| 4 | Preliminary issue → authorised deficiency notice (a reviewer cannot issue) → institution response → re-scrutiny → completion | `test_journeys_4_to_9_…` | see §2 |
| 5 | Conditional EVC: selection (demo), scheduling, visit, report, completion → *a person determines* whether SHC applies | same | see §2 |
| 6 | Conditional SHC: compliance, review, findings, recommendation, completion | same | see §2 |
| 7 | Authorised decision (incomplete decision refused; reports decide nothing) → eligible appeal → SAC → Council outcome on appeal | same | see §2 |
| 8 | Compliance evidence → human verification → deadline / reminder lifecycle (alerts to the verifier while evidence awaits verification; none after verification) → document validity (candidate → verified date → clock → renewal keeps old alerts) | same + `test_compliance.py`, `test_validity.py`, `test_clocks.py` | see §2 |
| 9 | External-sync failure, retry and reconciliation (transient failure → pending → confirmed once; receiver holds each message once) | same + `test_integration_sync.py`, `test_integration_adapter.py` | see §2 |
| — | Invalid routes, duplicates, stale updates, unauthorised access | `test_invalid_duplicate_stale_and_unauthorised_…` and the per-phase suites | see §2 |
| — | Worker / database failures: a scheduler that meets a database error survives and resumes; a killed worker's job is recovered by lease expiry; API answers a DB outage generically and `/api/ready` says not ready | `test_a_scheduler_that_meets_…`, `test_the_api_answers_a_database_outage_…`, `test_a_worker_that_dies_mid_job_loses_nothing`, `test_jobs.py` | see §2 |

**The mock is not proof of integration.** Journey 9 proves this application's behaviour around an adapter that fails, answers garbage, lies about a hash, or loses an acknowledgement. It proves nothing about any AICTE system.

## 4. Browser verification (real Chrome, real API, real PostgreSQL, real Tesseract)

`phase8.mjs`: one institution submits an application with two documents (one carrying a validity line); scrutiny; Bureau observation; Executive Committee records an LoA; Bureau issues it; Bureau records an obligation; the institution submits evidence (confirmation says *not verification*; status stays "awaiting verification"); the compliance officer requests a correction (internal note is not shown to the institution), then verifies; the validity candidate appears with its document version and page, is confirmed as a date; the institution uploads a renewal (stored as a claim; the earlier date's clock stays RUNNING); deadlines pages for Bureau and institution; the integration page; access refusals (institution at `/app/deadlines`, Executive Committee at `/app/integration`); security headers and request id on API responses; `/api/ready`; six pages at 390 px for horizontal overflow; console errors.
**Result: 39 of 39 checks passed, 0 console errors, no horizontal overflow at 390 px** (run twice: once before and once after the last backend fixes). Screenshots were reviewed by eye (`p8-*.png`); two wording issues were fixed.
The Phase 6 browser journey was **re-run as a regression gate: 53 of 53 checks passed**, no console errors, no overflow at desktop or mobile widths.
The browser run found one real defect that no automated test had: two documents uploaded together produced an HTTP 500 (item 2 in §6).

## 5. Deployment artefacts — what was verified

| Artefact | Verified how | Result |
|---|---|---|
| `backend/Dockerfile` | `docker build` | built (577 MB); runs as UID 10001; Tesseract present in the image |
| `frontend/Dockerfile` + nginx | `docker build` | built (76 MB) on the second attempt (the first failed on a network error inside `npm ci`: an environment limit, not a code fault) |
| `docker-compose.yml` (local stack) | `docker compose up -d --build` from clean volumes | db healthy → `migrate` ran all 8 migrations on PostgreSQL 17 → api healthy → worker (Tesseract available) and scheduler running → web healthy |
| Through nginx | `curl` | `/api/health`, `/api/ready` (`schema_current: true` — the Alembic head check works inside the container), SPA root and deep link, one set of security headers (no duplicates), login, CSRF refusal (403) for a wrong origin and a missing token, audit IP = the real peer (a spoofed `X-Forwarded-For` is not recorded) |
| `docker-compose.prod.yml` | `docker compose config` (merges correctly: production environment, secrets as files, database port not published, read-only roots); **NOT started** (needs ClamAV, real secrets, TLS) | syntax and merge verified only |
| Production fail-closed | `docker run -e ENVIRONMENT=production` | refuses to start with development settings and lists why; starts with real settings and a `DATABASE_URL_FILE` secret; API docs disabled |
| `ops/backup.sh`, `ops/restore.sh` | run in a `postgres:17-alpine` container against a migrated, seeded database | backup written with checksums; `verify` restored it into a throw-away database (revision 0008, row counts, triggers) and dropped it; a **one-byte corruption was detected and the restore refused** |
| `ops/backup.ps1`, `ops/restore.ps1` | **NOT RUN** — the portable PostgreSQL on this machine has no `pg_dump`/`pg_restore` | UNVERIFIED |
| `python -m app.create_admin` | run against a scratch database | created the administrator; refused a duplicate and a short password |
| `.github/workflows/ci.yml` | **NOT RUN** (no GitHub Actions here) | UNVERIFIED until its first run |
| Any cloud / hosting deployment | **NOT DONE** | see `DEPLOYMENT.md` §5 for the exact remaining manual steps |

## 6. Defects found by the Phase 7/8 validation, and fixed

These were found because the journeys, the browser run and the container smoke test exercised real paths. Each has a regression test unless noted.

| # | Defect | Origin | Effect | Fix / test |
|---|---|---|---|---|
| 1 | `/api/compliance/*` collided with the Phase 5 SHC route `/api/compliance/{round_id}` | Phase 7 (mine) | new endpoints answered 422 | prefix `/api/post-approval`; `test_compliance.py` |
| 2 | Two documents uploaded at the same moment: both enqueued a cross-check, the unique index turned the loser into **HTTP 500** | **Phase 3** (latent since then) | a real user uploading two files together could get an error | savepoint + coalesce; PostgreSQL-only concurrency test, **verified to fail on the old code** |
| 3 | `ALLOWED_ORIGINS=http://localhost:8080` (a plain value, as `.env.example` and compose use) crashed the process at start-up (pydantic-settings JSON-parsed it first) | **Phase 1 config** | `.env.example` as shipped would not start | `NoDecode` + CSV/JSON parsing; test |
| 4 | `docker compose up` failed unless `SEED_PASSWORD` was set (compose interpolates every service, including the profile-only `seed`) | Phase 8 (mine) | the documented demo command failed | `${SEED_PASSWORD:-}`; the seed refuses an empty value itself |
| 5 | Obligations could attach only to an Executive Committee decision, but an approval granted on appeal is the **Council's** | Phase 7 (mine) | conditions after a successful appeal could not be recorded | `council_decision_id` source; tests |
| 6 | The Regulation Bureau held two dashboard permissions, violating "exactly one per role" | Phase 7 (mine) | an existing invariant test failed | Bureau reaches compliance via a navigation item, not a second dashboard |
| 7 | Behind any reverse proxy every client appeared as the proxy (per-IP throttle would lock everyone out; audit IPs wrong) | **Phase 1** | operational DoS, wrong audit data | `TRUSTED_PROXY_COUNT`; tests; verified in the container (real peer recorded, spoof ignored) |
| 8 | The application's INFO logs (access log, scheduler, worker) were dropped under uvicorn (no handler on the `app` logger) | Phase 8 (mine) | the access log would have been invisible | handler configured in `create_app` |
| 9 | Existing tests that `pytest.raises(RuntimeError)` around API calls (7 tests) relied on the server exception reaching the test client; production now answers a generic **500** | Phase 8 change | 7 failures | each now asserts the generic 500 (no exception text), the unchanged rollback guarantees, and that the retry succeeds |
| 10 | Frontend test flakiness under CPU load (1 s default wait, 5 s test timeout) | test config | spurious red | waits and timeouts raised; assertions unchanged |
| 11 | `external_syncs.status` was `VARCHAR(16)`; the new state `RECONCILIATION_REQUIRED` is 23 characters. SQLite does not enforce lengths, so only the **PostgreSQL run** caught it: a garbled receipt would have turned an issued outcome into a **500** | Phase 8 (mine) | the "reconciliation required" path was broken on the production database | column widened in migration `0008`; both sync tests now pass on PostgreSQL |
| 12 | A streamed request body without a declared length hit FastAPI's own "error parsing the body" 400 instead of the size cap's 413 | Phase 8 (mine) | the cap answered unclearly for chunked uploads | the middleware answers one clear 413; test |
| 13 | Tests compared a PostgreSQL timestamp rendered in the session zone (IST on this machine) as if it were UTC; and a path-name filter in an old test matched `post-approval` ("approv") | test code | 4 spurious failures on PostgreSQL only | compare instants; the old filter now excludes the new workflow with a comment |

How the PostgreSQL results were reached: a first full run found 5 failures (items 11 and 13, three tests; fixed), and a final full run on the finished code is reported in §2. SQLite-only runs missed item 11 — **the PostgreSQL suite is the one that counts**.

Tests whose *expectations* changed because the product changed (reviewed one by one): institution permission list (+`compliance:read_own`, `compliance:submit`); auditor permissions (+`integration:read`, read-only oversight); the evidence-endpoint role matrices (Phase 6, earlier); the AppShell build-text assertion.

## 7. What is NOT verified (summary; full list in `GAPS_AND_BLOCKERS.md`)

* **Any real AICTE integration.** No interface exists for us. The contract is ours; the HTTP adapter was exercised only against the demo receiver.
* **Any deployment.** No cloud, no TLS, no ClamAV, no production run. `docker-compose.prod.yml` was only merged and validated, not started.
* **The CI workflow, the PowerShell backup scripts.**
* **Regulatory sources**: every duration, convention and policy is DEMO ONLY or `NOT_ESTABLISHED` (`COMPLIANCE_AND_DEADLINES.md` G-P7-1…9; `DECISIONS_AND_APPEALS.md`; `APH_VERIFICATION.md`).
* **Security assurance**: no independent assessment; see `SECURITY_REVIEW.md`.
* **Performance and scale**: not measured; no figure is claimed.
* **Accessibility audit** beyond semantic markup, labels, focus handling and the 390 px overflow check: not performed.
