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
| `rules_admin` | Rule **author**: drafts configuration, cites sources, submits for review | **No** approval or decision permissions |
| `rules_approver` | Rule **approver**: requests changes, publishes, retires | Cannot edit drafts; cannot approve a version they contributed to; **no** decision permissions |
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

## Functional requirements (Phase 2)

- FR-11 Institution users create, save, resume, edit and submit applications for their own institution only; the institution always comes from the signed-in user's stored association.
- FR-12 Category and academic year select the applicable configuration; the checklist, fields and route come from the approved, published rule version, never from interface code.
- FR-13 Required vs optional items are shown with an explanation, source and clause reference. "Supplied" never means "verified".
- FR-14 Submission is a validated backend operation, recorded once, with actor, time and the exact rule version and a frozen snapshot; submitted applications cannot be edited as drafts.
- FR-15 The rule catalogue has an explicit draft → in review → published → retired lifecycle with separated author and approver roles; published versions are immutable; overlapping or unverified configuration cannot be treated as an approved official rule.
- FR-16 Missing, conflicting or incomplete configuration never produces a guessed route; it is recorded as a configuration gap for rule staff.
- FR-17 Attachments: authenticated, allow-listed, size-limited, content-checked, privately stored, access-checked on download, never marked verified.
- FR-18 Application and configuration changes, refusals and upload failures are audited.

## Rule-source verification gaps (as of 2026-10-26)

No rule in the catalogue is verified against the official AICTE publication. Nothing below may be published as an official rule until closed.

| # | Gap | Effect in the product |
|---|---|---|
| G1 | The official APH PDF (`aicte.gov.in/.../APH%20Final.pdf`) returned HTTP 502 on 2026-10-26. All APH content was read via the project's curated reference pack (`docs/sources/`), which states it is not the full APH and not an official publication, and which cross-checked a non-authoritative mirror | Every APH-derived source is `SECONDARY_UNVERIFIED`; official-classified rule versions cannot be published |
| G2 | **Document checklists** per application category: none are stated in the available sources | Checklist for official drafts is empty (blocks publication). The only checklists are `[DEMO]` items |
| G3 | Table 2.3 is reproduced only as "selected examples"; the full table, its footnotes and applicability to each academic year (2024-25 … 2026-27) are unverified | Five route shapes seeded as unpublishable drafts, for 2026-27 only |
| G4 | Reported corrigendum of 2 December 2025 (EVC constitution/modes, SAC constitution) is known only from a secondary report | Recorded as a source; **not encoded** anywhere |
| G5 | Appeal deadline trigger differs between Ch. I cl. 1.11 (7 days from issue of LoD) and Ch. II cl. 2.24 (7 days from upload of the Executive Committee decision); which governs each category is not established | One policy for cl. 2.24 stored on unpublishable drafts, marked "applicability NOT confirmed"; calendar vs working days unspecified. No global appeal clock |
| G6 | SLA durations, escalation ladders, pause/resume rules: not established | Deadline policies carry no duration unless sourced; demo policy has none |
| G7 | Application category list: only the five Table 2.3 row names are known; whether other AICTE application types exist in scope is unverified | Categories flagged "not independently verified" |
| G8 | Official letter outputs (LoA/LoD/LoR) and the Council ratification step | Out of Phase 2 scope; not modelled |

Closing a gap means: read the official text, create a `VERIFIED_OFFICIAL` source citing clause and page, author a new version citing it, and have a different person publish it.