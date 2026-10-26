# Requirements

## Product constraints

1. The system supports AICTE's applicable process; authorised humans decide.
2. No claim of official AICTE endorsement or connectivity. No fake seals, contacts, policies or live data.
3. No regulatory values (categories, SLA durations, appeal windows, eligibility) in UI code. They arrive later from a versioned rule catalogue.
4. Illustrative content is always labelled as illustrative.

## Roles (Phase 1)

| Role key | Who | Notes |
|---|---|---|
| `institute_user` | Applicant institution | Created from an approved access request, scoped to one institution |
| `scrutiny_officer` | Scrutiny | |
| `evc_coordinator` | EVC assignment workflow | No auto-assignment algorithm in Phase 1 |
| `evc_member` | Expert visit committee member | |
| `shc_member` | Standing/hearing committee member | |
| `executive_committee` | Bureau / Executive Committee | Shell only; no decision logic |
| `sac_member` | Appeals committee | No eligibility rules invented |
| `rules_admin` | Maintains approved rule versions | **No** decision permissions |
| `auditor` | Audit and monitoring | Read-only |
| `platform_admin` | Provisions users, reviews access requests | Not a regulatory role |

`platform_admin` is an addition to the brief's list: the brief requires an "authorised administrative process" for provisioning, and keeping it separate from `rules_admin` avoids giving rule maintainers extra power.

## Functional requirements (Phase 1)

- FR-1 Landing page with workflow explanation and illustrative instrument.
- FR-2 Sign-in with email + password; uniform failure message; throttling; show/hide password.
- FR-3 Institution access request (name, optional identifier, applicant name, official email, reason); persisted as `pending`; confirmation says *received*, not approved.
- FR-4 Administrator reviews requests; approval issues an invitation for an `institute_user`.
- FR-5 Administrator invites official users with an explicit role; invitation is single-use and expires.
- FR-6 Invitation acceptance sets the password (min. 12 characters).
- FR-7 Session persistence, logout, idle/absolute expiry, explicit "session expired" behaviour in the UI.
- FR-8 Shared shell with role-aware navigation; dashboards for every role; access-denied for the rest.
- FR-9 Audit events recorded for sign-in, failed sign-in, sign-out, access requests, invitations, user changes; viewable by auditors and administrators.
- FR-10 Password recovery and email verification: truthfully reported as unavailable until an email provider is configured.

## Non-functional

- Strict TypeScript; accessible forms and keyboard navigation; WCAG AA contrast target; `prefers-reduced-motion` respected.
- Responsive from 360 px to desktop.
- No secrets in the repository; `.env.example` has placeholders only.
- Logs never contain passwords, tokens or invitation links.
