# Project roadmap

Problem statement 26262 — Integrated AICTE approval workflow automation, compliance monitoring and alert management.

The product **supports** the official process. Authorised AICTE officials make every regulatory decision; the system records, routes, reminds and evidences.

Each phase ends with working, tested software and a short handoff. Later phases are planned here and **must not be started early**.

| Phase | Theme | Status |
|---|---|---|
| 1 | Design system, landing page, authentication, role-based shell | Implemented |
| 2 | Versioned rule catalogue, application intake and document checklist | Planned |
| 3 | Document intelligence, deficiency handling, scrutiny and re-scrutiny | Planned |
| 4 | EVC coordination, visits and reports | Planned |
| 5 | SHC compliance and hearings | Planned |
| 6 | Executive Committee decision records, SAC appeals, undertakings | Planned |
| 7 | SLA clocks, notifications, document validity and alerts | Planned |
| 8 | Authorised integration with AICTE systems, hardening, deployment | Planned |

## Phase 1 — foundation (implemented)

Acceptance criteria:

- Public landing page with an interactive, clearly illustrative workflow instrument; no fake statistics or official branding.
- Real sign-in against PostgreSQL-backed users: Argon2id hashes, server-side sessions in `HttpOnly` cookies, CSRF protection, login throttling, explicit expiry/logout.
- Controlled institution access request (persisted, reviewed by an administrator). No self-assigned roles.
- Official accounts created only through administrator-issued, single-use invitations.
- One shared authenticated layout; navigation and routes are derived from the permissions the backend returns.
- Dashboard shells for all eight role families, with honest empty states.
- Audit event log foundation; read-only view for auditors.
- Alembic migrations, seed script, automated backend tests, frontend type-check/build.

Not in Phase 1: any application, document, scrutiny, EVC, SHC, EC, SAC or SLA logic; email delivery; external identity integration.

## Phase 2 — rule catalogue and intake (planned)

First task: a versioned rule-catalogue schema (rule set, version, effective dates, approval state) administered by the rules administrator, plus the `application` aggregate and its checklist generated from an *approved* rule version. No regulatory values are to be hard-coded in the UI.

## Phases 3–8 (planned)

- **3** Document upload and storage, validity extraction, deficiency notices, scrutiny / re-scrutiny queues with every decision attributed to a person.
- **4** EVC need determination per the authorised mechanism, scheduling, visit reports. No invented auto-assignment algorithm.
- **5** SHC submissions, hearing schedule, recommendations.
- **6** Executive Committee decision records, eligible SAC appeals, undertakings.
- **7** SLA clocks driven by versioned rules, notification delivery, document-validity alerts.
- **8** Integration with AICTE systems only once authorised, security review, deployment.
