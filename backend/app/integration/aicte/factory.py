"""Which adapter this process uses, decided by configuration alone. One instance per process, so the rate limiter and circuit breaker keep their state."""

from app.config import Settings, get_settings
from app.integration.aicte.adapter import AicteAdapter
from app.integration.aicte.http_adapter import HttpAicteAdapter
from app.integration.aicte.mock import MockAicteAdapter

_instance: AicteAdapter | None = None
_key: tuple | None = None
OVERRIDE: AicteAdapter | None = None  # tests install an adapter here; production code never sets it


def get_adapter(settings: Settings | None = None) -> AicteAdapter | None:
    """None means: no integration is configured, so nothing is sent and nothing is claimed."""
    global _instance, _key
    if OVERRIDE is not None:
        return OVERRIDE
    s = settings or get_settings()
    if s.aicte_integration_mode == "none":
        return None
    key = (s.aicte_integration_mode, s.aicte_base_url, bool(s.aicte_api_key), bool(s.aicte_hmac_secret))
    if _instance is None or key != _key:
        if s.aicte_integration_mode == "mock":
            if s.is_production:
                return None  # defence in depth: production_problems() already refuses to start with the mock
            _instance = MockAicteAdapter()
        elif s.aicte_integration_mode == "http":
            _instance = HttpAicteAdapter(s)
        else:
            return None
        _key = key
    return _instance


def reset() -> None:
    global _instance, _key
    _instance, _key = None, None
