"""FastAPI application factory (Aşama 1 / Aşama 9.4-9.5).

Assembles the app: CORS from resolved config (never "*" in production), a
request-id observability middleware, a global exception handler that hides
stack traces outside debug, the v1 router, and the database startup hook.

Kept free of business logic and route handlers per the Aşama 1 acceptance
criterion (see ``tests/test_main_app.py`` structural guard): route handlers
live in ``api/v1/*``.
"""

import logging
import traceback
from contextlib import asynccontextmanager
from typing import Optional
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .api.v1.health import probe_router
from .api.v1.router import api_router
from .config import Settings, settings
from .db import init_db
from .infrastructure.observability import (
    RequestContextMiddleware,
    configure_logging,
)
from .infrastructure.storage.minio_storage import decode_encryption_key

configure_logging()

LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}
DEFAULT_STORAGE_CREDENTIALS = {("minioadmin", "minioadmin")}
DEFAULT_DATABASE_CREDENTIALS = {
    ("postgres", "postgres"),
    ("raguser", "ragpass"),
    ("test", "test"),
}


def validate_runtime_security(cfg: Settings) -> None:
    """Reject insecure authentication and credential combinations at boot."""

    environment = cfg.APP_ENV.strip().lower()
    decode_encryption_key(cfg.OBJECT_STORAGE_ENCRYPTION_KEY)
    if cfg.AUTH_MODE == "disabled" and (
        environment != "local" or cfg.BIND_HOST.strip().lower() not in LOOPBACK_HOSTS
    ):
        raise ValueError(
            "AUTH_MODE=disabled is allowed only for APP_ENV=local on a loopback bind"
        )
    if cfg.AUTH_MODE == "api_key" and (
        not cfg.API_KEY_PEPPER or len(cfg.API_KEY_PEPPER) < 32
    ):
        raise ValueError(
            "AUTH_MODE=api_key requires API_KEY_PEPPER of at least 32 chars"
        )
    if environment in {"production", "staging"}:
        if not cfg.RATE_LIMIT_ENABLED or cfg.RATE_LIMIT_BACKEND != "redis":
            raise ValueError(
                "production/staging requires enabled Redis-backed rate limiting"
            )
        if (cfg.MINIO_ACCESS_KEY, cfg.MINIO_SECRET_KEY) in DEFAULT_STORAGE_CREDENTIALS:
            raise ValueError("default MinIO credentials are forbidden outside local")
        parsed = urlsplit(cfg.DATABASE_URL)
        if (parsed.username, parsed.password) in DEFAULT_DATABASE_CREDENTIALS:
            raise ValueError("default database credentials are forbidden outside local")


def _cors_origins(cfg: Settings):
    return list(cfg.cors_origins)


def create_app(cfg: Optional[Settings] = None) -> FastAPI:
    """Build and return the FastAPI application.

    ``cfg`` defaults to the module-level ``settings``; passing an explicit
    override makes CORS / debug / rate-limit behaviour testable per-env without
    mutating the shared singleton.
    """
    app_cfg = cfg or settings
    validate_runtime_security(app_cfg)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        init_db()
        yield

    application = FastAPI(title="Document RAG API", lifespan=lifespan)
    application.state.settings = app_cfg

    # Aşama 9.5: never "*" in production. allow_origins comes from resolved
    # config (see Settings.cors_origins).
    application.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins(app_cfg),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Aşama 9.4: request-id tagging + completion logging for every request.
    application.add_middleware(RequestContextMiddleware)

    # Aşama 9.5: never return a stack trace to the user unless API_DEBUG is on
    # in a non-production environment. The full traceback is still logged
    # server-side either way.
    @application.exception_handler(Exception)
    async def _handle_unhandled_exception(request: Request, exc: Exception):
        logging.getLogger("app.error").exception("Unhandled exception", exc_info=exc)
        if app_cfg.debug_enabled:
            return JSONResponse(
                status_code=500,
                content={
                    "detail": str(exc),
                    "traceback": traceback.format_exc(),
                },
            )
        return JSONResponse(
            status_code=500, content={"detail": "Internal Server Error"}
        )

    application.include_router(api_router)
    application.include_router(probe_router)
    return application


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=settings.BIND_HOST, port=8000)
