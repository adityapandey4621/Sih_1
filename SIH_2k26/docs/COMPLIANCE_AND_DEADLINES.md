# Phase 7 — Compliance, document validity, deadlines and alerts

This phase adds what happens **after** a decision is issued (obligations and document validity) and a **persistent deadline engine** that serves every stage
(response periods, appeal windows, officer-stage service levels, EVC scheduling, compliance due dates, document expiry).

Core principles, each enforced in code and tested:

1. **Uploading is not verifying.** An institution submits evidence; only the *assigned verifier's recorded review* makes an obligation `VERIFIED`.
2. **A deadline never changes anything but an alert.** A passing date raises reminders and escalations. Nothing revokes an approval, suspends, penalises or
   changes a decision. `NOT_MET` is a record for the competent authority.
3. **No invented durations.** A due date exists only if (a) the application's *frozen* rule configuration has a usable policy for it, or (b) an authorised person
   states the date and cites where the decision says so. Otherwise the clock is `NOT_ESTABLISHED` and says why.
4. **A clock never moves silently.** Its trigger, original due date and policy snapshot are immutable (ORM guard + tests). Publishing a new rule version or changing
   an internal service level does not touch an existing clock. The only thing that can move the *effective* date is an approved pause, where the clock's own policy allows it.
5. **Durable and restartable.** Everything pending lives in PostgreSQL. A restarted scheduler re-derives clocks from the recorded cases, plans missing alerts, recovers
   abandoned work (lease expiry) and sends what became due — except what the persisted state no longer calls for.
6. **Truthful notifications.** Alerts are *in-app* only. No email/SMS channel exists, and no message says or implies one was sent.

## 1. Compliance obligations (`/api/post-approval/obligations…`)

| Step | Who | Permission | Effect |
|---|---|---|---|
| Record an obligation against an *issued* decision | Regulation Bureau | `compliance:define` | `Obligation` with the condition as stated in the decision, where it appears, the evidence required, the assigned verifier, and the due date basis |
| Submit evidence (1–5 files, labelled) | the institution | `compliance:submit` | `ObligationSubmission` + `ObligationDocument` versions. Status → `EVIDENCE_SUBMITTED`. **Not verified.** |
| Verify / request a correction / record "not met" | the *assigned* compliance officer | `compliance:verify` | `ObligationReview` (immutable), status → `VERIFIED` / `CORRECTION_REQUESTED` / `NOT_MET`; the clock finishes on `VERIFIED` and `NOT_MET` |
| Withdraw (recorded in error) / reassign verifier | Regulation Bureau | `compliance:define` | kept in history |

* **Source decision, condition, rule version** are stored on the obligation and cannot be edited (ORM guard). The decision version it follows from is recorded.
* **Due-date basis** is one of `POLICY` (computed from a usable policy with trigger `compliance_obligation_defined`; if the policy cannot establish a date the request is *refused*, not defaulted),
  `SPECIFIED` (a future date plus the citation of where the decision states it — a human statement, never labelled statutory), or `NONE`.
* **Resubmission after a requested correction** creates a new submission and new document versions per label; earlier ones stay.
* **Internal notes** of the verifier are never returned to the institution (tested).
* **Scope:** the institution sees its own institution's obligations; the Bureau sees all; a compliance officer sees only those assigned to them; anyone else gets *not found* and an audit record.
* **Files** go to private storage with generated keys, size/type/content sniffing, and the malware scanner when configured. With no scanner a file is recorded `NOT_SCANNED` — never "clean" — and in production (or with `REQUIRE_MALWARE_SCAN`) it is refused.

Statuses: `AWAITING_EVIDENCE → EVIDENCE_SUBMITTED → VERIFIED | CORRECTION_REQUESTED (→ EVIDENCE_SUBMITTED) | NOT_MET`, and `WITHDRAWN` from any open state.
Transitions are compare-and-set updates with a `row_version`; stale writes get `409`.

## 2. Document validity and renewals (`/api/post-approval/validity…`)

What is tracked is **configuration**: an extraction field of kind `date` flagged `"validity": true` in a checklist item of the frozen rule version.

* **Extracted** date: read by the processing layer from a submitted document. Stored with the document, its version number, the page, method (PDF text / OCR), confidence and the source line.
  It is a *candidate* (`UNVERIFIED`). An uncertain read, a missing date, an implausible year, or a date that **conflicts** with another recorded date for the same document is `NEEDS_REVIEW`.
* **Declared** date: the institution's claim, uploaded with a renewal file (a new version). Also unverified.
* **Verified** date: a compliance officer confirms it against the document and records what they checked. The extracted/declared value and the verified value are kept side by side; the verifier may correct the date.
* **Only a verified date creates an expiry clock** (and so alerts). The expiry moment is the *end of that date, UTC*; the official convention for "valid until" is not verified (gap G-P7-4).
* **Renewals do not silence old alerts.** Uploading a renewal creates a new unverified version and leaves the previous verified date's clock and pending alerts untouched. They end only when
  the renewal is verified (the earlier version becomes `SUPERSEDED`, its clock `COMPLETED: renewal_verified`), or an authorised person records that the item no longer applies (with a reason; the history stays).
  A rejected renewal changes nothing and the institution may upload another. A second renewal is refused while one awaits review.

## 3. The deadline engine

### Clocks (`clocks`, `clock_segments`, `clock_events`)

One row per *(subject, policy)*. Kinds and what they time:

| Kind | Subject | Trigger → finished when | Policy source |
|---|---|---|---|
| `INSTITUTION_RESPONSE` | issued deficiency notice | issue → response received / notice closed | the due date **stored on the notice** when it was issued (never recomputed) |
| `APPEAL` | decision case | first issue of the official outcome → appeal submitted / case closed | `appeal_rules.resolve_window` (as in Phase 6) |
| `OFFICER_STAGE` | scrutiny case | `scrutiny_case_opened` → first scrutiny round over | deadline policy of the frozen rule version |
| `OFFICER_STAGE` | decision case | `decision_case_opened` → a decision is recorded | same |
| `EVC` | EVC case | `evc_stage_activated` → a visit is scheduled; `evc_visit_confirmed` → report submitted | same |
| `COMPLIANCE` | obligation | defined → `VERIFIED` / `NOT_MET` / `WITHDRAWN` | policy or cited date; *submitted-but-unverified does not finish it* |
| `DOCUMENT_VALIDITY` | verified validity version | verification → expiry; finished when superseded/resolved | the verified date; alert schedule from policy trigger `document_expiry` |

`deadline_type` is `STATUTORY`, `INTERNAL_SLA`, `SPECIFIED` or `VALIDITY` and comes from the policy (or the citation). **Statutory and internal clocks on the same trigger are separate rows**; changing one cannot change the other (tested).
Trigger names (`scrutiny_case_opened`, …) are this project's conventions, documented here; they are not AICTE terms.

`status`: `RUNNING`, `PAUSED`, `COMPLETED`, `CANCELLED`, `NOT_ESTABLISHED`. "Overdue / due soon / on track" are computed when asked, never stored. A `NOT_ESTABLISHED` clock never carries a date (a database CHECK enforces it).

### Policy semantics (what a deadline policy may say)

A due date requires: a stated duration in `days` or `hours`; counting convention `CALENDAR` (working days are unsupported — there is no verified holiday calendar); and a source that is `VERIFIED_OFFICIAL`,
or `DEMO_ONLY` on a DEMO-classified case with demo rules enabled. Anything else yields `NOT_ESTABLISHED` with the reason. The alert schedule, pause rule and escalation roles live in the policy's `escalation` field
(validated when a rule version is authored):

```json
{ "alerts": { "pre_due_hours": [48, 24], "at_due": true, "overdue_hours": [24, 72] },
  "pause":  { "allowed": true, "reasons": ["awaiting_clarification"] },
  "escalate_to": ["regulation_bureau"] }
```

Absent means *none*: no alerts, no pausing, nobody escalated to. `escalate_to` may only name `regulation_bureau`, `scrutiny_coordinator`, `evc_coordinator`, `shc_coordinator`, `sac_coordinator`, `compliance_officer`.

### Pause / resume

Only the Regulation Bureau (`clock:manage`), only if the clock's own policy has `pause.allowed`, only for a listed reason if the policy lists reasons. The pause is a `ClockSegment`; the original due date is
untouched; on resume the paused time is added (`paused_seconds`) and alerts are re-planned against the new effective date. A policy that does not allow pausing cannot be overridden — `409 pause_not_permitted`.

### Scheduler (`python -m app.scheduler`, `app/services/scheduler.py`)

Pending alerts are rows in `scheduled_work` (PostgreSQL). One cycle (`run_cycle`): read validity candidates → derive missing clocks from recorded cases (`clock_sources.sync`) → complete clocks whose subject finished (`refresh`) → plan
alerts (idempotent key = clock + alert + due date) → return work abandoned by a dead process to the queue (lease expiry) → claim and process due work with a compare-and-set lease (`FOR UPDATE SKIP LOCKED` on PostgreSQL).

Before anything is sent, **the persisted state is read again**. A finished subject, a moved due date, a PRE_DUE alert after the date, or an alert superseded by a nearer one that is also due, is skipped (`SKIPPED_STALE`, with the reason).
After an outage you therefore get the nearest missed alert, not a flood. Notifications are keyed per (clock, alert, due date, recipient): running the same row twice creates nothing new.
Failures are retried with exponential back-off up to `WORK_MAX_ATTEMPTS`, then `FAILED_EXHAUSTED`, which stays visible (`GET /api/clocks/health`) until an operator runs `python -m app.scheduler --requeue-failed`.
A notification that could not be stored is a failure of the work item (it is *not* recorded as sent).

Recipients are decided at send time from current state: e.g. an obligation whose evidence awaits verification alerts the **verifier**, not the institution.
Each notification records the clock, the obligation (if any), and the policy reference; each work row records the recipients, attempts, outcome and `delivery = STORED_IN_APP`.

Redis is not used. PostgreSQL is the only store of pending work.

## 4. API summary

`GET /api/post-approval/{overview, obligations, obligations/{id}, validity, cases, verifiers}` · `POST …/applications/{id}/obligations` · `POST …/obligations/{id}/{evidence, review, withdraw, reassign}` ·
`POST …/validity/items/{id}/{renewals, resolve}` · `POST …/validity/versions/{id}/{verify, reject}` · `GET /api/clocks`, `/api/clocks/{id}`, `/api/clocks/health` · `POST /api/clocks/{id}/{pause, resume}`.
(The prefix is `/api/post-approval` because `/api/compliance/*` is already the Phase 5 SHC compliance-response API.) See `docs/API.md`.

## 5. Roles and permissions added

`compliance_officer` (new role; an application capability, not an AICTE designation). Permissions: `compliance:queue`, `compliance:define` (Bureau), `compliance:verify` (officer), `compliance:read_own`, `compliance:submit` (institution),
`clock:read`, `clock:manage` (Bureau), `dashboard:compliance`. Clock visibility by role: Bureau — all kinds; scrutiny coordinator — officer-stage and response; EVC coordinator — EVC; SAC coordinator — appeal;
compliance officer — compliance and validity; institutions — only their own response, appeal, compliance and validity clocks (never internal service levels).

## 6. Start-up

```
cd backend
.\.venv\Scripts\python -m alembic upgrade head     # migrations 0007 (this phase) and 0008 (Phase 8)
.\.venv\Scripts\python -m app.seed                 # demo users, including compliance.officer@demo.example.org
.\.venv\Scripts\python -m app.seed_rules           # DEMO ONLY rule sets, now with DEMO deadline policies and a validity field
.\.venv\Scripts\python -m app.worker               # document processing (as before)
.\.venv\Scripts\python -m app.scheduler            # deadlines and alerts   (--once for one pass; --requeue-failed)
```

## 7. Unresolved regulatory-source gaps (not guessed)

* **G-P7-1** — No verified source defines post-approval obligations, which conditions an Approval may carry, or who may verify them. Obligations are recorded by an authorised person from the decision; the roles are application capabilities.
* **G-P7-2** — No verified source gives internal service-level durations for officer stages, EVC scheduling or decision-making. The DEMO values (5, 7, 10, 30 days; alert offsets) are invented, labelled `DEMO ONLY`, and exist only in the DEMO rule sets.
* **G-P7-3** — Counting conventions (calendar vs working days, whether the first day counts, cut-off time) are unverified for every period, including the 7-day appeal window. `WORKING` is refused; an unstated convention is refused for response/compliance clocks and, for appeals, handled and flagged as in Phase 6.
* **G-P7-4** — Which documents must stay valid (a lease, an affiliation, a fire certificate…), and how "valid until" is counted, is unverified. Tracking is configuration; the expiry moment is the end of the stated date in UTC.
* **G-P7-5** — Whether reminders/escalations are required, their offsets, and who must be escalated to are unverified; only configured (DEMO) schedules exist. Escalation roles are application capabilities.
* **G-P7-6** — Whether any statutory deadline may be paused is unverified; the engine allows a pause only where a policy states it, and no seeded statutory policy does.
* **G-P7-7** — Consequences of an unmet obligation or an expired document are not defined by any source available here. None is implemented, by design.
* **G-P7-8** — Email/SMS delivery does not exist. The notification store is the only channel. Time is stored in UTC and shown in the browser's locale.
* **G-P7-9** — A correction to an issued decision does not rebind existing obligations; they stay linked to the decision version they were recorded against.
