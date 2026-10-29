# AICTE integration boundary (Phase 8)

> **STATUS: UNVERIFIED.** No AICTE API, credential, webhook, database interface or written specification has been made available to this project, and none is assumed to exist.
> Everything below is a boundary *this project* defines so that, if and when an authorised interface exists, only one module has to change. A passing test here proves how this
> application behaves around an adapter. It proves nothing about any AICTE system.

## What is and is not claimed

| Claim | Status |
|---|---|
| The domain model is separated from any external system by an adapter interface, a strict message contract and a mapping module | Built and tested |
| Idempotent sends, bounded retries with back-off, timeouts, outbound rate limit, circuit breaker, signed requests, hash-checked receipts | Built; tested against `httpx.MockTransport` and a **real socket** to the demo receiver |
| Sync states pending / confirmed / failed / reconciliation required, with truthful wording | Built and tested |
| Integration log without secrets or contents | Built and tested |
| A real AICTE endpoint accepts this contract | **Unknown / unverified.** Needs the real interface from an authorised party |
| Authentication, transport security and field mapping of a real AICTE system | **Unknown / unverified** |
| Browser automation or direct database access to any AICTE system | **Never used, never to be used** |

## Layout

```
backend/app/integration/aicte/
  contract.py      Envelope, Receipt, StatusResponse, per-event bodies (strict; unknown fields refused; JSON Schema export). Project-defined, DEMO ONLY.
  adapter.py       AicteAdapter interface; result vocabulary; RateLimiter; CircuitBreaker
  http_adapter.py  HttpAicteAdapter: speaks the contract to AICTE_BASE_URL
  mock.py          MockAicteAdapter (DEMO ONLY): scriptable confirm / transient / permanent / garbled / mismatch / lost_ack
  mapping.py       internal sync kinds <-> contract events; adapter outcome -> sync state
  factory.py       which adapter, from configuration (none by default; mock refused in production)
  demo_server.py   DEMO ONLY receiver (what the other side would have to do), for exercising the HTTP adapter over a real socket
backend/app/services/portal_sync.py   the sync service: records, attempts, reconcile, settle, retry
backend/app/models_integration.py     IntegrationLog (append-only)
backend/app/routers/integration.py    GET /api/integration/status, /contract; POST /api/integration/syncs/{id}/retry
```

## Configuration (environment; secrets via `*_FILE`)

| Variable | Default | Meaning |
|---|---|---|
| `AICTE_INTEGRATION_MODE` | `none` | `none` nothing is sent; `mock` DEMO ONLY in-process adapter (**refused in production**); `http` this contract over HTTPS |
| `AICTE_BASE_URL` | — | `https://…` only in production |
| `AICTE_API_KEY` / `AICTE_API_KEY_FILE` | — | bearer credential issued by the receiver. Never logged, never returned by an endpoint |
| `AICTE_HMAC_SECRET` / `…_FILE` | — | signs `timestamp + "." + body` (HMAC-SHA256) |
| `AICTE_TIMEOUT_SECONDS` / `AICTE_CONNECT_TIMEOUT_SECONDS` | 10 / 3 | hard timeouts |
| `AICTE_HTTP_RETRIES` | 2 | extra tries *within one attempt*, only for connection errors, timeouts, 429/5xx |
| `AICTE_RATE_LIMIT_PER_MINUTE` | 60 | outbound token bucket (per process) |
| `AICTE_CIRCUIT_FAILURES` / `AICTE_CIRCUIT_COOLDOWN_SECONDS` | 5 / 60 | consecutive failed submits that pause calls |
| `SYNC_MAX_ATTEMPTS` / `SYNC_RETRY_BACKOFF_SECONDS` | 8 / 60 | attempts across the life of a record (back-off doubles, capped at 6 h) |

`ENVIRONMENT=production` **refuses to start** if the mode is `mock`, the URL is not `https://`, or the key/secret are missing in `http` mode.

## The contract (version `sih-demo-0.1`)

`POST {base}/v1/outcomes` with an `Envelope`: `contract_version`, `message_id` (our sync record's id — the same message is always sent under the same id), `event`
(`OFFICIAL_OUTCOME_ISSUED`, `COUNCIL_RATIFICATION_RECORDED`, `COUNCIL_APPEAL_DECISION_RECORDED`), `payload_hash` (sha256 of the internal record), `sent_at`, `body`.
Bodies carry **references and hashes only** — application reference, outcome, decision version/hash, the issued document's reference and sha256 — never a document, a name or free text.
Headers: `Authorization: Bearer`, `Idempotency-Key: <message_id>`, `X-Timestamp`, `X-Signature`, `X-Contract-Version`.

The receiver answers with a `Receipt`: `RECEIVED` | `DUPLICATE` | `REJECTED`, echoing `message_id` and `payload_hash`, with an `external_ref`.
`GET {base}/v1/messages/{message_id}` returns what the receiver holds (`StatusResponse`) — this is how a doubtful state is settled. `GET /api/integration/contract` serves the JSON Schemas.

## States (what is *known*)

| State | Meaning | Next |
|---|---|---|
| `NOT_CONFIGURED` | no adapter: nothing was sent | picked up automatically once an adapter exists |
| `SYNC_PENDING` | not confirmed; **safe to retry** (same id and key) | retried with back-off, bounded by `SYNC_MAX_ATTEMPTS` |
| `SYNCED` (shown as *Confirmed*) | a receipt echoing our message id **and** hash | terminal |
| `FAILED` | the receiver refused it, or the attempts ran out | **never retried automatically**; an operator (`integration:manage`) can grant a fresh bounded set |
| `RECONCILIATION_REQUIRED` | an answer came back that cannot be trusted (garbled, wrong hash, no reference) | `reconcile` *asks* the receiver what it holds: holds it with our hash → `SYNCED` without re-sending; does not hold it → `SYNC_PENDING`; holds something else → stays for a person |

A lost acknowledgement (the receiver stored the message but its answer never arrived) therefore ends as one stored message and one confirmation, not two messages. A sync problem never undoes the decision that
caused it, and the institution is never told AICTE's portal was updated before it is confirmed.

Recover after an outage or a fix: `python -m app.reconcile_decisions` (retry everything now) or the scheduler's back-off-respecting pass.

## Logs

`integration_logs` (append-only; also protected by a database trigger on PostgreSQL) holds one row per call: adapter, operation, attempt number, outcome code, HTTP status, latency, and the *hash* of the message.
No header, body, document, name or secret is stored. The application's access log records method, path (no query string), status and duration. Error responses and logs show exception *types and code locations*, never messages.

## Before this can be called "integrated"

1. An authorised AICTE party supplies the real interface (endpoints, authentication, field definitions, error codes, rate limits, test environment) in writing.
2. The contract and `mapping.py` are reconciled to it — this may mean a different shape entirely.
3. Credentials are issued through the authorised channel and stored as secrets.
4. A connection to their **test** environment is exercised end to end (confirm, duplicate, reject, outage), and the results are recorded in `docs/ACCEPTANCE_REPORT.md`.
5. Until then every screen and document keeps saying *unverified*, and the default mode stays `none`.
