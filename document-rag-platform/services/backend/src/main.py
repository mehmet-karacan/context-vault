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
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .api.v1.router import api_router
from .config import Settings, settings
from .db import init_db
from .infrastructure.observability import (
    RequestContextMiddleware,
    configure_logging,
)

configure_logging()


def _cors_origins(cfg: Settings):
    return list(cfg.cors_origins)


def create_app(cfg: Optional[Settings] = None) -> FastAPI:
    """Build and return the FastAPI application.

    ``cfg`` defaults to the module-level ``settings``; passing an explicit
    override makes CORS / debug / rate-limit behaviour testable per-env without
    mutating the shared singleton.
    """
    app_cfg = cfg or settings

    application = FastAPI(title="Document RAG API")

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
        logging.getLogger("app.error").exception(
            "Unhandled exception", exc_info=exc
        )
        if app_cfg.debug_enabled:
            return JSONResponse(
                status_code=500,
                content={
                    "detail": str(exc),
                    "traceback": traceback.format_exc(),
                },
            )
        return JSONResponse(status_code=500, content={"detail": "Internal Server Error"})

    @application.on_event("startup")
    def _startup():
        init_db()

    application.include_router(api_router)
    return application


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
