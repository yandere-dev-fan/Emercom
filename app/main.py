import logging

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from app.api.session_routes import router as session_api_router
from app.api.template_routes import router as template_api_router
from app.api.routes import router as api_router
from app.config import get_settings
from app.db.session import SessionLocal, init_db
from app.observability import install_observability, log_event
from app.runtime.manager import RuntimeManager
from app.security.rate_limit import InMemoryJoinKeyLimiter
from app.web.host_routes import router as host_router
from app.web.routes import router as web_router
from app.ws.manager import ConnectionManager
from app.ws.routes import router as ws_router


settings = get_settings()


def create_app() -> FastAPI:
    app = FastAPI(title=settings.app_name)
    install_observability(app)
    app.state.ws_manager = ConnectionManager()
    app.state.runtime_manager = RuntimeManager(app.state.ws_manager)
    app.state.join_key_limiter = InMemoryJoinKeyLimiter(settings)
    app.mount("/static", StaticFiles(directory="app/static"), name="static")
    app.include_router(host_router)
    app.include_router(web_router)
    app.include_router(api_router)
    app.include_router(template_api_router)
    app.include_router(session_api_router)
    app.include_router(ws_router)

    @app.get("/healthz", include_in_schema=False)
    def healthcheck() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/readyz", include_in_schema=False)
    def readiness_check():
        try:
            with SessionLocal() as db:
                db.execute(text("SELECT 1"))
        except Exception as exc:
            log_event("readiness_failed", level=logging.ERROR, error_type=type(exc).__name__)
            return JSONResponse({"ok": False, "error": "database_unavailable"}, status_code=503)
        return {"ok": True}

    @app.on_event("startup")
    def on_startup() -> None:
        init_db()
        log_event("startup_complete", app_name=settings.app_name)

    return app


app = create_app()
