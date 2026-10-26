# Official handbook verification (read 2026-10-27)

**Source read:** `docs/sources/2025031399.pdf` — "Approval Process Handbook 2024-25 to 2026-27" (AICTE), 231 pages, PDF created 8 December 2023, SHA-256 `153d54816f2cf11489218e239113544e8d49b73680009688baa7dc4880636051`. Supplied by the team after the official host stayed unreachable. This is the first primary-source reading in the project.

**What "verified" means here:** the clause text below was read in that PDF and matched the curated summary. It covers **this edition only**. Corrigenda or circulars issued after December 2023 (a December 2025 one is reported) have **not** been checked against a primary source, so currency is still open (G-P4-6).

## What the handbook says that the product relies on

| Clause | Text (paraphrased) | Where it is used |
|---|---|---|
| Ch. I 1.8(a) | Scrutiny/Re-Scrutiny Committee members are chosen through a web-portal automated selection process made available by AICTE | The assignment boundary: no interface to that process exists, so only a labelled demo assignment is possible |
| Ch. I 1.8(b) | The Regulation Bureau informs institutes and experts of the date and time of (re-)scrutiny | There is no fixed response period; the next date is scheduled by the authority |
| Ch. I 1.8(f) | Based on the Scrutiny Committee's recommendations, deficiencies are communicated to the applicant **through the AICTE web portal**; institutions are expected to fulfil them for re-scrutiny | What a formal deficiency notice in this product represents |
| Ch. I 1.8(g) | The Re-Scrutiny Committee verifies **ONLY** the deficiencies pointed out by the Scrutiny Committee | Re-scrutiny cannot record new findings (enforced, `409 rescrutiny_scope`) |
| Ch. I 1.8(h) | Applications found in order go on to EVC | Hand-off is a route-driven next stage, never approval |
| Ch. I 1.10(a)–(b) | The Executive Committee decides; decisions are uploaded as LoA / **LoD** / LoR | A "Letter of Deficiency" is an Executive Committee output, **not** the scrutiny-stage notice. The product's notice is the cl. 1.8(f) communication and is not called an LoD |
| Ch. II 2.23.1, 2.23.4, Table 2.3 | Chapter II applications are scrutinised per cl. 1.8; Table 2.3 lists the steps per application type; AA = as applicable depending on earlier outcomes | The five seeded route shapes were compared with the table: all five match |
| Ch. VIII item 12 | Policy/technical queries and clarifications go **only** through the CSS portal | No clarification channel is built inside this product (G-P4-5) |
| Annexure-7 | Flow chart: Scrutiny → (deficiency) Re-scrutiny → Executive Committee, or (nil deficiency) → EVC; the SAC loop is marked "will repeat for only once" | The chart shows one re-scrutiny, but the text states no cap, so no cap is enforced |

## What the handbook does **not** state (so nothing is encoded)

- **No response period** for deficiencies communicated under cl. 1.8(f). The only day counts near deficiencies are the 7-day appeal windows (cl. 1.11(a): from the date of issue of the LoD; cl. 2.24(a): from the date of uploading the Executive Committee decision) and are **different events**. They are not reused for notices.
- **No named issuing officer** for the cl. 1.8(f) communication, **no notice template**, signature or seal.
- **No limit** on the number of scrutiny or re-scrutiny rounds in the text.
- **No document checklists encoded yet.** The lists exist (Annexure-1 for a new institution; Annexure-2 sections 2.3–2.16 for Chapter II), but turning them into versioned checklist items is a separate catalogue task. Until then official rule versions stay unpublishable and no document requirement is invented.

## What changed in the product because of this reading

1. The two APH sources (Ch. I 1.8–1.11; Ch. II 2.23–2.24 and Table 2.3) are now `VERIFIED_OFFICIAL` in the seed, with the file reference, hash and the edition-only caveat. The project summary, the mirror and the corrigendum report stay `SECONDARY_UNVERIFIED`.
2. The official-classified drafts carry a policy stating that **no fixed period exists** (`duration` empty, source verified). The policy resolver treats that as *settled*: it no longer raises a configuration gap or the "response period unresolved" blocker, and still sets **no** due date. A missing policy, or one citing an unverified source, still blocks a non-demo notice.
3. Re-scrutiny cannot record new findings (cl. 1.8(g)); the API refuses and the case page explains why.
4. A non-demo notice is still blocked by the missing approved template (and the unselectable official reviewer assignment). Those are the remaining reasons it cannot be issued.
