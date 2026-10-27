# Remaining gaps, blockers and external dependencies

Honest list as of the end of Phase 8. "Blocker" = something outside this repository's control that must happen before real use. Nothing here is hidden elsewhere; each item points to the document with the detail.

## A. External dependencies (blockers for real use)

| # | Dependency | Why it blocks | Owner |
|---|---|---|---|
| B1 | **A real AICTE interface**: endpoints, authentication, field definitions, error codes, rate limits, a test environment — supplied in writing by an authorised party | the integration boundary is project-defined and **unverified**; the default mode sends nothing (`INTEGRATION.md`) | AICTE / the sponsoring team |
| B2 | **Verified rule configuration**: the Annexure-2 document checklists encoded as OFFICIAL, the December 2025 corrigendum verified, later corrigenda checked, committee composition and checklists (`APH_VERIFICATION.md`, `REQUIREMENTS.md`, G-P5-*, G-P6-*) | until then only DEMO ONLY configuration is selectable; non-demo applications are blocked at the decision stage by design | rule authors + approvers |
| B3 | **Verified durations and conventions**: internal service levels, response periods, counting convention and cut-off time (calendar vs working days), document-validity expiry convention, reminder / escalation requirements, which statutory deadlines may be paused (`COMPLIANCE_AND_DEADLINES.md` G-P7-1…9) | the engine refuses to invent them; every clock today is `DEMO ONLY` or `NOT_ESTABLISHED` | AICTE / policy owners |
| B4 | **A hosting environment and accounts**: PostgreSQL with point-in-time recovery, TLS certificate and DNS, container platform or VM, a secrets store, ClamAV, off-site encrypted backups | nothing has been deployed anywhere (`DEPLOYMENT.md` §5) | the deploying team |
| B5 | **An independent security assessment and a data-protection / retention decision** | this repository's self-review is not a substitute (`SECURITY_REVIEW.md` G-P8-1, G-P8-6) | the institution |
| B6 | **Official roles and designations mapped to application roles** (Bureau, Executive Committee, Council, SAC, compliance verifier): which real office holds which capability | the roles are application capabilities, not claimed AICTE designations | AICTE / administrators |
| B7 | **An email / SMS provider** if out-of-app delivery is wanted | no channel exists; the product says so everywhere | the deploying team |

## B. Engineering gaps (we could fix; not done)

| # | Gap | Impact | Where |
|---|---|---|---|
| E1 | Private files are on a local volume; no object-storage adapter, no application-level encryption at rest | scaling across hosts and at-rest encryption rely on the platform | `SECURITY_REVIEW.md` G-P8-3 |
| E2 | Rate limiting is in-process | per-process, not distributed; rely on nginx / WAF in front | G-P8-4 |
| E3 | No MFA, no session-management screen | account takeover risk if a password leaks | G-P8-5 |
| E4 | The SAC "further scrutiny / EVC" recommendation does not start a second cycle | a person must act outside the product | `DECISIONS_AND_APPEALS.md` G-P6-7 |
| E5 | A correction to an issued decision does not move the appeal window or rebind obligations | documented behaviour; a rule decision is needed | G-P6-9, G-P7-9 |
| E6 | Scheduler health is a page and an endpoint; there is no metrics / alerting integration (Prometheus, Sentry, …) | operators must watch `/api/clocks/health` and the logs | `OPERATIONS.md` |
| E7 | The frontend has no linter configured; the CI workflow runs type-check, tests and build only | style / a11y lint not enforced | README |
| E8 | The SPA CSP allows inline styles | see G-P8-10 | `SECURITY_REVIEW.md` |
| E9 | `LocalStorage` and OCR/PDF parsing run in the API/worker containers | isolate the worker where the platform allows | G-P8-11 |
| E11 | `clock_sources.sync` and `validity.sync_candidates` rescan the open subjects each cycle (every `SCHEDULER_SYNC_SECONDS`, default 60 s). Fine for a pilot (hundreds to low thousands of open cases); not measured at AICTE scale, and no performance figure is claimed | an incremental / event-driven derivation would be needed for very large volumes | `COMPLIANCE_AND_DEADLINES.md` |
| E10 | A real ClamAV and a real Tesseract inside the production image have not been exercised here (Tesseract ran on the host; ClamAV did not run at all) | first deployment must run the smoke checks in `DEPLOYMENT.md` | `ACCEPTANCE_REPORT.md` |

## C. What would make each phase "verified" rather than "implemented"

* **Phases 1–7** — real rule configuration (B2/B3) and real users acting under real roles (B6).
* **Phase 8** — B1 for the integration; B4 for deployment and a clean-machine CI run; B5 for security.

## D. Things that are deliberately not built

Automatic approval / rejection; any consequence for a missed deadline or unmet obligation; letter text, signatures or seals; browser automation or direct database access to any external system; invented deadlines, durations or fees; any claim that an email, SMS or AICTE-portal update happened when it did not.
