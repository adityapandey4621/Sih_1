# Operations

For the people running the system. Commands assume the repository root; in containers drop the `.\.venv\Scripts\` prefix and run them with `docker compose exec`/`run`.

## Health, readiness, logs

| Check | How | Healthy |
|---|---|---|
| Is the process up? | `GET /api/health` | `200 {"status":"ok"}` |
| Can it do its job? | `GET /api/ready` | `200 ready`; `503 not_ready` names *which* of `database`, `schema_current`, `storage_writable` failed (booleans only) |
| Is the scheduler keeping up? | `GET /api/clocks/health` (Bureau) | `due_and_waiting` is 0 or small and not growing; `failed` is empty |
| Is the audit trail protected? | `python -m app.db_protect` | "All append-only tables are protected by triggers." |
| Is the schema current? | `python -m alembic check` | "No new upgrade operations detected." |
| Integration state | `/app/integration` (Bureau, auditor, administrator) or `GET /api/integration/status` | counts by state; nothing `FAILED` unattended |

Logs go to standard output: `app.access` (one line per request: `request-id METHOD /path status ms` — no query string, body, cookie or header), `app.scheduler`, `app.worker`, `app.notifications`, `app.ready`.
Every response carries `X-Request-ID`; an error response quotes it. A 500 logs the exception *type* and the last code locations — never its message (database errors can carry parameters).
Ship these logs and the `audit_events` table to a place the application's database account cannot rewrite.

## Processes

```
python -m app.worker                      # document processing; --once drains and exits; --enqueue-missing recreates jobs for versions that have none
python -m app.scheduler                   # deadlines and alerts; --once for one pass; --requeue-failed gives exhausted alerts another bounded set of attempts
python -m app.reconcile_decisions         # retry unconfirmed external syncs now; open missing decision cases; close decision cases whose appeal window ran out
python -m app.reconcile_committee         # re-derive committee hand-offs
python -m app.reconcile_notifications     # re-create issued-notice notifications that are missing
```

All are idempotent and safe to run repeatedly and concurrently. **Recovery after an outage is: start them again.** The scheduler re-derives every clock from the recorded cases, plans missing alerts, takes back work abandoned by a dead process
when its lease expires (default 120 s) and sends what became due — skipping what is no longer needed. The worker does the same for document jobs (lease 300 s).

## If something is wrong

| Symptom | Likely cause | Action |
|---|---|---|
| `/api/ready` → `database: false` | PostgreSQL down or credentials wrong | check the DB, `DATABASE_URL(_FILE)`; the API recovers by itself when the DB returns (`pool_pre_ping`) |
| `schema_current: false` | code newer than the schema | run `python -m alembic upgrade head` |
| API refuses to start in production, listing problems | a development setting reached production | fix the listed settings (this is intended) |
| Uploads answer `503 scanner_required` | production requires malware scanning and ClamAV is unreachable | start/repair `clamav`; do not disable the requirement |
| Documents stay "processing" | no worker running, or OCR missing | start `app.worker`; check `OCR_ENGINE`/Tesseract; failed jobs show a code on the application's evidence page |
| Alerts not arriving; `due_and_waiting` growing | scheduler not running | start `app.scheduler` |
| Alerts in `FAILED_EXHAUSTED` | the notification store failed repeatedly | fix the cause, then `python -m app.scheduler --requeue-failed` |
| Sync `FAILED` / `RECONCILIATION_REQUIRED` | the external system refused, or answered untrustworthily | read `/app/integration`; fix; a Bureau user presses *Try again*, or `python -m app.reconcile_decisions` |
| Everyone gets `429` | rate limits too tight for the traffic, or `TRUSTED_PROXY_COUNT` wrong (all clients look like the proxy) | check the proxy chain; adjust `RATE_LIMIT_*` |
| Cookies not kept / CSRF 403 | `ALLOWED_ORIGINS` does not equal the browser's origin, or the site is served over http in production | fix the origin; serve over https |

## Backup and restore

```
.\ops\backup.ps1 -Database sih_aicte -StorageDir backend\storage -OutDir backups     # or ops/backup.sh
.\ops\restore.ps1 -Verify -Dump backups\db-<stamp>.dump                              # PROVE it restores, into a throw-away database
.\ops\restore.ps1 -Target sih_aicte -Dump backups\db-<stamp>.dump -Files backups\files-<stamp>.tar.gz -IKnowThisOverwrites    # a real restore: stop the application first
```

* Back up the database **and** the files together; the database refers to files by key.
* A backup contains personal data and private documents. Encrypt it, store a copy off the machine, restrict access, and set a retention period that the institution's policy supports.
* A backup that has not been restored is a hope. `-Verify` restores into a scratch database, checks the checksums, the schema revision, row counts and the append-only triggers, then drops it. Do this for each backup you rely on and on a schedule.
* PostgreSQL point-in-time recovery (WAL archiving) is recommended for production and is a platform feature, not something this repository provides.
* Restore objectives (RPO/RTO) are not defined here; they are an organisational decision.

## Secrets and rotation

Secrets are files (`secrets/*`) or your platform's secret store. Rotate by replacing the value and restarting the affected services. Rotating `aicte_hmac_secret` must be coordinated with the receiver.
Rotating the database password: change it in PostgreSQL, update `db_password` and `database_url`, restart. User passwords are Argon2id hashes; `python -m app.set_password` resets one (an operator action, audited by the platform's own logs).

## Retention and data protection

Nothing is deleted by the application: decisions, evidence, reviews, history and the audit trail are append-only (application guard *and*, on PostgreSQL, a database trigger). A retention and erasure policy, and the legal basis for what is kept, are
organisational decisions not yet made (gap G-P8-6). Do not remove rows by disabling the triggers without that decision.

## Upgrading dependencies

`pip-audit -r requirements.txt` and `npm audit` are part of CI. See `docs/SECURITY_REVIEW.md` for the results of the last run on the development machine.
