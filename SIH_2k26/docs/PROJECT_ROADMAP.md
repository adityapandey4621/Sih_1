# Project roadmap

Problem statement 26262 — Integrated AICTE approval workflow automation, compliance monitoring and alert management.

The product **supports** the official process. Authorised AICTE officials make every regulatory decision; the system records, routes, reminds and evidences.

Each phase ends with working, tested software and a short handoff. Later phases are planned here and **must not be started early**.

| Phase | Theme | Status |
|---|---|---|
| 1 | Design system, landing page, authentication, role-based shell | Implemented |
| 2 | Versioned rule catalogue, application intake, basic safe attachments | Implemented |
| 3 | Document intelligence, deficiency handling, scrutiny and re-scrutiny | Planned |
| 4 | EVC coordination, visits and reports | Planned |
| 5 | SHC compliance and hearings | Planned |
| 6 | Executive Committee decision records, SAC appeals, undertakings | Planned |
| 7 | SLA clocks, notifications, document validity and alerts | Planned |
| 8 | Authorised integration with AICTE systems, hardening, deployment | Planned |

## Phase 1 — foundation (implemented)

Design system, landing page with an illustrative workflow instrument, real authentication (Argon2id, server-side sessions, CSRF, throttling), controlled access requests and invitations, permission-driven navigation, dashboard shells for every role, audit log foundation.

## Phase 2 — rule catalogue and application intake (implemented)

Acceptance criteria and where they are verified (see `backend/tests/` and `frontend/src/**/*.test.tsx`):

- **Intake:** institutions create, save, resume, edit and submit applications; the checklist is generated from the approved configuration; submission is a validated, transactional, compare-and-set backend operation that records actor, time and the exact rule version, and cannot be repeated or raced. (`test_applications.py`)
- **Rule catalogue:** sources, categories, rule sets, versions, checklist items, route stages (with conditional stages) and deadline-policy definitions, with an explicit `draft → in review → published → retired` lifecycle; author and approver are separate roles; published content is immutable and frozen with a hash; overlapping configuration cannot silently become selectable; missing or ambiguous configuration never yields a guessed route. (`test_rules_lifecycle.py`)
- **Snapshots:** every submission stores the rule-version snapshot it was submitted under; later publication or retirement never rewrites it. (`test_applications.py`)
- **Attachments:** authenticated, size-limited, content-sniffed, privately stored uploads under generated keys, never marked verified. (`test_uploads.py`)
- **Security:** institution scoping enforced in services and queries; cross-institution access answers "not found" and is audited; refusals are audited; immutability enforced again at ORM flush time.
- **UI:** application list/new/draft editor/submitted record; rule catalogue, version, editor and review screens; works on desktop and mobile; verified in a real browser.

Explicitly **not** in Phase 2: OCR or extraction, evidence verification, deficiency/re-scrutiny, EVC, SHC, SAC, decisions, the deadline scheduler, notifications, email, malware scanning, external AICTE integration.

### Open rule-source gaps carried forward

No rule in the catalogue is verified against the official AICTE publication. See "Rule-source verification gaps" in `REQUIREMENTS.md`. The official-classified seed drafts therefore **cannot be published**, and the only selectable configuration is DEMO ONLY.

## Phase 3 — deferred from Phase 2 (planned)

First task: obtain and verify the official APH text (and corrigenda), then replace the demo configuration with verified, source-cited versions. Then:

- Document intelligence: PDF text extraction / OCR with page provenance and confidence; never a formal deficiency by itself.
- Authorised scrutiny and re-scrutiny queues; deficiency notices issued only by officers; corrected evidence as **new document versions**; re-scrutiny never auto-passes.
- Document lifecycle hardening: malware scanning, retention, versioned replacement after submission through an authorised correction mechanism, object storage behind the existing `LocalStorage` interface.
- Duplicate-application detection at intake (Stage 1 of the reference flow); intake-error return and permitted resubmission.
- Staff-side read access to submitted applications (scrutiny officers etc.) with assignment scopes.
- Per-user rule-configuration review queue notifications (needs the Phase 7 notification layer).

## Phases 4–8 (planned)

- **4** EVC need determination per the authorised mechanism, scheduling, visit reports. No invented auto-assignment algorithm.
- **5** SHC submissions, hearing schedule, recommendations.
- **6** Executive Committee decision records, eligible SAC appeals (appeal clock taken from the exact applicable clause and rule version), undertakings.
- **7** SLA clocks driven by versioned deadline policies (statutory separate from internal), notification delivery, document-validity alerts.
- **8** Integration with AICTE systems only once authorised (adapter boundary with mock until then), security review, deployment.
