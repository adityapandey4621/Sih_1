"""A DEMO ONLY receiver for the contract in contract.py - what an AICTE-side endpoint would have to do for the HTTP adapter to work.

    DEMO_API_KEY=... DEMO_HMAC_SECRET=... python -m app.integration.aicte.demo_server --port 8099

It exists so the HTTP adapter's behaviour (signing, idempotency, timeouts, retries, garbled answers) can be exercised over a real socket. It is NOT AICTE, it
does not represent AICTE, and a green run against it says nothing about any AICTE system. It holds messages in memory only.

Faults can be requested per call with the header `X-Demo-Fault`: 503 | 500 | 429 | slow | garbage | reject | lie (a receipt with the wrong hash) | lose (stores, then returns 503).
"""

import argparse
import hashlib
import hmac
import os
import time
import uuid

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from app.integration.aicte.contract import Envelope, Receipt, StatusResponse


def create_demo_app(api_key: str, hmac_secret: str) -> FastAPI:
    app = FastAPI(title="DEMO ONLY — contract receiver", docs_url=None, redoc_url=None, openapi_url=None)
    store: dict[uuid.UUID, tuple[str, str]] = {}
    app.state.store = store
    app.state.calls = 0

    def check(request: Request, body: bytes, authorization: str | None, timestamp: str | None, signature: str | None) -> None:
        if authorization != f"Bearer {api_key}":
            raise HTTPException(401, "bad credentials")
        try:
            skew = abs(time.time() - int(timestamp or "0"))
        except ValueError:
            skew = 1e9
        if skew > 300:
            raise HTTPException(401, "stale timestamp")
        expected = hmac.new(hmac_secret.encode(), (timestamp or "").encode() + b"." + body, hashlib.sha256).hexdigest()
        if not signature or not hmac.compare_digest(expected, signature):
            raise HTTPException(401, "bad signature")

    @app.post("/v1/outcomes")
    async def outcomes(request: Request, authorization: str | None = Header(None), x_timestamp: str | None = Header(None), x_signature: str | None = Header(None), x_demo_fault: str | None = Header(None)) -> Response:
        app.state.calls += 1
        body = await request.body()
        check(request, body, authorization, x_timestamp, x_signature)
        if x_demo_fault in ("503", "500", "429"):
            return JSONResponse({"detail": "demo fault"}, status_code=int(x_demo_fault), headers={"Retry-After": "0"} if x_demo_fault == "429" else {})
        if x_demo_fault == "slow":
            time.sleep(2.5)
        env = Envelope.model_validate_json(body)  # a body that is not exactly the contract is a 422, not a guess
        existing = store.get(env.message_id)
        ref = existing[1] if existing else f"DEMO-{env.message_id.hex[:10].upper()}"
        if x_demo_fault == "reject":
            return JSONResponse(Receipt(status="REJECTED", message_id=env.message_id, payload_hash=env.payload_hash, reason="demo rejection").model_dump(mode="json"))
        if not existing:
            store[env.message_id] = (env.payload_hash, ref)
        if x_demo_fault == "lose":
            return JSONResponse({"detail": "stored, but this answer is a fault"}, status_code=503)
        if x_demo_fault == "garbage":
            return Response("<html>ok</html>", media_type="text/html")
        digest = "f" * 64 if x_demo_fault == "lie" else env.payload_hash
        return JSONResponse(Receipt(status="DUPLICATE" if existing else "RECEIVED", message_id=env.message_id, payload_hash=digest, external_ref=ref).model_dump(mode="json"))

    @app.get("/v1/messages/{message_id}")
    async def message(message_id: uuid.UUID, request: Request, authorization: str | None = Header(None), x_timestamp: str | None = Header(None), x_signature: str | None = Header(None)) -> Response:
        check(request, b"", authorization, x_timestamp, x_signature)
        held = store.get(message_id)
        if held is None:
            raise HTTPException(404, "unknown message")
        return JSONResponse(StatusResponse(message_id=message_id, status="RECEIVED", external_ref=held[1], payload_hash=held[0]).model_dump(mode="json"))

    return app


def main() -> int:  # pragma: no cover - a development convenience
    import uvicorn

    parser = argparse.ArgumentParser(description="DEMO ONLY receiver for the project's AICTE contract")
    parser.add_argument("--port", type=int, default=8099)
    args = parser.parse_args()
    key, secret = os.environ.get("DEMO_API_KEY", ""), os.environ.get("DEMO_HMAC_SECRET", "")
    if not key or not secret:
        print("Set DEMO_API_KEY and DEMO_HMAC_SECRET (any throwaway values) for the demo receiver.")
        return 1
    uvicorn.run(create_demo_app(key, secret), host="127.0.0.1", port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
