# Security and reliability review (Phase 8)

Scope: the whole repository as of the Phase 8 commit. Method: reading the code paths for each concern, running the existing suites, writing tests for each fix, and the dependency tools below.
**This is a self-review by the engineering team. It is not an independent assessment, it does not certify the system secure or compliant with any standard, and it cannot find what it did not look for.**
An independent penetration test and a data-protection assessment are still required before real institutional data is processed (gap G-P8-1).

## 1. What was checked, what was found, what was done

| # | Area | Finding (before Phase 8) | Severity | Action | Test |
|---|---|---|---|---|---|
| 1 | Authentication | Argon2id; opaque session tokens stored hashed; fresh token on every sign-in; idle + absolute expiry; revocation on logout; per-account and per-IP failure throttling; constant-time-ish dummy verify for unknown accounts. **Sound.** | — | none | `test_auth.py` |
| 2 | Session cookies | `HttpOnly`, `SameSite=Lax`, `Secure` in production. **Sound.** | — | none | `test_auth.py` |
| 3 | CSRF / CORS | Double-submit token + `Origin` allow-list on every state-changing request; no CORS middleware (same-origin by design). **Sound.** | — | documented; the supplied nginx makes the browser see one origin | `test_auth.py` |
| 4 | RBAC | Role → permission table is the single source; every endpoint re-checks; scope (institution, assignment) checked in the services; out-of-scope = 404 + audit. Large role × endpoint matrices exist from earlier phases. **Sound.** Phase 7/8 added the same checks for the new endpoints. | — | extended | `test_compliance.py`, `test_validity.py`, `test_clocks.py`, `test_integration_sync.py` |
| 5 | Client address behind a proxy | `X-Forwarded-For` was ignored entirely, so behind any reverse proxy **every client looked like the proxy**: the per-IP sign-in throttle (30 failures / 15 min) would lock *everyone* out after 30 failures from anyone, and audit IPs were wrong. | **High** (operational DoS + audit quality) | `TRUSTED_PROXY_COUNT`; header used only when proxies are declared, taking the entry our proxy added, validated as an IP; nginx sends exactly one entry | `test_hardening.py::test_forwarded_for_…` |
| 6 | Security headers | None were set by the API. | Medium | `nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, `Permissions-Policy`, COOP/CORP, API CSP `default-src 'none'`, `no-store` on API responses, HSTS in production; nginx sets the SPA's CSP | `test_hardening.py` |
| 7 | Error responses | FastAPI's `422` echoed the submitted values (`input`) — a mistyped password field would be reflected in the response; unhandled errors had no request id; SQLAlchemy messages can carry parameters. | Medium | validation answers carry `loc/msg/type` only; a last-resort handler answers `500 {code, request_id}`; logs carry the exception *type* and code locations, never its message | `test_hardening.py` |
| 8 | Request size | JSON bodies were unbounded (uploads were limited per file). | Medium | `MAX_REQUEST_MB` cap, enforced on the declared length and while streaming; nginx `client_max_body_size` | `test_hardening.py` |
| 9 | Rate limiting | Only sign-in failures and access requests. | Medium | per-client sliding-window limits for reads, writes and sign-in endpoints (in-process; **not distributed** — see G-P8-4); nginx `limit_req` in front | `test_hardening.py` |
| 10 | Access log | None. | Low | one line per request with request id, method, path (no query string), status, duration; never bodies, cookies or headers | `test_hardening.py` |
| 11 | Production configuration | Nothing stopped a production start with the development password, SQLite, the demo seed password, `http://` origins or the mock integration. | **High** (misconfiguration) | `Settings.production_problems()`; the API **refuses to start** listing the problems | `test_hardening.py` |
| 12 | Secrets | Only environment variables / `.env`. | Medium | `*_FILE` secrets (Docker/Kubernetes), `secrets/` git-ignored, no secret in images or compose files; `.env.example` placeholders only | `test_hardening.py` |
| 13 | Audit integrity | The audit table was append-only *by convention*: nothing in the ORM or the database prevented an `UPDATE`/`DELETE`/`TRUNCATE`. Recorded decisions, observations, evidence and reviews had ORM guards only. | **High** (the audit trail is the accountability record) | ORM guard on `AuditEvent` and the integration log; **PostgreSQL triggers** rejecting `UPDATE`/`DELETE`/`TRUNCATE` on 10 tables (migration `0008`); `python -m app.db_protect` verifies they exist | `test_hardening.py` (PostgreSQL), `test_integration_sync.py` |
| 14 | Private uploads | Appeal and official-outcome files were never scanned; the scanner policy applied only to application documents. | Medium | all private uploads (compliance evidence, renewals, appeals, outcomes) go through the scanner; infected = refused and deleted; no scanner = `NOT_SCANNED` (never "clean") and refused when scanning is required (production) | `test_compliance.py::test_malware_scan_policy_is_honest` |
| 15 | File downloads | Authorised endpoints only; generated storage keys; `Content-Disposition: attachment`; `nosniff`; `Cache-Control: private, no-store`. **Sound.** | — | applied to the new endpoints | `test_compliance.py`, `test_validity.py` |
| 16 | Injection | All database access is through SQLAlchemy with bound parameters; no string-built SQL with user input (the only f-strings in SQL are table names from a constant list in `db_protect.py`). Search boxes use `ILIKE` with bound values; `%`/`_` act as wildcards (a usability quirk, not an injection). | — | none | review |
| 17 | Idempotency / duplicates | Present for every consequential create since Phase 4; extended to obligations, evidence, reviews, renewals, alerts (unique work keys), notifications (dedupe keys), sync (stable message id + `Idempotency-Key`). | — | extended | `test_compliance.py`, `test_clocks.py`, `test_integration_adapter.py` |
| 18 | Worker / scheduler recovery | Leases + compare-and-set claims (documents since Phase 3; alerts in Phase 7); recovery after a kill; bounded retries; failures stay visible. | — | extended | `test_clocks.py`, `test_jobs.py`, `test_e2e_journeys.py` |
| 19 | Outbound integration | None existed beyond a stub. | — | strict contract; HTTPS only in production; signed requests; `Idempotency-Key`; timeouts; bounded retries; rate limit; circuit breaker; no redirects followed; response size capped; receipts verified against message id and hash; log without bodies or secrets | `test_integration_adapter.py` |
| 20 | First administrator | The README admitted there was no bootstrap command; the demo seed is (correctly) refused in production. | Medium (deployability) | `python -m app.create_admin` (password prompted or read from a file, never from argv) | manual |
| 21 | Dependencies | — | — | `pip-audit` (OSV) on the 45 pinned backend packages: **no known vulnerabilities**; `npm audit`: **0 vulnerabilities** (run on the development machine, 2026-10-27; re-run in CI) | `.github/workflows/ci.yml` |

## 2. Defences by design worth stating

* Administrators hold no decision, scrutiny, verification, appeal or integration-management power; roles never imply each other's permissions.
* Uploading is never verification; a deadline passing is never a penalty; an external sync problem never undoes a decision; the software never claims an email/SMS or an AICTE update that did not happen.
* Rule configuration is frozen per application; later publications cannot move a recorded deadline or change what an application was evaluated against.
* The ORM guards (`app/guards.py`) and — on PostgreSQL — triggers make the audit trail, recorded decisions, observations, appeal documents, evidence, reviews, clock history and integration log append-only.

## 3. Unresolved risks (not fixed; documented)

| ID | Risk | Why it remains | Mitigation today |
|---|---|---|---|
| G-P8-1 | No independent penetration test or data-protection assessment | outside this project's means | do not process real institutional data until done |
| G-P8-2 | Malware scanning is optional in development and needs a ClamAV service; none was run here | no scanner on the dev machine | production refuses uploads without one; compose adds ClamAV (untested) |
| G-P8-3 | Private files are on a local volume (`LocalStorage`); no object storage, no encryption at rest in the application | not built | encrypt the volume / use encrypted disks; backups must be encrypted |
| G-P8-4 | Rate limiting is per process, in memory | a distributed limiter needs shared state | nginx `limit_req` in front; a WAF / platform limit is recommended |
| G-P8-5 | Sessions are in PostgreSQL; no device/session management screen, no MFA | not built | strong password policy (12+), throttling; MFA is a recommended addition |
| G-P8-6 | No retention / erasure policy or data-subject workflow; records are append-only | needs a legal decision | document before go-live |
| G-P8-7 | No email/SMS, so invitations and recovery are manual operator procedures | no provider | operator runbook (`docs/OPERATIONS.md`) |
| G-P8-8 | The PostgreSQL triggers can be dropped by a superuser / table owner | inherent | restrict those credentials; verify with `app.db_protect`; ship audit logs off-box |
| G-P8-9 | The container images, compose files, nginx and CI have been validated syntactically and (where stated in the acceptance report) built, but never run in production | no hosting available | `docs/DEPLOYMENT.md` lists the manual steps |
| G-P8-10 | The CSP for the SPA allows inline styles (`style-src 'unsafe-inline'`) because some components set `style` attributes | removing it needs a UI refactor | scripts remain restricted to `'self'` |
| G-P8-11 | OCR and PDF parsing (Tesseract, PyMuPDF) process untrusted files in the API/worker container | inherent | the worker is a separate process; run it with no network and a read-only root where the platform allows; keep PyMuPDF updated (it is AGPL: see `docs/DECISIONS.md`) |

## 4. How to re-run this review

```
cd backend && python -m pytest -q                       # includes the hardening tests
cd backend && python -m alembic check && python -m app.db_protect
pip-audit -r requirements.lock --no-deps --disable-pip   # (or in CI)
cd frontend && npm audit --audit-level=high
```
