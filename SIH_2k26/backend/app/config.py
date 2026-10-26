from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    environment: str = "development"  # development | test | production
    database_url: str = "postgresql+psycopg://sih:sih_dev_password@localhost:5432/sih_aicte"

    # Browser origins allowed to call state-changing endpoints (checked against the Origin header).
    allowed_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]

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

    # DEMO ONLY configuration may be published and selected only when this is true. Forced off in production.
    allow_demo_rules: bool = True

    # Dev-only: used by the seed script. Never used in production.
    seed_password: str = ""

    @field_validator("allowed_origins", mode="before")
    @classmethod
    def _split_origins(cls, v: object) -> object:
        if isinstance(v, str) and not v.strip().startswith("["):
            return [o.strip() for o in v.split(",") if o.strip()]
        return v

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
