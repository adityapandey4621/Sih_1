"""HTTP-level hardening for the API (Phase 8): request ids and an access log, security headers, a request-size cap and a rate limiter.

Pure ASGI (no BaseHTTPMiddleware), so uploads stream and nothing is buffered. What it does NOT do, honestly:
  * the rate limiter is in-process memory: with several API processes each keeps its own counters, so a shared reverse proxy must add its own limit
    (docs/DEPLOYMENT.md has the nginx configuration). It stops a single client hammering one process; it is not a distributed limiter.
  * the access log records method, path (never the query string), status, duration and the request id. No bodies, no headers, no cookies.
"""

import hashlib
import json
import logging
import re
import time
import traceback
import uuid
from collections import deque

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.config import Settings
from app.netutil import resolve_client_ip

log = logging.getLogger("app.access")

_REQ_ID = re.compile(r"^[A-Za-z0-9._-]{8,64}$")
_SAFE = {"GET", "HEAD", "OPTIONS"}
_AUTH_PREFIXES = ("/api/auth/login", "/api/auth/invitations", "/api/access-requests", "/api/auth/password-reset")
_EXEMPT = ("/api/health", "/api/ready")
_DOCS = ("/api/docs", "/api/openapi.json")

SECURITY_HEADERS = [
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"no-referrer"),
    (b"cross-origin-opener-policy", b"same-origin"),
    (b"cross-origin-resource-policy", b"same-origin"),
    (b"permissions-policy", b"camera=(), microphone=(), geolocation=(), payment=(), usb=()"),
]
API_CSP = b"default-src 'none'; frame-ancestors 'none'; base-uri 'none'"
HSTS = b"max-age=31536000; includeSubDomains"


def _frames(exc: BaseException, limit: int = 4) -> str:
    """Where it failed (file:line:function), never what it said: database errors can carry parameters, and exception text can carry user input."""
    tb = traceback.extract_tb(exc.__traceback__)[-limit:]
    return " <- ".join(f"{f.filename.replace(chr(92), '/').rsplit('/app/', 1)[-1]}:{f.lineno}:{f.name}" for f in reversed(tb)) or "unknown"


class SlidingWindow:
    """Counts hits per key over the last 60 seconds. Memory is bounded: idle keys are dropped as the table grows."""

    def __init__(self) -> None:
        self.hits: dict[str, deque[float]] = {}
        self.last_sweep = time.monotonic()

    def allow(self, key: str, limit: int, now: float | None = None) -> tuple[bool, int]:
        now = time.monotonic() if now is None else now
        q = self.hits.setdefault(key, deque())
        while q and now - q[0] >= 60:
            q.popleft()
        if len(q) >= limit:
            return False, max(1, int(60 - (now - q[0])) + 1)
        q.append(now)
        if now - self.last_sweep > 120 or len(self.hits) > 20000:
            self.last_sweep = now
            for k in [k for k, v in self.hits.items() if not v or now - v[-1] >= 60]:
                self.hits.pop(k, None)
        return True, 0


def _json(status: int, body: dict, extra: list[tuple[bytes, bytes]] | None = None) -> tuple[Message, Message]:
    payload = json.dumps(body).encode()
    headers = [(b"content-type", b"application/json"), (b"content-length", str(len(payload)).encode()), (b"cache-control", b"no-store"), *(extra or [])]
    return {"type": "http.response.start", "status": status, "headers": headers}, {"type": "http.response.body", "body": payload}


class HardeningMiddleware:
    def __init__(self, app: ASGIApp, settings: Settings) -> None:
        self.app = app
        self.s = settings
        self.window = SlidingWindow()
        self.max_bytes = settings.max_request_mb * 1024 * 1024

    def _client(self, scope: Scope, headers: dict[bytes, bytes]) -> str:
        peer = scope["client"][0] if scope.get("client") else None
        xff = headers.get(b"x-forwarded-for", b"").decode("latin-1") or None
        return resolve_client_ip(peer, xff, self.s.trusted_proxy_count) or "unknown"

    def _bucket(self, method: str, path: str, headers: dict[bytes, bytes], ip: str) -> tuple[str, int]:
        if path.startswith(_AUTH_PREFIXES):
            return f"auth:{ip}", self.s.rate_limit_auth_per_minute
        cookie = headers.get(b"cookie", b"").decode("latin-1")
        m = re.search(rf"(?:^|;\s*){re.escape(self.s.session_cookie_name)}=([^;]+)", cookie)
        who = "s:" + hashlib.sha256(m.group(1).encode()).hexdigest()[:20] if m else "ip:" + ip
        if method in _SAFE:
            return f"r:{who}", self.s.rate_limit_read_per_minute
        return f"w:{who}", self.s.rate_limit_write_per_minute

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = time.perf_counter()
        headers = {k.lower(): v for k, v in scope["headers"]}
        raw = headers.get(b"x-request-id", b"").decode("latin-1")
        rid = raw if _REQ_ID.match(raw) else uuid.uuid4().hex[:16]
        scope.setdefault("state", {})["request_id"] = rid
        method, path = scope["method"], scope["path"]
        status_holder = {"status": 0}
        production = self.s.is_production

        big = {"v": False, "sent": False}

        async def emit(message: Message) -> None:
            if message["type"] == "http.response.start":
                status_holder["status"] = message["status"]
                existing = {k.lower() for k, _ in message["headers"]}
                extra = [(b"x-request-id", rid.encode())]
                if not path.startswith(_DOCS):
                    extra += SECURITY_HEADERS + [(b"content-security-policy", API_CSP)]
                if production:
                    extra.append((b"strict-transport-security", HSTS))
                if path.startswith("/api/") and b"cache-control" not in existing:
                    extra.append((b"cache-control", b"no-store"))
                message = {**message, "headers": [*message["headers"], *[h for h in extra if h[0] not in existing]]}
            await send(message)

        async def send_413() -> None:
            if not big["sent"]:
                big["sent"] = True
                start, body = _json(413, {"detail": "The request is larger than the server accepts.", "code": "request_too_large"})
                await emit(start)
                await emit(body)

        async def send_wrapped(message: Message) -> None:
            if big["v"]:  # the body outgrew the cap while the application was reading it: whatever it now tries to answer (FastAPI would say 400 "error parsing the body") becomes one clear 413
                await send_413()
                return
            await emit(message)

        def finish() -> None:
            log.info("%s %s %s %d %dms", rid, method, path, status_holder["status"], int((time.perf_counter() - started) * 1000))

        # 1. size cap (declared length first; streamed bodies are counted as they arrive)
        declared = headers.get(b"content-length")
        if declared and declared.isdigit() and int(declared) > self.max_bytes:
            start, body = _json(413, {"detail": "The request is larger than the server accepts.", "code": "request_too_large"})
            await send_wrapped(start)
            await send_wrapped(body)
            finish()
            return

        # 2. rate limit (not for health probes)
        if self.s.rate_limit_enabled and not path.startswith(_EXEMPT) and path.startswith("/api/"):
            key, limit = self._bucket(method, path, headers, self._client(scope, headers))
            ok, retry = self.window.allow(key, limit)
            if not ok:
                start, body = _json(429, {"detail": "Too many requests. Please slow down and try again shortly.", "code": "rate_limited"}, [(b"retry-after", str(retry).encode())])
                await send_wrapped(start)
                await send_wrapped(body)
                finish()
                return

        seen = {"n": 0}

        async def receive_counted() -> Message:
            message = await receive()
            if message["type"] == "http.request":
                seen["n"] += len(message.get("body", b""))
                if seen["n"] > self.max_bytes:
                    big["v"] = True
                    return {"type": "http.disconnect"}  # stop feeding the application
            return message

        try:
            await self.app(scope, receive_counted, send_wrapped)
        except Exception as exc:  # noqa: BLE001 - the last line of defence: the caller gets a generic answer and an id, never a trace or a database message
            if big["v"]:
                return  # an expected consequence of the cap; the finally block answers 413
            log.error("%s unhandled %s at %s", rid, type(exc).__name__, _frames(exc))
            if status_holder["status"] == 0:
                start, body = _json(500, {"detail": "Something went wrong on our side. Nothing you sent was lost or half-saved by this error; please try again, and quote the request id if it persists.", "code": "internal_error", "request_id": rid})
                await send_wrapped(start)
                await send_wrapped(body)
            else:
                raise
        finally:
            if big["v"]:
                await send_413()
            finish()
