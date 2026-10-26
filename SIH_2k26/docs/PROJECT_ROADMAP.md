# Project roadmap

Problem statement 26262 — Integrated AICTE approval workflow automation, compliance monitoring and alert management.

The product **supports** the official process. Authorised AICTE officials make every regulatory decision; the system records, routes, reads, reminds and evidences.

Each phase ends with working, tested software and a short handoff. Later phases are planned here and **must not be started early**.

| Phase | Theme | Status |
|---|---|---|
| 1 | Design system, landing page, authentication, role-based shell | Implemented |
| 2 | Versioned rule catalogue, application intake, basic safe attachments | Implemented |
| 3 | Secure document processing: versions, OCR, extraction, preliminary evidence checks | Implemented |
| 4 | Authorised scrutiny and re-scrutiny, formal deficiency process, corrections as new versions | Implemented (see `SCRUTINY.md`) |
| 5 | Conditional route engine; EVC (selection boundary, schedule, visit, report) and SHC (compliance, hearing, recommendation); hand-off to the decision stage | Implemented (see `COMMITTEE.md`) |
| 6 | Executive Committee decision records, eligible SAC appeals, undertakings | Planned |
| 7 | SLA clocks, notifications, document validity and alerts | Planned |
| 8 | Authorised integration with AICTE systems, hardening, deployment | Planned |

(Phase 3 was re-scoped to *document processing*; scrutiny and the deficiency process moved to Phase 4. Phase 5 now covers EVC **and** SHC, so decisions/appeals are Phase 6, the SLA engine Phase 7 and integration/hardening Phase 8.)

## Phase 1 — foundation (implemented)

Design system, landing page with an illustrative workflow instrument, real authentication (Argon2id, server-side sessions, CSRF, throttling), controlled access requests and invitations, permission-driven navigation, dashboard shells for every role, audit log foundation.

## Phase 2 — rule catalogue and application intake (implemented)

Sources, categories, rule sets and versions with an explicit draft → in review → published → retired lifecycle (separate author and approver, frozen snapshots and hashes, overlap and verification gates); institution applications (DRAFT/SUBMITTED) bound to a rule version with compare-and-set edits and submission; private uploads. **No rule in the catalogue is verified against the official AICTE publication**; the selectable configuration is DEMO ONLY. See `RULE_CATALOGUE.md`.

## Phase 3 — document processing (implemented)

See `DOCUMENT_PROCESSING.md`. Acceptance criteria and where they are verified:

- **Secure upload** (`test_uploads.py`, `test_evidence_api.py`): authenticated, institution-scoped, draft-only, size/type/signature checks, generated storage keys, private storage, access-checked downloads, no false success on storage failure, duplicate requests create no duplicate version, replacement preserves the previous version.
- **Versioning** (`test_jobs.py`): slot → version chain; superseded versions kept; old queued work cancelled; new versions do not inherit results; findings recomputed against current versions with history kept.
- **Pipeline** (`test_processing_units.py`): technical validation; digital PDF text with page numbers; OCR per page with confidence (**tested against a real Tesseract 5.4**); mixed documents; encrypted/corrupt PDFs fail with specific codes and never reach OCR; configuration-driven field extraction with page and version provenance; uncertainty and "unable to extract" instead of guesses.
- **Evidence checks** (`test_processing_units.py`, `test_evidence_api.py`): missing ≠ unreadable ≠ extraction failure ≠ inconsistency; findings carry check, version, pages, values and checklist reference; nothing changes application status; no deficiency notice exists (no such endpoint or table; every finding states it is not one).
- **Reliability** (`test_jobs.py`): durable PostgreSQL jobs; leases and restart recovery; bounded retries with backoff; permanent failures terminate visibly; stale workers cannot write; retries replace results; concurrent claims (PostgreSQL); logs contain no document content or paths.
- **UI**: state wording from the server; institution processing panel with live refresh; scrutiny evidence review with page preview beside values, text, findings, versions and history; read-only; verified in a real browser against real Tesseract (desktop and 390 px).

Explicitly **not** in Phase 3: formal deficiency notices, scrutiny/re-scrutiny workflow, approval/rejection logic, EVC/SHC/SAC, SLA and notification engine, custom ML, queues or microservices beyond PostgreSQL, document replacement after submission.

## Phase 4 — scrutiny, notices, responses (implemented)

Queue and scoped reviewer workspace; assignment boundary (demo only); structured reviewer findings; formal notices issued only by an authorised issuer with confirmation, idempotency and atomicity; institution notice and response pages with corrected evidence as new versions; re-scrutiny rounds; completion and route-driven hand-off (never approval); persistent in-app notifications; audit throughout. **Not** in Phase 4: EVC, SHC, Executive Committee, SAC, SLA/reminder engine, external messaging, AICTE integration. Open gaps G-P4-1…6 in `SCRUTINY.md`. **Closing work (2026-10-27):** the official APH 2024-27 was read (`APH_VERIFICATION.md`): no response period, issuing officer, template or round cap is stated; re-scrutiny is limited to the deficiencies already raised (enforced). **Next:** Phase 5, with the Annexure-2 document checklists and a corrigenda check as catalogue work that can run alongside.

## Phase 5 — route engine, EVC and SHC (implemented)

Full description in `COMMITTEE.md`. The route engine reads the next stage from the application's frozen route (never a universal EVC-plus-SHC sequence); conditional stages need a recorded human determination; an EVC / SHC case is created idempotently; a **selection provider boundary** (demo mode labelled `DEMO_ONLY`, real portal not connected); schedules with history; an EVC visit with observations, private evidence and an immutable versioned report; an SHC process with institution compliance (new document versions), findings, hearing records and a versioned recommendation; a **decision hand-off record** once every required stage is complete; in-app notifications, audit and persistent state throughout. **Not** in Phase 5: Executive Committee decisions, SAC appeals, post-approval compliance, the SLA / reminder engine, AICTE integration, any selection algorithm, any official committee checklist (gaps G-P5-1…9). **Recommended next:** encode the verified EVC checklist (Annexure-3 / 4) and committee composition as OFFICIAL policies once the corrigenda are checked, then Phase 6.

## Phase 4 — original plan, as deferred from Phase 3

- ~~First task: obtain and verify the official APH text~~ — done for the 2024-27 edition (`APH_VERIFICATION.md`); encoding Annexure-2 checklists and checking later corrigenda remain.
- Reviewer **assignment** scopes (today every scrutiny officer sees every submitted application).
- Authorised **deficiency process**: an officer, not OCR, decides what is a deficiency; institution notices; corrected evidence as **new versions** after submission through a controlled correction mechanism; re-scrutiny never auto-passes.
- Hardening: real ClamAV integration test, encryption/retention policy for stored text, duplicate-application detection, object storage behind `LocalStorage`, OCR languages beyond English, table/layout extraction where a requirement justifies it.

## Phases 5–9 (planned)

- **5** EVC need determination per the authorised mechanism, scheduling, visit reports. No invented auto-assignment algorithm.
- **6** SHC submissions, hearing schedule, recommendations.
- **7** Executive Committee decision records, eligible SAC appeals (clock from the exact applicable clause and rule version), undertakings.
- **8** SLA clocks from versioned deadline policies (statutory separate from internal), notification delivery, document-validity alerts.
- **9** Integration with AICTE systems only once authorised (adapter boundary with mock until then), security review, deployment.
