# Rule catalogue

The catalogue is data, not code. The interface and the application service contain **no** categories, document lists, routes, durations or eligibility conditions; they read them from published rule versions.

## Concepts

| Concept | Table | Notes |
|---|---|---|
| Source | `rule_sources` | Title, authority, URL / stored-document reference, clause, page, dates, **verification status**, applicability notes |
| Category | `application_categories` | Stable `key`; what an institution selects |
| Rule set | `rule_sets` | A lineage of versions for one category. **Classification**: `OFFICIAL` or `DEMO` |
| Rule version | `rule_versions` | Academic years, effective dates, change summary, status, row version, frozen snapshot + hash |
| Checklist item | `checklist_items` | Stable `item_key`, type (`document_upload`, `form_field`, `declaration`), required/optional, explanation, options/validation, `applies_when`, source + clause |
| Route stage | `route_stages` | Ordered stages, `next_stage_keys`, **conditional** stages with a stated condition |
| Deadline policy | `deadline_policies` | Statutory vs internal SLA, trigger event, duration **only if sourced** (nullable), calendar convention, pause/resume note. Stored only; nothing is scheduled in Phase 2 |
| Version history | `rule_version_events` | Per-version history; the global `audit_events` log records the same actions |
| Gap | `configuration_gaps` | A category/year that someone tried to use but has no single approved configuration |

Source verification statuses: `VERIFIED_OFFICIAL` (read in the issuing authority's own publication), `SECONDARY_UNVERIFIED` (mirror, summary or secondary report), `PROPOSED_POLICY` (our own proposed behaviour), `DEMO_ONLY` (invented for the demo).

## Lifecycle

```
draft ──submit──▶ in_review ──publish──▶ published ──retire──▶ retired
  ▲                   │
  └──request changes──┘
```

Only these transitions exist (`domain.ALLOWED_TRANSITIONS`); the backend rejects everything else.

| Action | Permission | Rules |
|---|---|---|
| Create / edit draft, submit for review | `rules:edit` (rules administrator) | Draft only; optimistic concurrency (`row_version`); submission needs structural completeness |
| Request changes, publish, retire | `rules:approve` (rules approver) | Approver must not have contributed to the version (`self_approval`); roles are also disjoint |
| Read | `rules:read` | Rules administrator, approver, auditor |

### Publication gates (`services/rules.publication_issues`)

A version is published only if **all** hold; otherwise the API answers `422 publication_blocked` with itemised issues:

1. At least one academic year, a change summary, at least one cited source; category active.
2. **OFFICIAL** classification: every cited source is `VERIFIED_OFFICIAL`. **DEMO** classification: only where `ALLOW_DEMO_RULES` is true and `ENVIRONMENT != production`.
3. Non-empty checklist; each required item has an explanation; conditions reference another form field/declaration and a valid option.
4. Non-empty route; valid next-stage references; reachable from the first stage; acyclic; has a final stage; conditional stages state their condition.
5. Any stated duration cites a source; every element's source is among the version's cited sources.
6. No overlap with another published version for the same category + academic year + effective range. Overlap with another rule set always blocks; overlap with an earlier version of the **same** rule set requires `supersede: true`, which retires that version explicitly in the same transaction.

### Immutability

Published versions cannot change: the service returns `409 not_editable`, and `app/guards.py` independently raises at ORM flush time for edits to reviewed content, snapshots, sources cited by a reviewed version, submitted applications and their documents. Changes require a new version (`POST /api/rules/sets/{id}/versions`), cloned from the latest one.

On publication the version stores a canonical JSON **snapshot** (category, sources, checklist, route, deadlines) and its SHA-256 **hash**. Applications use the snapshot, never the live tables.

## Selection and gaps

`resolve_version(category, academic_year, today)` considers published, in-force versions (demo ones only where enabled). Exactly one must remain, with checklist, route and snapshot; otherwise it returns `no_approved_version`, `ambiguous` or `incomplete`. Creating an application in that situation answers `409 no_applicable_configuration`, creates nothing, and records/increments a **configuration gap** visible to rule staff. Publishing a covering version closes matching open gaps.

## Seeding

```
python -m app.seed         # demo users (needs SEED_PASSWORD)
python -m app.seed_rules   # catalogue
```

| Seeded item | Standing |
|---|---|
| 6 sources (APH Ch. I and II, the project reference pack, a mirror, a reported corrigendum) | `SECONDARY_UNVERIFIED` — the official PDF returned HTTP 502 on 2026-10-26; contents were taken from the project's curated pack, which says it is not authoritative |
| 5 categories named after the Table 2.3 rows in the pack | Names/applicability **not independently verified** |
| 5 `aph-table-2-3-*` OFFICIAL-classified rule sets, each a **draft** | Route shapes from Table 2.3 only. **Checklist: GAP** (the sources contain no verified document list). An appeal-window policy citing Ch. II cl. 2.24 (7 days, calendar convention unspecified, applicability to the category **not confirmed**). Cannot be published |
| 5 `demo-*` DEMO rule sets, each **published** through the real author → review → publish flow | **DEMO ONLY — NOT VERIFIED AS CURRENT OFFICIAL RULES.** Invented checklist items labelled `[DEMO]`; no durations |
| `demo-intake-increase-or-additional-courses` v2 | Left **in review** so the review screen has real content; publishing it needs an explicit supersede |

Nothing seeded is verified official rule data.

## Not modelled yet (deliberately)

- Escalation ladders, pause/resume mechanics (free-text note only).
- Per-institution or per-region applicability.
- Rule migration of existing drafts is explicit (`POST /api/applications/{id}/rebind`); migration of **submitted** applications has no mechanism and would need an audited, authorised process.
