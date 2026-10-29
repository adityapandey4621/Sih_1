# Deployment

Two things are described here and they must not be confused:

* **Local / demonstration** — everything on one machine with Docker, development settings, DEMO ONLY rules. Good for evaluating the product. Not for real applications.
* **Production** — the same images with `ENVIRONMENT=production`, which refuses to start with development settings, requires malware scanning, TLS in front, real secrets, backups and an operator.

> **What has and has not been verified.** See `docs/ACCEPTANCE_REPORT.md` for exactly what was executed on the development machine (a Windows laptop with portable PostgreSQL and no cloud access).
> The container images, the compose files, the CI workflow and the nginx configuration were **written to the best of our knowledge but only the checks listed in that report were actually run**.
> No deployment to any hosting provider has been performed. Where a step needs credentials or an account we do not have, it is listed under "Remaining manual steps".

## 1. The pieces

| Component | Image / command | Role | Scales |
|---|---|---|---|
| PostgreSQL 17 | `postgres:17-alpine` or a managed service | the only store of record, of pending work, and of the audit trail | one primary |
| `migrate` | `python -m alembic upgrade head` (one-shot) | schema changes, once per release, before the others start | run once |
| `api` | `uvicorn app.main:app` | HTTP API | horizontally (stateless; sessions are in PostgreSQL) |
| `worker` | `python -m app.worker` | OCR and extraction of uploaded documents; claims jobs with a lease | horizontally |
| `scheduler` | `python -m app.scheduler` | deadlines and alerts; claims work with a lease | one is enough; more are safe |
| `web` | nginx (`frontend/Dockerfile`) | serves the built web app and proxies `/api` so the browser sees one origin | horizontally |
| `clamav` | `clamav/clamav` | malware scanning (required in production) | one |
| a TLS terminator | your platform's router, Caddy, a cloud load balancer | HTTPS in front of `web` | — |

Private files live in a volume mounted at `/data` in `api` and `worker` (they must see the same files). **They are never served statically** — every download goes through an authorised endpoint.

## 2. Local, Docker (demonstration)

Requirements: Docker with Compose 2.24+.

```
copy .env.example .env             # then set SEED_PASSWORD (12+ characters) in it; leave the rest
docker compose up --build -d       # db, migrate, api, worker, scheduler, web
docker compose --profile demo run --rm seed      # demo users and DEMO ONLY rule sets (needs SEED_PASSWORD)
```

Open <http://localhost:8080>. Sign in as `<role>@demo.example.org` with the seed password (for example `regulation.bureau@demo.example.org`, `compliance.officer@demo.example.org`).
Health: <http://localhost:8080/api/health> (liveness) and <http://localhost:8080/api/ready> (database, schema revision, storage). Logs: `docker compose logs -f api scheduler worker`.

Without Docker, see the README (portable PostgreSQL or `docker compose up -d db`, then `uvicorn`, `python -m app.worker`, `python -m app.scheduler`, `npm run dev`).

## 3. Production

### 3.1 Settings that are enforced

With `ENVIRONMENT=production` the API **will not start** (and says why) if any of these holds: the database URL is SQLite or contains the development password; `SEED_PASSWORD` is set;
`ALLOWED_ORIGINS` is empty or contains an `http://` origin; `AICTE_INTEGRATION_MODE=mock`; `http` mode with a non-`https` URL or without the key and secret. Also forced by production: demo rules off, malware scanning required
(uploads are refused if no scanner is reachable), `Secure` cookies, HSTS, no API documentation endpoints.

### 3.2 Environment variables

| Variable | Purpose |
|---|---|
| `ENVIRONMENT` | `production` |
| `DATABASE_URL` or `DATABASE_URL_FILE` | `postgresql+psycopg://user:password@host:5432/dbname` (the file form for secrets) |
| `ALLOWED_ORIGINS` | the exact https origin(s) of the web app, e.g. `https://pace.example.org` (checked against `Origin` on every state-changing request) |
| `TRUSTED_PROXY_COUNT` | how many reverse proxies append to `X-Forwarded-For` (1 with the supplied nginx). **0 ignores the header**; setting it wrongly lets a client spoof its address in logs and rate limits |
| `STORAGE_DIR` | private attachment directory (a persistent, access-restricted volume) |
| `SCANNER=clamd`, `CLAMD_HOST`, `CLAMD_PORT`, `REQUIRE_MALWARE_SCAN` | malware scanning |
| `OCR_ENGINE`, `TESSERACT_CMD`, `TESSDATA_PREFIX`, `OCR_MIN_CONFIDENCE` | OCR |
| `SESSION_IDLE_MINUTES`, `SESSION_ABSOLUTE_HOURS`, `LOGIN_*` | session and sign-in throttling |
| `RATE_LIMIT_*`, `MAX_REQUEST_MB` | per-process rate limits and the request-size cap |
| `SCHEDULER_*`, `WORK_*`, `CLOCK_DUE_SOON_HOURS` | scheduler cadence, retry bounds, display threshold |
| `AICTE_*`, `SYNC_*` | the integration boundary (default `none`; see `docs/INTEGRATION.md`) |

Secrets can be supplied as files: `DATABASE_URL_FILE`, `AICTE_API_KEY_FILE`, `AICTE_HMAC_SECRET_FILE` (Docker/Kubernetes secrets). A value set directly takes precedence over a `_FILE`; an empty value counts as unset.
**No secret belongs in source control, an image layer, a compose file or a log.** `.env`, `secrets/*` and `backups/` are git-ignored.

### 3.3 With the supplied compose files

```
mkdir secrets
# create secrets/db_password, secrets/database_url, secrets/aicte_api_key, secrets/aicte_hmac_secret   (see secrets/README.md)
set SIH_PUBLIC_HOST=pace.example.org          # PowerShell: $env:SIH_PUBLIC_HOST="..."
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

This starts PostgreSQL (not published on the host), runs migrations, then `api`, `worker`, `scheduler`, `clamav` and `web` on port 8080 bound to `127.0.0.1`. **Put a TLS terminator in front of port 8080.** The first real user is created by
an operator; the demo seed is deliberately unavailable in production (`python -m app.set_password` and the administrator workflows are the supported path — see the README).
Then load the **real** rule configuration through the rule catalogue (authoring, review and publishing are separate people). Until a rule version is published and its sources are verified, applications cannot start for that category — by design.

### 3.4 Hosting guidance (honest)

| Environment | Suitable for | Limits |
|---|---|---|
| A free tier on a PaaS (Render, Railway, Fly.io, …) with a free PostgreSQL | a **demonstration** with DEMO ONLY data | free databases are often time-limited or deleted on inactivity; free instances sleep (the scheduler and worker need to run continuously); no private network, no ClamAV, ephemeral disks lose uploaded files; no backup guarantees. **Never put real institutional documents there.** |
| A single VM you control (the compose files) | a **pilot** with real data, if the operating items below are met | you own patching, backups, TLS, monitoring |
| Managed PostgreSQL + container platform + object storage | **production** | requires adapting `LocalStorage` to object storage (not built; see gap G-P8-3) |

Production requirements that a demo does not need: TLS everywhere; managed or well-run PostgreSQL with point-in-time recovery; encrypted, off-site, **restore-tested** backups of the database *and* the files;
a malware scanner; secrets management; log shipping and alerting; patching; an incident process; data-protection compliance review; an independent security assessment. None of these is claimed as done.

## 4. Release procedure

1. Back up (`ops/backup.ps1` / `ops/backup.sh`) and **verify** the backup restores (`ops/restore.ps1 -Verify`).
2. Build and tag images; run the test suites and `alembic check` against a copy.
3. Start `migrate` (one-shot). A failed migration leaves the previous schema (migrations run in a transaction on PostgreSQL); restore from the backup if needed.
4. Start `api`, `worker`, `scheduler`, then `web`. Check `/api/ready`.
5. Roll back by redeploying the previous images **only if** the migration is backward compatible; otherwise restore the backup. `alembic downgrade` exists for each revision, but downgrades of 0006 delete the SAC/DEC rows they cannot represent.

## 5. Remaining manual steps (not done here)

* Choose a host and create accounts; provision PostgreSQL, a volume or object storage, a TLS certificate and DNS.
* Create the secrets; set `SIH_PUBLIC_HOST`; set `TRUSTED_PROXY_COUNT` to match the real proxy chain.
* Load verified rule configuration; verify the December 2025 corrigendum and the counting conventions (see `docs/APH_VERIFICATION.md`, `docs/DECISIONS_AND_APPEALS.md`, `docs/COMPLIANCE_AND_DEADLINES.md`).
* Obtain the real AICTE interface (or decide there is none) and test it (`docs/INTEGRATION.md`).
* Run the CI workflow once; fix anything it finds on a clean machine.
* Commission an independent security review and a data-protection assessment.
