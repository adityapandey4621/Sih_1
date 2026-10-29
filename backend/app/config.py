import os
from functools import lru_cache
from pathlib import Path

from typing import Annotated

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

# Settings that may be supplied as a file (Docker / Kubernetes secrets): NAME_FILE=/run/secrets/name is read into NAME. A value set directly wins.
SECRET_FILE_FIELDS = ("DATABASE_URL", "SEED_PASSWORD", "AICTE_API_KEY", "AICTE_HMAC_SECRET")
DEV_DB_PASSWORD = "sih_dev_password"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @model_validator(mode="before")
    @classmethod
    def _secret_files(cls, data: object) -> object:
        """NAME_FILE=/path reads the secret from a file (never printed, never logged). Ignored when NAME itself is set."""
        if not isinstance(data, dict):
            return data
        out = dict(data)
        for name in SECRET_FILE_FIELDS:
            path = os.environ.get(f"{name}_FILE")
            if path and not os.environ.get(name) and not out.get(name.lower()):  # an empty value counts as unset (how a compose override clears a development default)
                out[name.lower()] = Path(path).read_text(encoding="utf-8").strip()
        return out

    environment: str = "development"  # development | test | production
    database_url: str = "postgresql+psycopg://sih:sih_dev_password@localhost:5432/sih_aicte"

    # Browser origins allowed to call state-changing endpoints (checked against the Origin header).
    allowed_origins: Annotated[list[str], NoDecode] = ["http://localhost:5173", "http://127.0.0.1:5173"]

    session_cookie_name: str = "sih_session"
    csrf_cookie_name: str = "sih_csrf"
    session_idle_minutes: int = 60
    session_absolute_hours: int = 12

    invitation_valid_hours: int = 72

    login_max_failures: int = 5
    login_ip_max_failures: int = 30
    login_window_minutes: int = 15

    # Private attachment storage (never served statically). Keys are generated, never user-supplied.
    storage_dir: str = "storage"
    max_upload_mb: int = 10

    # ---- document processing (Phase 3) ----
    # OCR: "tesseract" (needs the executable) or "none". tesseract_cmd empty = look on PATH.
    ocr_engine: str = "tesseract"
    tesseract_cmd: str = ""
    tessdata_prefix: str = ""  # directory holding *.traineddata, if not the executable's default
    ocr_languages: str = "eng"
    ocr_dpi: int = 200
    ocr_timeout_seconds: int = 90
    ocr_min_confidence: float = 70.0  # below this a page / extracted value is treated as uncertain
    pdf_min_text_chars: int = 25  # a PDF page with fewer extractable characters is treated as scanned
    max_pdf_pages: int = 150
    max_image_pixels: int = 40_000_000
    max_page_text_chars: int = 100_000

    # Malware scanning: "none" or "clamd". Without a scanner every file is reported as UNSCANNED, never as clean.
    scanner: str = "none"
    clamd_host: str = "127.0.0.1"
    clamd_port: int = 3310
    clamd_timeout_seconds: int = 30
    # When true, files are held (not processed) until a scanner is configured. Forced on in production.
    require_malware_scan: bool = False

    # Durable job queue
    job_max_attempts: int = 3
    job_lease_seconds: int = 300
    job_retry_backoff_seconds: int = 30
    max_manual_retries: int = 3
    worker_poll_seconds: float = 2.0

    # Deadline scheduler (Phase 7). Pending alert work lives in PostgreSQL; nothing depends on process memory.
    scheduler_poll_seconds: float = 5.0
    scheduler_sync_seconds: float = 60.0  # how often the scheduler re-derives clocks from persisted state
    work_max_attempts: int = 5
    work_lease_seconds: int = 120
    work_retry_backoff_seconds: int = 30
    clock_due_soon_hours: int = 72  # display only: how close to its due date a clock is shown as "due soon"

    # ---- HTTP hardening (Phase 8) ----
    # How many reverse proxies sit in front of the API and append to X-Forwarded-For. 0 = none: the header is ignored (it is client-controllable).
    trusted_proxy_count: int = 0
    max_request_mb: int = 64  # hard cap on any request body (a compliance submission may carry several files of max_upload_mb)
    rate_limit_enabled: bool = True
    rate_limit_read_per_minute: int = 600  # per client (session if signed in, otherwise IP); a shared reverse proxy should add its own limit (docs/DEPLOYMENT.md)
    rate_limit_write_per_minute: int = 120
    rate_limit_auth_per_minute: int = 30  # sign-in and invitation endpoints, per IP

    # ---- AICTE integration boundary (Phase 8). No AICTE API, credential or webhook is known to exist: the default is OFF. ----
    # none = nothing is sent (rows are NOT_CONFIGURED); mock = in-process DEMO ONLY adapter (refused in production); http = our own published contract
    # (backend/app/integration/aicte/contract.py) spoken to AICTE_BASE_URL. "http" is UNVERIFIED until an authorised endpoint has actually been tested.
    aicte_integration_mode: str = "none"
    aicte_base_url: str = ""
    aicte_api_key: str = ""  # a secret: supply AICTE_API_KEY_FILE in production
    aicte_hmac_secret: str = ""  # a secret: signs request bodies so the receiver can verify them
    aicte_timeout_seconds: float = 10.0
    aicte_connect_timeout_seconds: float = 3.0
    aicte_http_retries: int = 2  # extra tries inside one attempt, on connection errors and 502/503/504 only (every request carries an Idempotency-Key)
    aicte_rate_limit_per_minute: int = 60
    aicte_circuit_failures: int = 5  # consecutive failures that open the circuit
    aicte_circuit_cooldown_seconds: int = 60
    sync_max_attempts: int = 8  # then FAILED (needs an operator); never retried forever
    sync_retry_backoff_seconds: int = 60

    # DEMO ONLY configuration may be published and selected only when this is true. Forced off in production.
    allow_demo_rules: bool = True

    # Dev-only: used by the seed script. Never used in production.
    seed_password: str = ""

    @field_validator("allowed_origins", mode="before")
    @classmethod
    def _split_origins(cls, v: object) -> object:
        """Accepts a comma-separated list (what .env.example, docker-compose and most platforms use) or a JSON list. NoDecode on the field stops pydantic-settings
        from trying to JSON-parse the raw value first, which made a plain `ALLOWED_ORIGINS=http://localhost:8080` crash the process at start-up."""
        if isinstance(v, str):
            s = v.strip()
            if s.startswith("["):
                import json

                return json.loads(s)
            return [o.strip() for o in s.split(",") if o.strip()]
        return v

    def production_problems(self) -> list[str]:
        """Settings that must not reach production. create_app() refuses to start when this is non-empty (fail closed)."""
        p: list[str] = []
        if DEV_DB_PASSWORD in self.database_url or self.database_url.startswith("sqlite"):
            p.append("DATABASE_URL uses the development database or password. Use PostgreSQL with a real secret (DATABASE_URL_FILE).")
        if self.seed_password:
            p.append("SEED_PASSWORD is set. The demo seed must never be configured in production.")
        if any(o.startswith("http://") for o in self.allowed_origins) or not self.allowed_origins:
            p.append("ALLOWED_ORIGINS must list the real https origin(s) of the web app (no http:// origin, not empty).")
        if self.aicte_integration_mode == "mock":
            p.append("AICTE_INTEGRATION_MODE=mock is DEMO ONLY and is refused in production.")
        if self.aicte_integration_mode == "http":
            if not self.aicte_base_url.startswith("https://"):
                p.append("AICTE_BASE_URL must be an https:// URL.")
            if not self.aicte_api_key or not self.aicte_hmac_secret:
                p.append("AICTE_API_KEY and AICTE_HMAC_SECRET are required for AICTE_INTEGRATION_MODE=http (supply them as *_FILE secrets).")
        if self.aicte_integration_mode not in ("none", "mock", "http"):
            p.append("AICTE_INTEGRATION_MODE must be none, mock or http.")
        return p

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def demo_rules_enabled(self) -> bool:
        return self.allow_demo_rules and not self.is_production

    @property
    def malware_scan_required(self) -> bool:
        return self.require_malware_scan or self.is_production

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024


@lru_cache
def get_settings() -> Settings:
    return Settings()
