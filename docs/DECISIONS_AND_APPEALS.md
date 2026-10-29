# Executive Committee decisions, Council steps and SAC appeals (Phase 6)

> Authorised bodies make and record regulatory decisions; the software manages the case record, permissions, process, appeals and traceability. **Nothing in this phase decides, suggests or defaults an outcome.** No report, recommendation, observation or completed stage is a decision, and an appeal is never promised to succeed.

Code: `app/models_decisions.py`, `app/services/{decisions,appeals,sac,appeal_rules,portal_sync,decision_texts,private_files}.py`, `app/decision_presenters.py`, `app/routers/decisions.py` (+ the `/api/sac` router in `routers/committee.py`), `app/schemas_decisions.py`, `app/reconcile_decisions.py`, `app/domain.py` (state machines), `app/guards.py`, `app/rbac.py`. Migration `0006`. Frontend: `frontend/src/features/decisions/`, SAC screens in `features/committee/`.

## Three separate authorities, one distinct record each

| Body | Role (`rbac.py`) | Records | Never |
|---|---|---|---|
| Regulation Bureau | `regulation_bureau` | Observations; issues a recorded outcome (reference and/or file); closes the stage | Decides |
| Executive Committee | `executive_committee` | The decision (LoA / LoD / LoR as configured) and reasoned corrections | Issues, ratifies |
| Council | `council_member` | The ratification step on a decision; the decision on an appeal | Decides before the SAC stage completes |
| Standing Appellate Committee | `sac_coordinator`, `sac_member` | Hearing/sitting records; a versioned recommendation to the Council | Decides the appeal |

The two new roles `regulation_bureau` and `council_member` (and the SAC roles) are **application capabilities, not claimed AICTE designations**. `platform_admin`, `rules_admin` and `super` roles hold none of the decision permissions: a role never implies another's powers.

Source reading (APH 2024-27, see `APH_VERIFICATION.md`): the Council ratifies Executive Committee decisions (Ch. I 1.10(a); Ch. II 2.25(a)); the Executive Committee decides after considering committee recommendations and Regulation Bureau views (1.10(a)); LoA / LoD / LoR, with a speaking order for a rejection (1.10(b)); one appeal to the Council within 7 days (Ch. I 1.11(a) from the issue of the LoD; Ch. II 2.24(a) from the upload of the Executive Committee decision); the SAC recommends or does not recommend, or recommends a further scrutiny / EVC (1.11(d)); the Council's decision on an appeal is final (1.11(e), 2.24(f)).

## The decision case

`DecisionCase` (one per application, created from the Phase 5 `DecisionHandoff`, which exists **only** when every mandatory stage of the application's frozen route is complete). Opening is idempotent; `stages.reconcile` opens any orphaned hand-off.

```
AWAITING_OBSERVATIONS -> READY_FOR_DECISION -> DECIDED -> ISSUED -> CLOSED
                                                   ^         |
                                                   +---------+  (correction before/after issue; the first issue date never moves)
```

State is changed only by a compare-and-set transition (`DECISION_TRANSITIONS`) in the service; ORM assignment of state is refused by `guards.py`. A case with no applicable decision policy, an ambiguous route or a missing mandatory stage is **blocked, with the reason shown**; nothing is assumed to keep it moving.

* **Observations** (`BureauObservation`): append-only, ordered. Whether at least one is required is configuration (`bureau_observation_required`).
* **Decision** (`EcDecision`): versioned. `ORIGINAL`, then `CORRECTION` rows that carry a reason and supersede the earlier one. Stores decider, timestamp, reasons, speaking order (required for outcomes the policy marks, e.g. LoR), meeting reference, the **supporting records considered** (scrutiny, EVC report, SHC recommendation, Bureau observations, document versions, each with its version / hash), the rule version, the policy reference and a content hash. Rows are immutable and cannot be deleted. The decision-maker must confirm the records considered.
* **Issue** (`OfficialIssuance`): a reference and/or a PDF stored privately. **No letter text, signature or seal is generated.** One live issuance per case (partial unique index); a correction supersedes it and the earlier issue stays on record. Issue may require ratification only if the policy says so (`issue_requires_ratification`).
* **Council ratification** (`CouncilDecision`, kind `RATIFICATION`): kept distinct from the decision; vocabulary and whether it is required are configuration. A correction retires the earlier live row (`is_live`) and adds a new one.
* **Close**: needs the ratification (where required) and either the appeal period run out / no eligibility, or the appeal decided. Closing writes a `PostDecisionHandoff` for later phases; it is not post-approval processing.

## Institution view

`GET /api/applications/{id}/decision` shows only: the official status and reason, the issued outcome with reasons / speaking order / reference / file, any correction and why, the Council step, the appeal eligibility (deadline, provision, window note), its own appeal and documents, the Council's decision on the appeal, and the sync status. It never shows Bureau observations, supporting-record detail, SAC deliberations or committee notes. In-app notifications (the Phase 4 engine, deduped by key) tell the institution about the issued outcome, a correction, the Council step, the appeal being received and its decision. **No email or SMS is sent or claimed.**

## Appeals

Eligibility is computed, not assumed (`services/appeals.eligibility`, `services/appeal_rules.resolve_window`):

1. An outcome must have been **issued**; the appeal is against the Executive Committee decision (the appeal row references the exact `EcDecision`), not an EVC / SHC record.
2. The decision policy must permit appeals and list the outcome as appealable (`appeal.appealable_outcomes`; **demo configuration only**).
3. The frozen rule version must contain exactly one deadline policy whose trigger applies: `letter_of_deficiency_issued` (Ch. I 1.11(a)) for an LoD, `executive_committee_decision_uploaded` (Ch. II 2.24(a)) otherwise. Zero, several, or one without a duration → blocked (`MISSING`, `AMBIGUOUS`, `NO_DURATION`). Working-day counting is refused (`UNSUPPORTED`: no verified calendar). A source that is not `VERIFIED_OFFICIAL` is refused (`UNVERIFIED`) except `DEMO_ONLY` on a DEMO case with demo rules allowed (`DEMO_COMPUTED`).
4. The window is anchored at the **first issue** (`first_issued_at`) and never moves, even after a correction.
5. Only the owning institution with `appeal:submit` may appeal; once per application (service check **and** unique constraint **and** idempotency key). A refusal is audited (`appeal.refused`) with the state.

Submitting creates the `Appeal`, opens a `CommitteeCase` for stage `SAC` (selection through the same provider boundary as Phase 5, demonstration mode labelled, the real portal not connected), and notifies the SAC coordinator. The institution may add PDFs / links while the appeal is under SAC review (closed when the stage completes; link schemes limited to http(s)).

SAC flow: coordinator requests selection → members assigned (only assigned members see the case; the coordinator sees all) → optional hearing (policy `sac.hearing`) → append-only proceedings → versioned recommendation (`RECOMMEND` / `NOT_RECOMMEND` / `FURTHER_SCRUTINY_OR_EVC`, demo vocabulary) → coordinator completes the stage, which moves the appeal to `AWAITING_COUNCIL_DECISION`. **Only then** can the Council record its decision (configured outcomes, reasons, speaking order where marked), which sets `COUNCIL_DECIDED`. A Council correction adds a new live row and keeps the old. The SAC's recommendation changes nothing official.

The **NO branch**: if the period runs out without an appeal, the case closes without any SAC case (`reconcile_decisions` finalises expired cases; closing is also available to the Bureau).

## Synchronisation with AICTE's portal

`ExternalSync` (one row per subject and kind) with `NOT_CONFIGURED`, `SYNC_PENDING`, `SYNCED`. With no authorised provider nothing is sent and the screens say "AICTE's portal has NOT been updated by this system". `SYNCED` is set **only** when a provider confirms receipt. A failed or unconfirmed push is `SYNC_PENDING` with the error and attempt count, notifies the Bureau, and is retried by `python -m app.reconcile_decisions` (safe: one row per subject, stable payload hash for the receiver to de-duplicate on). A sync problem never undoes a decision. The provider interface (`PortalSyncProvider`) is the only thing a production integration must implement; no production integration exists.

## Security

Every endpoint is checked on the server for permission and for this exact case: queue/detail (`decision:queue`, else 404 + audit), observations (`decision:observe`), decision and correction (`decision:record`), issue and close (`decision:issue`), ratification and appeal decision (`council:record`), appeal (`appeal:submit` + institution scope), SAC (`sac:*` + assignment). Out-of-scope access returns 404 and is audited. Historical decisions, observations, appeal documents, issued records and Council records are immutable at the ORM guard layer; state and `row_version` cannot be assigned from outside the service layer. Files are stored privately, size/type-checked, and not malware-scanned in this build (the scanner boundary from Phase 3 is not applied to appeal / issuance files: gap below).

## Configuration

Decision, ratification and appeal rules live in a `DEC` committee policy (`committee_policies`, stage `DEC`) and SAC process rules in an `SAC` policy; both are **DEMO_ONLY** (`seed_demo_policies`), frozen on the case as `policy_snapshot`. There is no OFFICIAL policy, so a non-demo application is **blocked** at the decision stage with an explicit configuration gap. The 7-day appeal windows are in the rule version as deadline policies (`demo-appeal-window` in `seed_rules.py`; real versions are unpublishable until their sources are verified).

## Start-up

```
cd backend
.\.venv\Scripts\python -m alembic upgrade head          # includes 0006
.\.venv\Scripts\python -m app.seed ; .\.venv\Scripts\python -m app.seed_rules
.\.venv\Scripts\python -m app.reconcile_decisions        # route hand-offs, pending syncs, expired appeal windows
cd ..\frontend ; npm run dev
```

## Open gaps (not guessed)

| ID | Gap |
|---|---|
| G-P6-1 | The reported **December 2025 corrigendum** could not be retrieved (official pages 404 / reset on 2026-10-27); unverified. |
| G-P6-2 | The handbook does not state how the 7 days are **counted** (calendar vs working) or the **cut-off time**; calendar days are encoded and flagged in the UI. |
| G-P6-3 | Which outcomes are **appealable**, the **ratification vocabulary**, whether issue waits for ratification, and the **Council's appeal outcomes** are demo-only configuration, not verified rules. |
| G-P6-4 | No OFFICIAL decision / SAC policy exists, so non-demo applications are blocked at the decision stage. |
| G-P6-5 | No letter wording, signature or seal is generated: reference / file only. |
| G-P6-6 | No authorised AICTE portal integration; sync is `NOT_CONFIGURED` by default. |
| G-P6-7 | A SAC recommendation of "further scrutiny / EVC" is recorded but does not trigger a second cycle. |
| G-P6-8 | ~~Appeal and issuance files are not malware-scanned.~~ **Closed in Phase 8:** every private upload goes through the configured scanner (infected = refused and deleted); with no scanner the file is recorded `NOT_SCANNED` and, in production, refused. Still unverified: a real ClamAV was not run on the development machine. |
| G-P6-9 | A post-issue correction does not move the appeal window (anchored at the first issue); whether it should is a rule question. |
| G-P6-10 | The roles `regulation_bureau`, `council_member`, `sac_coordinator` and `sac_member` are application capabilities, not AICTE designations; real appointment of people to them is out of scope. |
| G-P6-11 | Downgrading migration `0006` deletes SAC cases / reports and `DEC` / `SAC` policies (they cannot exist under the Phase 5 CHECKs). |

## Not in this phase

Post-approval compliance, the SLA / reminder scheduler, production AICTE integration, microservices / queues. *(Updated: post-approval compliance, the deadline scheduler and alerts were built in Phase 7 — `COMPLIANCE_AND_DEADLINES.md`; the appeal window is now also a persistent clock. The integration boundary and the new sync states `FAILED` / `RECONCILIATION_REQUIRED` came in Phase 8 — `INTEGRATION.md`. A production AICTE integration is still not built and is unverified.)*
