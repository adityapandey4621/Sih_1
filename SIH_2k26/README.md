# PACE — Platform for AICTE Compliance and Evaluation

> **Smart India Hackathon 2026 — Problem Statement 26262**  
> *Automation of AICTE Institutional Approval Process, Document Scrutiny, Committee Reviews, Deficiencies, and Post-Approval Compliance.*

---

## 🏛️ Executive Summary

**PACE** is a comprehensive, production-grade regulatory workflow automation platform engineered specifically for the All India Council for Technical Education (AICTE) institutional approval process.

It replaces opaque manual processing with an **end-to-end transparent, role-gated, and auditable system** spanning the complete lifecycle of technical education institutions: from initial application intake and automated OCR evidence extraction, to multi-tier committee evaluations (EVC/SHC), formal Executive decisions, time-bound appeals (SAC), and ongoing post-approval compliance monitoring.

### Core Design Philosophy
> **"The software routes work, checks evidence, keeps the clock, and preserves the record — but authorised officials make every regulatory decision."**  
> No algorithm auto-approves or auto-rejects an institution. The system guarantees complete auditability, role segregation, and tamper-evident decision logging.

---

## 🚀 Key Features

| Capability | What PACE Delivers |
|---|---|
| **Intelligent Application Intake** | Category-sensitive dynamic forms (New Technical Institution, Extension of Approval, Course Additions) based on the AICTE Approval Process Handbook (APH). |
| **Automated OCR & Document Verification** | Embedded Tesseract OCR engine extracts critical evidence fields (land area, building completion dates, trust registration IDs) with confidence scoring and side-by-side document inspection. |
| **Granular Scrutiny & Deficiency Workflow** | Scrutiny Officers record itemized findings against specific document pages; Coordinators verify; Notice Issuers transmit formal deficiency notices; Institutions submit targeted responses with versioned evidence. |
| **Committee Review Workflows (EVC & SHC)** | **Expert Visit Committee (EVC)** workspace for scheduling visits, physical/virtual mode selection, and site observation reports. **Standing Hearing Committee (SHC)** for compliance hearings and recommendations. |
| **Executive Decisions & Council Steps** | Authorised Executive Committee issuance of Letter of Approval (LoA), Letter of Deficiency (LoD), or Letter of Rejection (LoR) with formal reasons and Council ratification. |
| **Standing Appellate Committee (SAC)** | Time-bound appeal workflow with statutory deadline clocks, hearing proceedings, and Council appellate determination. |
| **Post-Approval Compliance & Clocks Engine** | Persistent background engine continuously tracking annual compliance obligations, document validity expirations (Fire Safety, Land Use, Building Safety), and SLA escalation warnings. |
| **Tamper-Evident Immutable Audit Log** | Append-only audit trail logging every actor, action, timestamp, and regulatory transition with complete traceability. |

---

## ⚡ Quickstart for Teammates & Evaluators (Docker)

The fastest and cleanest way to run the entire stack on any computer (Windows, macOS, Linux). Zero manual dependency installations required.

### Prerequisites
* [Docker Desktop](https://www.docker.com/products/docker-desktop/) installed and running.

### 3-Step Setup

```bash
# 1. Clone the repository
git clone https://github.com/adityapandey4621/Sih_1.git
cd Sih_1

# 2. Build and launch all services (Database, Migrations, Backend API, Worker, Scheduler, Web SPA)
docker compose up --build -d

# 3. Seed demo accounts and AICTE demo rule catalogue
docker compose --profile demo run --rm seed
```

**Open in your browser:**  
👉 **`http://localhost:8080`**

---

## 💻 Local Development Setup (Without Docker)

### Prerequisites
* **Node.js**: v20 or v22
* **Python**: v3.12 or v3.13
* **PostgreSQL 15+** or lightweight local SQLite for quick testing

### 1. Backend Setup

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\activate

# Install dependencies
pip install -r requirements-dev.txt

# Create local environment config
copy ..\.env.example .env

# Run database migrations & seed demo data
python -m alembic upgrade head
python -m app.seed
python -m app.seed_rules
```

Start the backend services (in 3 separate terminals):
```powershell
# Terminal 1: FastAPI REST API
python -m uvicorn app.main:app --reload --port 8000

# Terminal 2: Document Processing & OCR Worker
python -m app.worker

# Terminal 3: Deadline & SLA Scheduler
python -m app.scheduler
```

### 2. Frontend Setup

```powershell
# In a new terminal:
cd frontend
npm install
npm run dev
```

**Open in your browser:**  
👉 **`http://localhost:5173`**

---

## 🔑 Demo Accounts Cheat Sheet

All accounts are automatically seeded with password: **`Password123456!`**

| Role / Persona | Demo Login Email | Primary Responsibilities & What to Test |
|---|---|---|
| **Institution User** | `institute.user@demo.example.org` | Creates drafts, uploads documents, responds to deficiency notices, files SAC appeals, submits compliance renewals. |
| **Scrutiny Coordinator** | `scrutiny.coordinator@demo.example.org` | Manages scrutiny queue, assigns reviewers, determines case routes. |
| **Scrutiny Officer** | `scrutiny.officer@demo.example.org` | Inspects OCR extracted evidence beside uploaded documents, records findings, prepares deficiency notices. |
| **Notice Issuer** | `notice.issuer@demo.example.org` | Formally transmits deficiency notices to institutions with confirmed requirements. |
| **EVC Coordinator** | `evc.coordinator@demo.example.org` | Selects expert visit committees, schedules on-site or virtual visits, records route determinations. |
| **EVC Member** | `evc.member@demo.example.org` | Records physical/virtual site observations, uploads inspection evidence, submits visit report. |
| **SHC Coordinator** | `shc.coordinator@demo.example.org` | Formulates compliance requirements from prior shortcomings, schedules hearings. |
| **SHC Member** | `shc.member@demo.example.org` | Conducts hearing proceedings, records findings, drafts final committee recommendations. |
| **Executive Committee** | `executive.committee@demo.example.org` | Issues formal regulatory decisions: Letter of Approval (LoA), Deficiency (LoD), or Rejection (LoR). |
| **Regulation Bureau** | `regulation.bureau@demo.example.org` | Enforces regulatory observations, issues final outcome, binds post-approval obligations. |
| **Council Member** | `council.member@demo.example.org` | Records Council ratification on Executive Committee decisions and SAC appeals. |
| **SAC Coordinator** | `sac.coordinator@demo.example.org` | Coordinates Standing Appellate Committee appeal hearings. |
| **SAC Member** | `sac.member@demo.example.org` | Evaluates institution appeals, assesses fresh evidence, submits appellate recommendations. |
| **Compliance Officer** | `compliance.officer@demo.example.org` | Verifies post-approval compliance submissions, tracks validity of safety certificates. |
| **Rules Admin** | `rules.admin@demo.example.org` | Authors versioned regulatory checklists and statutory deadline policies. |
| **Rules Approver** | `rules.approver@demo.example.org` | Reviews rule diffs against official APH clauses, publishes and retires rule sets. |
| **Auditor** | `auditor@demo.example.org` | Read-only oversight of the immutable audit trail and external sync status. |
| **Platform Admin** | `platform.admin@demo.example.org` | Reviews access requests, provisions staff accounts. |

---

## 🛠️ Technology Stack

```
PACE Architecture:
├── Frontend:    React 18, TypeScript, Vite, React Router 6, Lucide Icons, Vanilla CSS Design System
├── Backend:     FastAPI, SQLAlchemy 2.0 (async/sync), Pydantic v2, Alembic
├── Engine:      Tesseract OCR, PyPDF2, pdf2image (Document processing & Extraction)
├── Database:    PostgreSQL 17 (Enterprise Dialect) / SQLite (Fast Local Testing)
├── Security:    Double-Submit CSRF, HTTP-only SameSite Cookies, Role-Based Access Control (18 Roles)
├── Background:  Asynchronous Job Queue (Document Worker) + SLA Engine (Deadline Scheduler)
└── Deployment:  Docker, Docker Compose, Nginx Reverse Proxy, Caddy SSL
```

---

## 🧪 Testing & Verification

Both the frontend and backend suites are thoroughly verified:

```powershell
# Backend Test Suite (FastAPI + SQLAlchemy + Hardening)
cd backend
python -m pytest

# Frontend Test Suite (Vitest + React Testing Library - 188 Unit & Integration Tests)
cd frontend
npm test -- --run

# TypeScript Typecheck & Production Bundle Build
npm run typecheck
npm run build
```

---

## 📚 Technical Documentation Index

Detailed architectural and regulatory documentation is maintained under the [`docs/`](file:///c:/Users/awadh/OneDrive/Desktop/Claude-Code/SIH_2k26/docs/) directory:

* [`docs/ARCHITECTURE.md`](file:///c:/Users/awadh/OneDrive/Desktop/Claude-Code/SIH_2k26/docs/ARCHITECTURE.md) — System boundaries, data flow diagrams, and component interactions.
* [`docs/PRODUCTION_GUIDE.md`](file:///c:/Users/awadh/OneDrive/Desktop/Claude-Code/SIH_2k26/PRODUCTION_GUIDE.md) — Production server deployment guide (VPS, Docker, Cloud, Secrets, SSL).
* [`docs/SCRUTINY.md`](file:///c:/Users/awadh/OneDrive/Desktop/Claude-Code/SIH_2k26/docs/SCRUTINY.md) — Scrutiny workflow, findings, deficiency notices, and re-scrutiny rules.
* [`docs/COMMITTEE.md`](file:///c:/Users/awadh/OneDrive/Desktop/Claude-Code/SIH_2k26/docs/COMMITTEE.md) — Detailed specifications for EVC (Expert Visit) and SHC (Standing Hearing) committees.
* [`docs/DECISIONS_AND_APPEALS.md`](file:///c:/Users/awadh/OneDrive/Desktop/Claude-Code/SIH_2k26/docs/DECISIONS_AND_APPEALS.md) — Executive Committee outcomes, Council steps, and SAC appeals.
* [`docs/COMPLIANCE_AND_DEADLINES.md`](file:///c:/Users/awadh/OneDrive/Desktop/Claude-Code/SIH_2k26/docs/COMPLIANCE_AND_DEADLINES.md) — Post-approval obligations, document validity expirations, and SLA scheduler.
* [`docs/DOCUMENT_PROCESSING.md`](file:///c:/Users/awadh/OneDrive/Desktop/Claude-Code/SIH_2k26/docs/DOCUMENT_PROCESSING.md) — OCR extraction architecture, confidence thresholds, and malware scanning.
* [`docs/SECURITY_REVIEW.md`](file:///c:/Users/awadh/OneDrive/Desktop/Claude-Code/SIH_2k26/docs/SECURITY_REVIEW.md) — Threat model, CSRF protection, input sanitation, and RBAC matrix.

---

## ⚖️ Regulatory Integrity & Transparency Notice

PACE is an independent software solution developed for Smart India Hackathon 2026 (Problem Statement 26262). 
* It is **not** an official AICTE website or service and does not claim official affiliation.
* Regulatory authority remains strictly with designated human officers and statutory committees.
* Initial rule configurations are seeded as **DEMO ONLY** until verified against official AICTE Approval Process Handbook gazette notifications.
