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
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024


@lru_cache
def get_settings() -> Settings:
    return Settings()
