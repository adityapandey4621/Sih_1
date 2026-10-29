# Document processing (Phase 3)

> Upload success is not extraction success. Extraction success is not evidence verification. Evidence verification is not approval. OCR uncertainty is not a regulatory deficiency.

The output of this subsystem is a **reviewable evidence package**: what was uploaded, whether it could be read, what values were found where, and what looks worth a person's attention. It never approves, rejects, verifies or notifies anyone officially.

## Pipeline

```
upload (API, synchronous)            worker (python -m app.worker, asynchronous)
─────────────────────────            ──────────────────────────────────────────────────────────────
auth + institution scope             claim job (lease) ─► A technical validation ─► B malware scan
draft-only, item is a doc slot                     │              (fails: stop, no OCR)       (infected / required-but-absent: stop)
size limit, signature sniff                        ▼
extension + declared type agree      C text: digital PDF text per page; OCR for pages with too little
store file under generated key                     │
new version (slot, v+1, chain)                     ▼
processing row + job + cross-check   D field extraction (configuration-driven) ─► persist results atomically
 committed together                                │
                                                   ▼
                                     E cross-check job: deterministic preliminary findings
```

Code: `app/processing/` (pure stages, no database), `app/jobs.py` (queue), `app/evidence.py` (persistence, facts, state wording), `app/worker.py`, `app/services/documents.py` (versioned upload), `app/routers/documents.py`.

## What each status answers

| Layer | Question | Values |
|---|---|---|
| Upload | Was a file stored? | `ApplicationDocument` row (immutable); always `verification_status = NOT_VERIFIED` (DB check constraint) |
| Technical | Is it readable and structurally sound? | `PENDING`, `PASSED`, `FAILED` (+ code) |
| Scan | Did a scanner see it? | `PENDING`, `CLEAN`, `INFECTED`, `SCANNER_UNAVAILABLE`, `ERROR` |
| Extraction | Was text/values read? | `QUEUED`, `RUNNING`, `COMPLETE`, `PARTIAL`, `FAILED`, `BLOCKED`, `SUPERSEDED` |
| Field | Was this value found? | `EXTRACTED`, `UNCERTAIN`, `NOT_FOUND` ("unable to extract" — nothing invented) |
| Finding | Is there something for a reviewer to look at? | `PRELIMINARY_ISSUE`, `SUPERSEDED` — never a deficiency |

The server turns these into plain-language display states (`evidence.describe`), the only place wording lives. The UI renders `state.label` / `state.explanation` and never maps enums itself:

| Code | Label | Terminal? |
|---|---|---|
| *(no file)* | No file submitted | — |
| `received` | File received | no |
| `extraction_queued` | Extraction queued | no |
| `extraction_running` | Technical checks in progress | no |
| `extraction_complete` | Extraction complete | yes |
| `review_recommended` | Human review recommended | yes |
| `processing_failed` | Processing failed (retry offered only where it can help) | yes |
| `technical_failed` | File could not be read | yes |
| `held` | Held before processing (scanning required but unavailable) | yes |
| `quarantined` | File quarantined | yes |
| `superseded` | Replaced by a newer version | yes |

Every explanation includes: *"These are technical checks on the file only. They are not verification, and do not mean the document has been accepted."* Upload-in-progress and upload-failed are client states during the request.

## Versions

Each checklist item has a **slot** (`document_slots`) whose versions are `application_documents` rows: `slot_id`, `version_number`, `previous_version_id`, `superseded_at`. Replacing a file creates version *n+1* and marks the previous one superseded; nothing is overwritten or deleted. Queued work for the superseded version is cancelled; results already produced stay with that version. Extracted fields, pages, jobs and findings reference the exact version. A new version never inherits results.

**Same-bytes policy:** identical SHA-256 to the *current* version → no new version, no job (`200`, `X-Upload-Result: duplicate_of_current`; the stored duplicate is deleted). Identical to an *earlier* version → a new version (a deliberate revert). The same file attached to two *different* requirements is allowed and raises a low-priority `potential_duplicate_file` finding.

Uploads and removals are allowed only while the application is a **draft**. Replacing evidence after submission needs an authorised correction mechanism (Phase 4).

## Extraction is configuration, not code

A checklist item may carry `extraction`: a list of `{field_key, label, kind(text|date|identifier), anchors[], required, compare_group, compare_to_application}` in the rule version (authored in the rule editor, frozen in the snapshot like everything else). The engine finds the value after one of the labels (case- and punctuation-insensitive; fuzzy label matching **only** on OCR text, and then the value is `UNCERTAIN`). Numeric dates are read day-first and normalised to ISO. Values below `OCR_MIN_CONFIDENCE`, unparsable dates, multiple different values in one document, or garbled labels are `UNCERTAIN`. Nothing is hard-coded about which values matter.

Preliminary checks (`processing/checks.py`, deterministic, exact comparison after normalisation — no fuzzy/semantic matching): missing vs unreadable file, text extraction failure, low-confidence pages, expected value not found, uncertain value, cross-document inconsistency (compare groups, and document vs application record), same file reused, scanner unavailable, malware flagged, manual inspection recommended. Issue types, priorities and the wording shown to institutions live in `processing/issues.py`.

## Errors and retries

| Kind | Codes (examples) | Behaviour |
|---|---|---|
| **input** | `pdf_corrupt`, `pdf_encrypted`, `unsupported_format`, `too_many_pages`, `image_too_large`, `malware_detected` | Permanent. One attempt, then `failed_permanent`. Never retried. |
| **config** | `ocr_unavailable`, `scan_required_unavailable` | Not retried automatically; a person may request a retry once the environment is fixed. |
| **transient** | `storage_unavailable`, `ocr_timeout`, `ocr_failed`, `scanner_error`, `worker_lost`, `unexpected` | Automatic retry with exponential backoff up to `JOB_MAX_ATTEMPTS`, then `failed_exhausted` (visible, retryable by a person up to `MAX_MANUAL_RETRIES`). |

Error messages shown to people come from a fixed catalogue (`processing/errors.py`): no paths, no stack traces, no document content. Logs carry job/document IDs and error codes only; unexpected exceptions are logged by class name.

## Durable jobs (PostgreSQL)

`processing_jobs`: one `PROCESS_DOCUMENT` per version (partial unique index), coalesced `CROSS_CHECK` per application. Claim = compare-and-set update (`FOR UPDATE SKIP LOCKED` on PostgreSQL) with a **lease**; the worker extends it between stages. A job whose lease expires (worker crashed/restarted) is re-queued by `reap_expired` — no job is lost. Results are written in one transaction **only if the worker still owns the job**; a stale worker's results are discarded. Retrying re-queues the same job row and replaces (not appends) results.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `STORAGE_DIR` | `storage` | Private directory; generated keys only; never served statically |
| `MAX_UPLOAD_MB` | 10 | Upload limit |
| `OCR_ENGINE` | `tesseract` | `tesseract` or `none` |
| `TESSERACT_CMD` | *(PATH)* | Path to the executable |
| `TESSDATA_PREFIX` | *(default)* | Directory with `*.traineddata` |
| `OCR_LANGUAGES` / `OCR_DPI` / `OCR_TIMEOUT_SECONDS` | `eng` / 200 / 90 | |
| `OCR_MIN_CONFIDENCE` | 70 | Below this a page/value is uncertain |
| `PDF_MIN_TEXT_CHARS` | 25 | A PDF page with less text is treated as scanned |
| `MAX_PDF_PAGES` / `MAX_IMAGE_PIXELS` | 150 / 40,000,000 | Safety limits |
| `SCANNER` | `none` | `none` or `clamd` (`CLAMD_HOST`, `CLAMD_PORT`) |
| `REQUIRE_MALWARE_SCAN` | `false` | Hold files until a scanner exists. **Forced on in production.** |
| `JOB_MAX_ATTEMPTS`, `JOB_LEASE_SECONDS`, `JOB_RETRY_BACKOFF_SECONDS`, `MAX_MANUAL_RETRIES`, `WORKER_POLL_SECONDS` | 3 / 300 / 30 / 3 / 2 | Queue behaviour |

**Installing Tesseract (Windows):** install the UB-Mannheim build, or extract it to a folder (no admin needed) and set `TESSERACT_CMD` and `TESSDATA_PREFIX`. Linux: `apt install tesseract-ocr`. Without it the application works; scanned pages are reported as unreadable (`ocr_unavailable`, retryable) and digital PDFs are unaffected.

## Running

```powershell
cd backend
.\.venv\Scripts\python -m uvicorn app.main:app --port 8000     # API
.\.venv\Scripts\python -m app.worker                           # worker (run 1+); Ctrl+C to stop
.\.venv\Scripts\python -m app.worker --once                    # drain the queue and exit
.\.venv\Scripts\python -m app.worker --enqueue-missing --once  # backfill jobs for file versions that have none
```

## Security assumptions and limits

- Uploads are untrusted. Type is decided from **content** (signature) and must agree with the extension and declared type; PDFs and images are opened only by PyMuPDF/Pillow inside the worker, with page and pixel limits. Previews are **rasterised PNGs**: a PDF is never served inline.
- Files live in a private directory under generated keys; access is checked on every request: the owning institution, or a reviewer (read-only, **submitted** applications only). Anything else is "not found" and audited. Downloads and reviewer views are audited.
- **Malware scanning is not performed unless a scanner is configured.** Without one, every file is labelled *"NOT performed"*, never clean; in production, files are held. The clamd client is tested against a **fake** clamd (INSTREAM framing), **not** against real ClamAV.
- Extracted page text is stored in PostgreSQL and is as sensitive as the document. There is no encryption-at-rest or retention policy yet.
- **PyMuPDF is AGPL-3.0 (or commercial).** Review the licence before any distribution or hosted deployment.
- OCR is a local executable; no document leaves the machine and no external AI service is used.

## Known limitations (deferred)

Label-anchored extraction only: no tables, handwriting, layout analysis or non-English text. Phase 3 has no reviewer assignment, so every scrutiny officer sees every submitted application. No duplicate-application detection. Preview rendering is on demand and uncached. The worker is a polling loop with no metrics endpoint. Rule versions published before Phase 3 carry no extraction configuration; publish a new version to add it.
