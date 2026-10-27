import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.guards import ImmutableRecordError
from app.http_hardening import HardeningMiddleware
from app.readiness import readiness
from app.routers import access_requests, admin, applications, auth, catalogue, committee, compliance, decisions, documents, integration, notices, rules, scrutiny
from app.services.errors import ServiceError


def _configure_logging() -> None:
    """Uvicorn configures only its own loggers. Without this, the application's INFO lines (access log, scheduler, worker) are dropped and only warnings reach stderr."""
    log = logging.getLogger("app")
    log.setLevel(logging.INFO)
    if not any(getattr(h, "_sih", False) for h in log.handlers):
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
        handler._sih = True  # type: ignore[attr-defined]
        log.addHandler(handler)


_configure_logging()


def create_app() -> FastAPI:
    settings = get_settings()
    if settings.is_production:
        problems = settings.production_problems()
        if problems:  # fail closed: a production start with development settings is a mistake, not a configuration
            raise RuntimeError("Refusing to start in production:\n - " + "\n - ".join(problems))
    app = FastAPI(
        title="AICTE Approval Workflow — API",
        version="0.2.0",
        # API docs are a development convenience only.
        docs_url=None if settings.is_production else "/api/docs",
        redoc_url=None,
        openapi_url=None if settings.is_production else "/api/openapi.json",
    )
    for r in (auth.router, access_requests.router, admin.router, catalogue.router, applications.router, documents.router, rules.router, scrutiny.router, notices.router, committee.evc, committee.shc, committee.inst, committee.handoffs, committee.sac, decisions.router, decisions.inst, compliance.router, integration.router):
        app.include_router(r)

    app.add_middleware(HardeningMiddleware, settings=settings)

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        # FastAPI's default answer echoes the submitted value back ("input"), which can be a password or a document text. Keep where and why, drop what.
        items = [{"loc": list(e.get("loc", [])), "msg": e.get("msg", "Invalid value."), "type": e.get("type", "invalid")} for e in exc.errors()]
        return JSONResponse({"detail": items}, status_code=422)

    @app.exception_handler(ServiceError)
    async def service_error(_: Request, exc: ServiceError) -> JSONResponse:
        body = {"detail": exc.message, "code": exc.code}
        if exc.issues:
            body["issues"] = [{"code": i.code, "message": i.message, "path": i.path} for i in exc.issues]
        return JSONResponse(body, status_code=exc.status)

    @app.exception_handler(ImmutableRecordError)
    async def immutable(_: Request, exc: ImmutableRecordError) -> JSONResponse:
        return JSONResponse({"detail": str(exc), "code": "immutable_record"}, status_code=409)

    @app.get("/api/health", tags=["meta"])
    def health() -> dict[str, str]:
        """Liveness: the process answers. Says nothing about the database; use /api/ready for that."""
        return {"status": "ok"}

    @app.get("/api/ready", tags=["meta"])
    def ready() -> JSONResponse:
        """Readiness: the database answers, the schema is at the revision this code expects, the private storage is writable. Booleans only; no detail leaks."""
        result = readiness()
        return JSONResponse(result, status_code=200 if result["status"] == "ready" else 503)

    return app


app = create_app()
