# AICTE SIH 2026 — Workflow, Rules and Product Boundaries

**Problem Statement ID:** 26262  
**Project title:** Integrated AICTE Approval Workflow Automation, Compliance Monitoring and Alert Management System  
**Purpose:** A project-specific reference for Claude Code. This file explains the proposed product workflow and maps it to selected APH provisions. It is not the full APH and is not a substitute for the applicable official handbook, corrigenda or portal instructions.

## How Claude must use this pack

1. Retry the official AICTE APH URL first: <https://www.aicte.gov.in/sites/default/files/APH%20Final.pdf>.
2. If the official URL is still unreachable, use `AICTE_SIH26262_APH_Selected_Provisions_Reference.pdf` and this file as a **curated, source-referenced summary**, not as an authority to invent missing legal rules.
3. Treat `reference/AICTE_Approval_Workflow_reference.png` as a visual workflow reference only. It is not a legal source and its connectors/labels must be interpreted using this written flow. It is a working reference image, not an immutable final design specification.
4. Treat anything labelled `DEMO ONLY`, `UNVERIFIED`, or `PROPOSED PRODUCT POLICY` as non-regulatory. Do not publish it as an official rule.
5. If a required official clause or current amendment cannot be verified, create a clearly tracked gap and do not hardcode the guess.

## Source status and limitations

- The APH inspected for this brief is titled **Approval Process Handbook 2024-25 to 2026-27**. Official AICTE URL: <https://www.aicte.gov.in/sites/default/files/APH%20Final.pdf>.
- The official AICTE host did not load reliably during preparation, so relevant text was cross-checked in a mirror copy of the same titled handbook: <https://www.iiqaccreditation.com/public/assets/aicte-process-handbook-2025-26.pdf?u=>. This mirror is **not** the issuing authority. Verify exact wording and current applicability against the official AICTE source before implementation.
- A secondary legal update reports a corrigendum dated 2 December 2025 changing the EVC definition (constitution by Council; Physical/online/Hybrid) and changing SAC constitution to the Council. The official primary corrigendum PDF was not verified during preparation. Treat this as a **critical verification gap**; do not implement these changes as confirmed until the primary source is found. Secondary report: <https://teamleaseregtech.com/updates/article/52940/aicte-issued-the-corrigendum-regarding-the-approval-process-2026-27/>.
- The handbook contains two appeal clauses with differently worded deadline triggers. Chapter I clause 1.11 says 7 days from issue of LoD; Chapter II clause 2.24 says 7 days from upload of the Executive Committee decision. Do not use one universal appeal trigger. The application category and applicable provision must determine the policy.

## A. Selected APH provisions relevant to the product

### 1. Chapter I, clause 1.8 — Scrutiny / re-scrutiny (printed APH pages 7–8)

Source-derived summary:
- Scrutiny/re-scrutiny committee members are selected through the web-portal automated selection process described in the handbook.
- The Regulation Bureau informs the institution and concerned experts of the scheduled date/time.
- The applicant presents supporting/original documents under the applicable procedure.
- Any deficiencies identified by scrutiny are communicated through the AICTE web portal, and the institution is expected to address them for re-scrutiny.
- Re-scrutiny verifies only the deficiencies pointed out by the Scrutiny Committee under the norms and standards.
- An application found in order after scrutiny/re-scrutiny is processed further for EVC where the applicable route requires it.
- The handbook notes that scrutiny/re-scrutiny and EVC are generally online; extraordinary circumstances may permit offline processing, and proceedings must be recorded for transparency/accountability.

**Implementation implications:**
- Re-scrutiny must revisit the identified deficiencies; it is not an automatic pass.
- Preserve the prior findings, exact issue references, institution response and new document versions.
- Do not equate OCR uncertainty with a formal deficiency.
- Do not hardcode committee appointment mechanics without confirming current official text.

### 2. Chapter I, clause 1.9 — Expert Visit Committee (EVC) (printed APH pages 8–9)

Source-derived summary:
- The handbook describes an AICTE portal-based automated selection process for EVC member selection.
- EVC verifies institutional infrastructure and readiness, relevant facilities and documents, and faculty-related requirements as applicable.
- EVC processing/reporting should be recorded; the scrutiny/re-scrutiny and EVC reports are made available to institutions through the AICTE portal under the handbook process.

**Implementation implications:**
- The product can support the authorised selection mechanism, scheduling, notifications, visit records and report submission.
- Do not assume an ordinary coordinator may freely assign any expert solely because the person appears available.
- EVC report is evidence/recommendation for the next authorised stage, not the final approval decision.
- The reported 2025 corrigendum may change constitution/mode wording; verify primary source before encoding the selection process.

### 3. Chapter I, clause 1.10 — Grant of approval / decision outputs (printed APH pages 8–9)

Source-derived summary:
- The Executive Committee considers committee recommendations and Regulation Bureau views and decides whether to grant approval.
- The APH states Executive Committee decisions are ratified by the Council.
- Outputs include Letter of Approval (LoA), Letter of Deficiency (LoD) or Letter of Rejection (LoR), as applicable; reasons/speaking order apply as described in the handbook.

**Implementation implications:**
- Only authorised decision-makers record official decisions.
- Workflow completion, clean OCR, completed checklist or an EVC report must never automatically approve/reject an application.
- Store the decision actor, source record, timestamp, applicable rule version and official document/reference.

### 4. Chapter I, clause 1.11 — Appeal procedure (printed APH pages 9–10)

Source-derived summary:
- The clause describes a one-time portal appeal to the Council for an institution/applicant aggrieved by an Executive Committee decision.
- It states a 7-day period measured from the date of issue of LoD in this clause.
- The Standing Appellate Committee (SAC) considers the appeal; relevant scrutiny/re-scrutiny and EVC reports and Regulation Bureau observations may be placed before it.
- SAC recommendations are placed before the Council, whose decision is final and uploaded on the portal, as described in the APH.

**Important caution:** The trigger wording in this clause is not identical to Chapter II clause 2.24. Keep deadline basis as versioned configuration tied to the exact applicable provision; do not silently generalise.

### 5. Chapter II, clauses 2.23–2.24 — Processing, SHC and appeal (printed APH pages 29–30)

Source-derived summary:
- Clause 2.23.1 points to Chapter I clause 1.8 for scrutiny/re-scrutiny, as applicable, and refers to Table 2.3 for category-specific processing.
- Clause 2.23.2 describes EVC subsequent to scrutiny/re-scrutiny where Table 2.3 applies.
- Clause 2.23.3 says institutions have an opportunity to submit compliance for shortcomings based on scrutiny/re-scrutiny/EVC before the Standing Hearing Committee (SHC). SHC recommendations are placed before the Executive Committee; the decision is uploaded with a speaking order as described in the handbook.
- Clause 2.24 describes a one-time appeal to the Council within 7 days of uploading the Executive Committee decision. The SAC considers the appeal; SHC report and Regulation Bureau observations, if any, are placed before SAC; SAC recommendations go to the Council, whose decision is final as described there.

**Implementation implications:**
- SHC is a compliance/hearing process; SAC is an appeal process. They are distinct.
- SHC does not automatically apply to every application.
- Where the applicable Chapter II procedure invokes SHC, the compliance evidence/recommendations should feed the Executive Committee decision process.
- Appeals are against the applicable Executive Committee decision under the governing process, not automatically against an EVC report in isolation.

### 6. Chapter II, Table 2.3 — Category-specific routes (printed APH page 30)

Table 2.3 lists processing steps by application nature. It must not be turned into a universal fixed workflow.

Selected examples from the table:

| Application type in Table 2.3 | Self-disclosure | Scrutiny / re-scrutiny | EVC | SAC/SHC column |
|---|---:|---:|---:|---|
| Extension of Approval based on self-disclosure | Yes | — | — | — |
| Change in institution name / affiliating University or Board / relevant institution type change | — | Yes | — | — |
| Increase in intake / additional courses | — | Yes | Yes | AA |
| Start new programme/level in an existing institution | — | Yes | Yes | AA |
| Change in course name / merger of courses / reduction in intake | — | Yes | — | — |

The APH defines **AA** as “As Applicable Depending on the Outcome of the Previous Processing Steps.” The table is for the listed Chapter II application types; it is not an exhaustive rule catalogue for every AICTE workflow.

**Implementation implications:**
- Rules determine the application route by category and applicable academic year.
- Conditional stages must be stored as data/rules.
- Do not infer that every case requires both EVC and SHC.
- If an approved route cannot be found or is ambiguous, flag a configuration gap instead of guessing.

## B. Our proposed product workflow (design requirement, not a legal quotation)

### Pre-processing configuration strip
Read applicable APH and current official corrigenda → create versioned rule catalogue → validate/approve rules → publish approved configuration. If source/rule/route is missing or ambiguous, route it to authorised configuration review. This is configuration, not a stage each application passes through.

### Stage 1 — Application intake and initial validation
Institute submits form and files → authenticate source/user and institution scope → validate required request fields and file type/size/basic safety → detect duplicates → register unique application ID → preserve original files and timestamp. Intake errors go back to permitted correction/resubmission. Do not show success before the record is actually persisted.

### Stage 2 — Document intelligence and evidence validation
Load approved rule version/checklist → extract digital PDF text and OCR scanned pages → record values with document/page provenance and extraction confidence → check completeness and cross-document inconsistencies. If no unresolved issue, go directly to scrutiny officer review. If issues exist, flag and route by submission status: draft corrections may be requested where permitted; after formal submission, potential deficiencies go to the authorised scrutiny officer. OCR alone never issues a formal deficiency notice.

### Stage 3 — Authorised scrutiny and re-scrutiny
Scrutiny officer reviews original files, extracted values, source pages, checklist and applicable rules → records findings/reasons → decides permitted action. If further information is required, the authorised deficiency process notifies the institution and tracks the response; corrected files are saved as new versions, revalidated, then returned to scrutiny/re-scrutiny. If complete, record the stage outcome and proceed to the next required route stage. The software never grants approval on its own.

### Stage 4 — Conditional EVC / SHC process
The approved route and case outcome determine applicable stages and their order. If EVC is required: follow current authorised committee selection/constitution mechanism → schedule/notify → visit/verify → submit EVC report. Where SHC compliance/hearing is applicable: institution submits compliance → SHC reviews/hears as required → records recommendations → forward to Executive Committee process. Do not force all applications through EVC and SHC, or depict them as universal parallel checks. EVC report and SHC recommendations are not final approval.

### Stage 5 — Executive Committee decision and notification
Bring together scrutiny findings, applicable EVC report, SHC recommendations and Regulation Bureau observations → authorised decision process → record official outcome/reference/status → notify institution → sync permitted status through AICTE integration only if authorised. If sync fails, show pending-sync and reconcile safely; never claim external sync succeeded without confirmation.

### Stage 6 — Eligible SAC appeal and continuing compliance
Appeal branch: if the institution is aggrieved and appeal is permitted, submit via authorised process within the deadline derived from the applicable rule version → SAC review/hearing → Council decision where prescribed → update final status/notify. No-appeal path bypasses SAC. Both paths converge on a separate “Any continuing compliance obligations?” decision. If yes, register obligations, collect evidence, route to authorised verification, track deadline reminders and document validity, and close only after required verification. If no, record that no continuing compliance tracking is required. Appeal and compliance are separate processes.

## C. Cross-cutting controls

- Role-based access and data scope, enforced by the backend.
- Versioned regulatory rules and source references.
- Original document preservation and immutable version history.
- Audit records with actor, timestamp, action, reason, rule version and document version.
- Separate statutory deadline clocks and internal operational SLAs.
- Clock start/due/status persistence; pause/resume only where applicable policy permits.
- Notifications for receipt/assignment, authorised deficiency notices and response deadlines, EVC scheduling, decisions, eligible appeals, compliance deadlines and document expiry.
- Pre-due/due/overdue reminders according to configured policy; no invented fixed SLA durations.
- Retry/idempotency/reconciliation for failed jobs and integrations; pending work must survive restarts.
- OCR confidence/uncertainty must be visible and must not be treated as a legal finding.

## D. Dashboards / authorised users

- **Institute:** own applications, status, checklist, uploaded files, correction requests, deadlines, decision notices, eligible appeals and compliance obligations.
- **Scrutiny officer:** assigned cases, original files, source-linked extracted fields, flags, findings, deficiency/re-scrutiny queue and deadlines.
- **EVC coordinator / approved selection workflow:** cases requiring EVC, selection status, scheduling, progress, rescheduling and pending reports. Implement only the current authorised selection procedure.
- **EVC member:** assigned cases, documents, visit details, checklist, observations, evidence and report submission.
- **SHC:** applicable shortcomings, evidence submissions, hearing/review schedule and recommendations.
- **Bureau / Executive Committee:** complete case record, scrutiny and committee reports, observations and authorised decision actions.
- **SAC / appeal workflow:** eligible appeals, source decision, evidence, hearing/review status, recommendations and Council outcome.
- **Rules administrator / approver:** draft configurations, source references, version lifecycle and publication controls. Editing rules does not automatically confer decision powers.
- **Auditor:** appropriately scoped read-only access to audit history.

These should be role-based dashboards in one application, not separate websites.

## E. Product requirements from the SIH problem statement (team brief)

These are project requirements and must not be presented as direct quotations from APH:
- End-to-end application workflow, authorised returns/reverts and reason capture.
- Role-based dashboards, application pendency, ageing, workload and bottlenecks.
- EVC queues, scheduling/completion tracking and reminders.
- Time-bound undertakings, evidence submission, official verification and escalation.
- Lease/other document validity, expiry and renewal tracking.
- Configurable notifications, consolidated reporting and complete audit history.
- Automation supports, but does not replace, statutory scrutiny, inspections or decisions reserved for competent authorities.

## F. Critical implementation guardrails

1. No single universal application route.
2. No fixed SLA/appeal clock unless verified against the exact applicable clause and current amendments.
3. No automatic formal deficiency notice based solely on OCR/algorithmic flags.
4. No automatic approval/rejection from a checklist, OCR result or EVC report.
5. No unrestricted manual assignment of EVC members unless the authorised official process permits it.
6. No conflation of SHC compliance/hearing with SAC appeal.
7. No hardcoded current legal rules embedded in frontend components.
8. Existing submitted cases preserve their rule-version snapshot; rule updates do not silently rewrite existing cases.
9. AICTE portal/API access is not assumed. Build an adapter boundary and use mock integration in the prototype until authorised access is provided.
10. Keep verified rules, proposed product policies and demo-only rules explicitly separated.
