# Reply to Claude's rule-source question

I am adding three project-specific reference files to the repository:

- `docs/sources/AICTE_SIH26262_APH_Selected_Provisions_Reference.pdf` — a curated summary of selected relevant APH provisions, clause/page references, selected Table 2.3 route examples and explicit verification gaps. It is **not the full APH and is not an official AICTE publication**.
- `docs/sources/AICTE_SIH26262_WORKFLOW_AND_RULES.md` — our complete proposed six-stage flow, role/dashboard definitions, route constraints, deadlines/notifications, and the distinction between verified source summaries and proposed product policy.
- `docs/sources/AICTE_Approval_Workflow_reference.png` — our workflow diagram image. Treat it as visual/context guidance only, not as a legal source.

Please retry the official AICTE APH URL first: https://www.aicte.gov.in/sites/default/files/APH%20Final.pdf. If it remains unavailable, use the curated PDF and Markdown as a map to relevant clauses, but do not claim that the entire official handbook or the latest corrigenda have been verified. Mark unknown/current-rule details as explicit source gaps. A seed configuration may be created only as `DEMO ONLY — NOT VERIFIED AS CURRENT OFFICIAL RULES` until the appropriate official source is confirmed.

Important route constraints:
- Chapter I clauses 1.8–1.11 and Chapter II clauses 2.23–2.24 plus Table 2.3 are relevant.
- Table 2.3 has category-specific processing paths; `AA` means applicable depending on the outcome of prior processing steps. Never force one universal route.
- Stage 2 “Any unresolved issue?” NO goes directly to scrutiny officer review. YES identifies/handles the issue; OCR does not issue a formal deficiency notice.
- Re-scrutiny rechecks flagged deficiencies and must not auto-pass.
- EVC, SHC and SAC have distinct roles. EVC findings are not final approval; SHC is a compliance/hearing process; SAC is an appeal process.
- The applicable Executive Committee/Council decision process retains regulatory decision authority.
- Appeal deadline triggers differ between Chapter I clause 1.11 and Chapter II clause 2.24. Do not use one global appeal clock.
- The reported December 2025 EVC/SAC corrigendum needs verification from the primary official source before coding those changes as confirmed.
- Keep statutory deadlines separate from internal SLAs. Do not invent fixed processing durations.
- AICTE API access is not assumed; mock the integration boundary until authorised access exists.

Use these files during planning and implementation. Keep the rules in a versioned data catalogue with source references and approval status, rather than hardcoding regulatory rules in frontend components.
