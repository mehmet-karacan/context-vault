"""Root and health-check endpoints.

Moved from ``main.py`` (no behavior change for the original endpoints) and
extended in Aşama 9.4 with the health-vs-readiness split:

- ``GET /health``            — retained for backward compatibility (the
                               document counts slice the original endpoint
                               returned).
- ``GET /health/live``       — liveness: the process is up. No dependencies.
- ``GET /health/readiness``  — fail-closed admission for migration/DB/Redis /
                               MinIO/queue/required provider, with a bounded
                               capability signal for optional provider outage.
- ``GET /ready``             — alias for ``/health/readiness``.
- ``GET /``                  — root banner.
"""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from ...db import get_db
from ...infrastructure.observability import (
    ReadinessChecker,
    build_default_readiness_checks,
)
from ...models import Document

router = APIRouter(tags=["health"])
probe_router = APIRouter(tags=["health"])


def get_readiness_checker(request: Request) -> ReadinessChecker:
    """FastAPI dependency returning the readiness checker.

    Overridable in tests to inject stub checkers (no real services required).
    """
    configuration = request.app.state.settings
    optional = set() if configuration.PROVIDER_REQUIRED_FOR_READINESS else {"provider"}
    return ReadinessChecker(
        build_default_readiness_checks(configuration), optional=optional
    )


@router.get("/")
def root():
    return {"message": "Document RAG API is running"}


@router.get("/health")
def health(db: Session = Depends(get_db)):
    total = db.query(func.count(Document.id)).scalar()
    indexed = (
        db.query(func.count(Document.id)).filter(Document.status == "indexed").scalar()
    )
    return {
        "status": "healthy",
        "documents_count": total,
        "indexed_count": indexed,
    }


@probe_router.get("/health/live")
def liveness():
    """Liveness probe — the process is up and serving. No dependencies."""
    return {"status": "ok"}


@probe_router.get("/health/readiness")
def readiness(checker: ReadinessChecker = Depends(get_readiness_checker)):
    """Return only ready/not-ready or bounded optional capability degradation."""
    result = checker.run()
    ready = bool(result["ready"])
    content = {"status": "ready" if result["status"] == "ok" else result["status"]}
    if result["capabilities"]:
        content["capabilities"] = result["capabilities"]
    return JSONResponse(
        status_code=200 if ready else 503,
        content=content,
    )


@probe_router.get("/ready", include_in_schema=False)
def ready(checker: ReadinessChecker = Depends(get_readiness_checker)):
    """Alias endpoint for the readiness probe."""
    return readiness(checker)
