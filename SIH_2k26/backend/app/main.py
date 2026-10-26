from fastapi import FastAPI

from app.config import get_settings
from app.routers import access_requests, admin, auth


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="AICTE Approval Workflow — API",
        version="0.1.0",
        # API docs are a development convenience only.
        docs_url=None if settings.is_production else "/api/docs",
        redoc_url=None,
        openapi_url=None if settings.is_production else "/api/openapi.json",
    )
    app.include_router(auth.router)
    app.include_router(access_requests.router)
    app.include_router(admin.router)

    @app.get("/api/health", tags=["meta"])
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
