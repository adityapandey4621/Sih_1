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
| G1 | **Partly closed 2026-10-27.** The official host stayed unreachable, but the team supplied the official 2024-27 handbook PDF (`docs/sources/2025031399.pdf`); Ch. I 1.8–1.11 and Ch. II 2.23–2.24 / Table 2.3 were read in it (`APH_VERIFICATION.md`). Corrigenda after December 2023 are unchecked | The two APH clause sources are `VERIFIED_OFFICIAL` (this edition only); the summary, mirror and corrigendum report stay `SECONDARY_UNVERIFIED`; official versions remain unpublishable (checklists not encoded, corrigenda unchecked) |
| G2 | **Document checklists** per application category: none are stated in the available sources | Checklist for official drafts is empty (blocks publication). The only checklists are `[DEMO]` items |
| G3 | Table 2.3 was read in full in the official PDF and the five seeded routes match it; applicability to each academic year (2024-25 … 2026-27) and later corrigenda are unverified | Five route shapes seeded as unpublishable drafts, for 2026-27 only |
| G4 | Reported corrigendum of 2 December 2025 (EVC constitution/modes, SAC constitution) is known only from a secondary report | Recorded as a source; **not encoded** anywhere |
| G5 | Appeal deadline trigger differs between Ch. I cl. 1.11 (7 days from issue of LoD) and Ch. II cl. 2.24 (7 days from upload of the Executive Committee decision); which governs each category is not established | One policy for cl. 2.24 stored on unpublishable drafts, marked "applicability NOT confirmed"; calendar vs working days unspecified. No global appeal clock |
| G6 | SLA durations, escalation ladders, pause/resume rules: not established | Deadline policies carry no duration unless sourced; demo policy has none |
| G7 | Application category list: only the five Table 2.3 row names are known; whether other AICTE application types exist in scope is unverified | Categories flagged "not independently verified" |
| G8 | Official letter outputs (LoA/LoD/LoR) and the Council ratification step | Out of Phase 2 scope; not modelled |

Closing a gap means: read the official text, create a `VERIFIED_OFFICIAL` source citing clause and page, author a new version citing it, and have a different person publish it.

## Functional requirements (Phase 3)

- FR-19 Uploaded files are versioned per checklist item; replacement never overwrites or deletes an earlier version; identical bytes do not create a version.
- FR-20 Each version is processed asynchronously and durably: technical validation, malware scan (where a scanner exists), PDF text, OCR for scanned pages, configuration-driven field extraction with page/confidence provenance.
- FR-21 The system distinguishes: no file; unreadable file; extraction failure; uncertain value; possible inconsistency. None of these is a regulatory deficiency, and none changes an application's status.
- FR-22 Institutions see the technical state of their own files and institution-appropriate wording of preliminary issues; reviewers (scrutiny officers, read-only) see extracted values, page text, previews, findings with priority, versions and processing history.
- FR-23 Processing failures are visible and diagnosable; retries are bounded; permanent input errors never loop; stale or crashed workers lose nothing and corrupt nothing.
- FR-24 Without a malware scanner files are labelled as not scanned; where scanning is required they are held.

Roles: **scrutiny_officer** gains `evidence:read` (read-only, submitted applications only) and `document:retry`; **institute_user** gains `document:retry`. No other role changed.

### Additional verification gaps (Phase 3)

| # | Gap | Effect |
|---|---|---|
| G9 | ClamAV was not available; the clamd client is tested only against a fake server implementing INSTREAM framing | Scanning is off by default and every file says "not scanned" |
| G10 | Tesseract was tested at one version (5.4.0, English) on synthetic documents; real institutional documents (stamps, tables, handwriting, other languages) have not been tried | Extraction quality on real documents is unknown; uncertainty is reported, not hidden |
| G11 | The demo extraction fields (name, date, reference) are invented; no verified list of values to extract for any category exists (see G2) | Only DEMO configuration carries extraction fields |
| G12 | Day-first reading of numeric dates is an assumption (Indian convention), stated in the value's note | Ambiguous dates such as 04/05/2024 are interpreted day-first |


## Functional requirements (Phase 4)

- A submitted application enters the scrutiny queue in the same transaction; a route without a scrutiny stage goes to a configuration-exception state, not a guessed route.
- Cases are visible only within scope: reviewer = assigned, issuer = notice submitted for issuance, coordinator = queue metadata only. Out-of-scope access is "not found" and audited. Auditors have no document access.
- Assignment follows a configurable boundary: a labelled demonstration assignment for DEMO cases only; official selection unavailable until an authorised interface exists.
- Reviewers record structured findings linked to an exact document version and page, with a source reference chosen from the frozen rule version or flagged for verification; internal and institution-facing wording are separate fields.
- Only an authorised issuer issues a formal notice, after inspecting the exact content, with a reason, atomically and idempotently. A draft or prepared notice is never visible to the institution.
- The response period and due date come only from a usable, sourced policy in the application's frozen configuration; otherwise no due date (demo) or no issuance (non-demo, with a recorded configuration gap).
- Institutions respond only to their own issued, open notice; corrected evidence is stored as new versions through the Phase 3 pipeline; the response is received, not accepted, and closes nothing.
- Re-scrutiny is a new round; findings are concluded individually with reasons; unresolved findings need explicit acknowledgement to complete; further notices are separate linked records with no invented cap.
- Completion never approves or rejects; the next stage comes from the application's own frozen route or a recorded determination.
- Every action is audited with actor, time, object, state change and reason; in-app notifications are persistent and idempotent and never claim external delivery.

### Additional verification gaps (Phase 4)

G-P4-1 … G-P4-6 and the limits listed at the end of `SCRUTINY.md`. In short: the handbook states no response period, issuing officer, template, clarification channel or round limit, so none is encoded; corrigenda after the 2024-27 edition are unchecked. See `APH_VERIFICATION.md`.

## Functional requirements (Phase 5)

- FR-25 The next stage after scrutiny, EVC or SHC is read from the application's frozen route. A conditional stage needs a recorded determination with a reason; a missing or inconsistent route goes to a controlled exception; EVC, SHC and any other stage are never activated together by default.
- FR-26 An EVC / SHC case is created at most once per application and stage, records why it exists (route, rule version, determination, prior outcome) and freezes the committee configuration it follows.
- FR-27 Committee members come only from an authorised selection mechanism behind a provider boundary. Without a connected provider, selection is unavailable and says so; a demonstration mode is labelled `DEMO_ONLY` everywhere. A failed or unverifiable result never creates an assignment.
- FR-28 Visit and hearing schedules are proposed then confirmed, keep their history when rescheduled or cancelled, and accept only modes the configuration permits. The system never claims anyone is available.
- FR-29 Only an assigned member sees and works a case; a member role alone shows nothing. Coordinators arrange; neither can approve, reject or decide.
- FR-30 Observations record what was seen, never "compliant". Reports and recommendations are versioned; a submitted version is immutable, retries create no duplicate, and a correction is a new version opened by an authorised coordinator.
- FR-31 SHC compliance is requested only where the configuration permits it; the institution responds with evidence as new document versions; a response is received, never accepted; findings and recommendations stay separate from each other and from any decision.
- FR-32 When every stage the route required is complete, a decision hand-off is recorded. It decides nothing.

### Additional verification gaps (Phase 5)

See `COMMITTEE.md`, G-P5-1 … G-P5-9: the December 2025 corrigendum is unverified; the EVC checklist (Annexure-3 / 4) and committee composition are not encoded; the portal selection has no interface; no SHC response period, hearing format or scheduling deadline is established; several release and authority rules are conventions.

## Functional requirements (Phase 6)

- A permission-protected decision queue; a case exists only once every mandatory stage of the frozen route is complete; an incomplete or ambiguous route is blocked and explained.
- Each case shows the rule version, scrutiny findings, EVC report, SHC recommendation, Regulation Bureau observations, outstanding stages and the audit history.
- Only authorised decision-makers record a decision; decision-maker, timestamp, reasons, supporting references and rule version are stored; the original and its corrections are preserved; the Council's step is recorded separately.
- No automatic approval or rejection; no report decides.
- The institution sees the official status, reason and reference, in-app notifications, and an honest AICTE-sync state (`NOT_CONFIGURED`, `SYNC_PENDING`, `SYNCED`).
- An appeal is possible only when the frozen rule version and the decision configuration allow it; the deadline is computed from the rule version's own policy; duplicates are prevented; the SAC works the appeal and recommends; the Council records the decision, in that order; the NO branch bypasses the SAC.
- Backend enforcement for every one of these; administrators hold no decision powers.

### Additional verification gaps (Phase 6)

See `DECISIONS_AND_APPEALS.md`, G-P6-1 … G-P6-11: the December 2025 corrigendum is unverified; the counting convention and cut-off time for the 7-day window are not stated; appealable outcomes, the ratification vocabulary and the Council's appeal outcomes are demo configuration; no OFFICIAL decision policy exists; no letter text is generated; no portal integration; a "further scrutiny / EVC" recommendation does not start a second cycle; appeal files are not malware-scanned.
