# API reference (Phase 2 additions)

All state-changing requests need the CSRF header (`X-CSRF-Token`, double-submit with cookie `sih_csrf`) and an authenticated session cookie. Permissions are checked on the server; a refusal is `403` and is written to the audit log. Interactive docs: `/api/docs` (non-production).

**Errors.** `{"detail": "<message>", "code": "<stable code>", "issues": [{"code","message","path"}]}`. Validation errors from request schemas use FastAPI's `detail` list.

| Status | Typical `code` |
|---|---|
| 401 | not signed in / session ended |
| 403 | `no_institution`, permission refused |
| 404 | `not_found` (also returned for another institution's records) |
| 409 | `stale_version`, `not_draft`, `already_submitted`, `rule_version_changed`, `no_applicable_configuration`, `not_editable`, `invalid_transition`, `self_approval`, `immutable_record`, `duplicate_key`, `open_version_exists` |
| 413 / 415 | `file_too_large`, `type_not_allowed`, `type_mismatch` |
| 422 | `validation_failed`, `submission_blocked`, `publication_blocked`, `incomplete_configuration` |
| 503 | `storage_unavailable` |

## Catalogue (institution-facing) — `application:create`

| Method & path | Purpose |
|---|---|
| `GET /api/catalogue/options` | Active categories and the academic years that have a published, selectable version |
| `GET /api/catalogue/checklist?category_id&academic_year` | The resolved checklist, route and sources, or `409 no_applicable_configuration`. No side effects |

## Applications — institution users only, scoped to their own institution

| Method & path | Permission | Purpose |
|---|---|---|
| `POST /api/applications` | `application:create` | Create a DRAFT bound to the resolved rule version. Body `{category_id, academic_year}`; any institution in the body is ignored. `409` + a recorded configuration gap if none applies |
| `GET /api/applications` | `application:read_own` | List; filters `status` (`DRAFT`/`SUBMITTED`), `category_id`, `academic_year`, `q` (reference), `page`, `page_size` (≤ 50) |
| `GET /api/applications/{id}` | `application:read_own` | Detail: answers, checklist states, documents, rule version, route, history |
| `PATCH /api/applications/{id}` | `application:update_own` | `{form_data, row_version}`; partial merge; compare-and-set; draft only |
| `POST /api/applications/{id}/rebind` | `application:update_own` | Explicitly move a draft to the configuration now in force |
| `POST /api/applications/{id}/documents` | `application:update_own` | Multipart `item_key`, `file`; draft only |
| `DELETE /api/applications/{id}/documents/{doc}` | `application:update_own` | Soft-remove a draft file |
| `GET /api/applications/{id}/documents/{doc}/download` | `application:read_own` | Authenticated download (`attachment`, `nosniff`, `no-store`) |
| `POST /api/applications/{id}/submit` | `application:submit` | `{row_version}`; validates the checklist, records actor/time/version/snapshot; at most once |

Upload rules: PDF, PNG, JPEG only; content is sniffed and must agree with extension and declared type; limit `MAX_UPLOAD_MB` (default 10); stored privately under a generated key; `verification_status` is always `NOT_VERIFIED` (also a database check constraint).

## Rule catalogue administration — `/api/rules/*`

| Method & path | Permission |
|---|---|
| `GET /categories`, `GET /sources`, `GET /sets`, `GET /versions/{id}`, `GET /gaps` | `rules:read` |
| `POST /categories`, `PUT /categories/{id}`, `POST /sources`, `PUT /sources/{id}` | `rules:edit` (a source cited by a version in review/published is locked: `409 immutable_record`) |
| `POST /sets` (creates v1 draft), `POST /sets/{id}/versions`, `PUT /versions/{id}`, `POST /versions/{id}/submit-review`, `POST /gaps/{id}/resolve` | `rules:edit` |
| `POST /versions/{id}/request-changes`, `POST /versions/{id}/publish` (`{supersede, note}`), `POST /versions/{id}/retire` (`{reason}`) | `rules:approve` |

## Roles and permissions added in Phase 2

| Role | Added permissions |
|---|---|
| `institute_user` | `application:create`, `application:read_own`, `application:update_own`, `application:submit` |
| `rules_admin` (author) | `rules:read`, `rules:edit` |
| `rules_approver` (new) | `rules:read`, `rules:approve` |
| `auditor` | `rules:read` (read-only) |

No other role gained anything; scrutiny, committee and administrator roles keep their Phase 1 shells.

## Phase 3 — documents, processing and evidence

| Method & path | Who | Purpose |
|---|---|---|
| `POST /api/applications/{id}/documents` | owner (`application:update_own`) | Upload a **new version** for a checklist item (draft only). `201` new version; `200` + `X-Upload-Result: duplicate_of_current` when the bytes equal the current version |
| `GET /api/applications/{id}/documents` | owner | Every version with its processing state |
| `GET /api/applications/{id}/evidence` | owner | Per checklist item: slot state, versions, extracted values, institution-visible findings, disclaimer |
| `GET /api/documents/{doc}` | owner, or reviewer if submitted | Version metadata, siblings, state |
| `GET /api/documents/{doc}/download` | same | Attachment download (audited; `409 quarantined` for flagged files) |
| `GET /api/documents/{doc}/pages/{n}/preview` | same | Rasterised PNG of one page (`404` out of range, `409` unreadable) |
| `GET /api/documents/{doc}/extraction` | same | Page text/method/confidence and extracted values with page + version |
| `GET /api/documents/{doc}/history` | same | Processing history |
| `POST /api/documents/{doc}/retry` | owner or reviewer with `document:retry` | Re-queue a failed job **only if retrying can help** (`409 not_retryable`, `already_pending`, `already_succeeded`, `retry_limit`) |
| `GET /api/review/applications` | `evidence:read` | Submitted applications with counts |
| `GET /api/review/applications/{id}/evidence` | `evidence:read` | Full evidence package (priorities, check names, institution); `404` for drafts; audited |

Other roles, other institutions and anonymous callers get `404`/`403`/`401` as appropriate. There is **no** endpoint that issues a notice, approves, rejects or verifies anything.

New codes: `quarantined`, `file_unreadable`, `not_retryable`, `already_pending`, `already_succeeded`, `retry_limit`. Document state codes: `received`, `extraction_queued`, `extraction_running`, `extraction_complete`, `review_recommended`, `processing_failed`, `technical_failed`, `held`, `quarantined`, `superseded`.
