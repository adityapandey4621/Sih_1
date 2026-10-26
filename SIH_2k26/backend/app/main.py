from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.guards import ImmutableRecordError
from app.routers import access_requests, admin, applications, auth, catalogue, committee, documents, notices, rules, scrutiny
from app.services.errors import ServiceError


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="AICTE Approval Workflow — API",
        version="0.2.0",
        # API docs are a development convenience only.
        docs_url=None if settings.is_production else "/api/docs",
        redoc_url=None,
        openapi_url=None if settings.is_production else "/api/openapi.json",
    )
    for r in (auth.router, access_requests.router, admin.router, catalogue.router, applications.router, documents.router, rules.router, scrutiny.router, notices.router, committee.evc, committee.shc, committee.inst, committee.handoffs):
        app.include_router(r)

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
        return {"status": "ok"}

    return app


app = create_app()
