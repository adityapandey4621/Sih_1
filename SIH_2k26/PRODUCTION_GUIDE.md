# Dossier — Production & Team Deployment Guide

This guide gives you and your teammates everything needed to run the project locally or deploy it to a live production server.

---

## 1. Quickstart for Teammates (Local Development)

### Option A: Using Docker (Fastest, zero local dependencies)

Prerequisites: Docker Desktop installed.

```powershell
# 1. Clone the repository
git clone https://github.com/adityapandey4621/Sih_1.git
cd Sih_1

# 2. Configure environment
copy .env.example .env
# Set SEED_PASSWORD in .env (minimum 12 characters, e.g. DemoPassword123!)

# 3. Spin up all services (Database, Migrations, Backend API, Worker, Scheduler, Web SPA)
docker compose up --build -d

# 4. Seed demo users and rules
docker compose --profile demo run --rm seed

# 5. Open in browser:
# http://localhost:8080
```

---

### Option B: Local Development (Without Docker)

Prerequisites: Node 20+, Python 3.12+, PostgreSQL 15+ (or SQLite for lightweight testing).

#### Terminal 1 — Backend API
```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements-dev.txt

# Create your .env
copy ..\.env.example .env
# If using SQLite for quick testing, set: DATABASE_URL=sqlite:///./sih.db
# If using PostgreSQL, leave default: postgresql+psycopg://sih:sih_dev_password@localhost:5432/sih_aicte
# Set: SEED_PASSWORD=Password123456!

# Run migrations and seed
python -m alembic upgrade head
python -m app.seed
python -m app.seed_rules

# Start API server (port 8000)
python -m uvicorn app.main:app --reload --port 8000
```

#### Terminal 2 — Asynchronous Document Worker (OCR & Extraction)
```powershell
cd backend
.\.venv\Scripts\activate
python -m app.worker
```

#### Terminal 3 — SLA & Deadline Scheduler (Deadlines & In-App Alerts)
```powershell
cd backend
.\.venv\Scripts\activate
python -m app.scheduler
```

#### Terminal 4 — Frontend Client (Vite + React SPA)
```powershell
cd frontend
npm install
npm run dev
# Open http://localhost:5173
```

---

## 2. Production Deployment (Cloud Server / VPS)

### Architecture in Production:
- **Web**: Nginx container serving compiled React SPA + reverse-proxying `/api` with rate limiting and security headers.
- **API**: FastAPI application (Gunicorn / Uvicorn workers).
- **Worker**: Durable background process processing OCR & documents.
- **Scheduler**: Background process calculating deadline clocks and escalation alerts.
- **Database**: Managed PostgreSQL 15+ with point-in-time recovery.

### Step 1: Server Setup (Ubuntu 22.04 / 24.04 LTS)
```bash
# Update server packages
sudo apt update && sudo apt upgrade -y
sudo apt install -y docker.io docker-compose-v2 git curl

# Add user to docker group
sudo usermod -aG docker $USER
newgrp docker
```

### Step 2: Clone and Configure Secrets
```bash
git clone https://github.com/adityapandey4621/Sih_1.git /opt/sih
cd /opt/sih

# Create production secrets directory
mkdir -p secrets
echo "postgresql+psycopg://db_user:STRONG_PROD_PASSWORD@db_host:5432/sih_aicte" > secrets/database_url
chmod 600 secrets/*

# Configure production .env
cp .env.example .env
```

Edit `.env` for production:
```ini
ENVIRONMENT=production
ALLOWED_ORIGINS=https://your-domain.com
SIH_PUBLIC_HOST=your-domain.com
TRUSTED_PROXY_COUNT=1
REQUIRE_MALWARE_SCAN=false
AICTE_INTEGRATION_MODE=mock
```

### Step 3: Launch Production Stack
```bash
# Run production compose
docker compose -f docker-compose.prod.yml up --build -d

# Run database migrations
docker compose -f docker-compose.prod.yml exec api python -m alembic upgrade head

# Create your first platform administrator (prompts securely for password)
docker compose -f docker-compose.prod.yml exec api python -m app.create_admin --email admin@your-domain.com --name "System Administrator"
```

### Step 4: HTTPS / Reverse Proxy (Caddy Example)
Install Caddy for automatic free TLS:
```bash
sudo apt install -y debian-keyring debian-archive-keyring apt-transport-https
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
echo "deb [signed-by=/usr/share/keyrings/caddy-stable-archive-keyring.gpg] https://dl.cloudsmith.io/public/caddy/stable/deb/debian any-version main" | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt update && sudo apt install caddy -y
```

Add to `/etc/caddy/Caddyfile`:
```caddy
your-domain.com {
    reverse_proxy localhost:8080
}
```
Reload Caddy:
```bash
sudo systemctl reload caddy
```

---

## 3. Demo Accounts Cheat Sheet

All accounts are created by `python -m app.seed`.
Default password: value of `SEED_PASSWORD` in your `.env` (e.g. `Password123456!`).

| Role | Email | Responsibilities |
|---|---|---|
| **Institution User** | `institute.user@demo.example.org` | Creates applications, uploads documents, responds to notices, submits compliance. |
| **Scrutiny Coordinator** | `scrutiny.coordinator@demo.example.org` | Queue oversight, reviewer assignment, route determination. |
| **Scrutiny Officer** | `scrutiny.officer@demo.example.org` | Inspects OCR documents, records findings, prepares deficiency notices. |
| **Notice Issuer** | `notice.issuer@demo.example.org` | Formally issues deficiency notices to institutions. |
| **EVC Coordinator** | `evc.coordinator@demo.example.org` | Selection boundary, visit scheduling, conditional next-stage determination. |
| **EVC Member** | `evc.member@demo.example.org` | On-site/virtual observations, visit evidence, visit report submission. |
| **SHC Coordinator** | `shc.coordinator@demo.example.org` | Formulates compliance requirements, schedules hearings, completes stage. |
| **SHC Member** | `shc.member@demo.example.org` | Conducts hearing, records proceedings, drafts recommendations. |
| **Executive Committee** | `executive.committee@demo.example.org` | Records formal regulatory decisions (LoA, LoD, LoR) with reasons. |
| **Regulation Bureau** | `regulation.bureau@demo.example.org` | Records Bureau observations, issues official outcome, records obligations. |
| **Council Member** | `council.member@demo.example.org` | Records Council ratification and final decision on SAC appeals. |
| **SAC Coordinator** | `sac.coordinator@demo.example.org` | Arranges Standing Appellate Committee appeal reviews and hearings. |
| **SAC Member** | `sac.member@demo.example.org` | Reviews eligible appeals, conducts hearings, submits appeal recommendations. |
| **Compliance Officer** | `compliance.officer@demo.example.org` | Verifies post-approval evidence, confirms document validity dates. |
| **Rules Admin** | `rules.admin@demo.example.org` | Author of rule versions, checklists, and deadline policies. |
| **Rules Approver** | `rules.approver@demo.example.org` | Reviews diffs, publishes and retires rule sets. |
| **Auditor** | `auditor@demo.example.org` | Read-only oversight of the immutable audit trail and sync boundary. |
| **Platform Admin** | `platform.admin@demo.example.org` | Reviews institution access requests and provisions official accounts. |

---

## 4. Maintenance & Operations

- **Health Checks**:
  - `GET /api/health` — API process liveness.
  - `GET /api/ready` — Checks database connectivity, storage write capability, and migration status.
- **Database Backups**:
  ```bash
  # Take verified backup
  ./ops/backup.sh
  # Verify and restore
  ./ops/restore.sh
  ```
- **Sync Recovery**:
  If external AICTE synchronization encounters transient failures:
  ```bash
  python -m app.reconcile_decisions
  ```
- **Password Recovery**:
  Self-service email reset is intentionally disabled (no email provider). Reset an account directly via CLI:
  ```bash
  python -m app.set_password user@example.org
  ```
