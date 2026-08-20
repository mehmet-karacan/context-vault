"""Root and health-check endpoints.

Moved from ``main.py`` (no behavior change for the original endpoints) and
extended in Aşama 9.4 with the health-vs-readiness split:

- ``GET /health``            — retained for backward compatibility (the
                               document counts slice the original endpoint
                               returned).
- ``GET /health/live``       — liveness: the process is up. No dependencies.
- ``GET /health/readiness``  — readiness: per-dependency health for DB / Redis /
                               MinIO / embedding gateway. A down dependency
                               degrades (never crashes) the result.
- ``GET /ready``             — alias for ``/health/readiness``.
- ``GET /``                  — root banner.
"""

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from ...db import get_db
from ...infrastructure.observability import (
    ReadinessChecker,
    build_default_readiness_checks,
)
from ...models import Document

router = APIRouter(tags=["health"])


def get_readiness_checker() -> ReadinessChecker:
    """FastAPI dependency returning the readiness checker.

    Overridable in tests to inject stub checkers (no real services required).
    """
    return ReadinessChecker(build_default_readiness_checks())


@router.get("/")
def root():
    return {"message": "Document RAG API is running"}


@router.get("/health")
def health(db: Session = Depends(get_db)):
    total = db.query(func.count(Document.id)).scalar()
    indexed = db.query(func.count(Document.id)).filter(Document.status == "indexed").scalar()
    return {
        "status": "healthy",
        "documents_count": total,
        "indexed_count": indexed,
    }


@router.get("/health/live")
def liveness():
    """Liveness probe — the process is up and serving. No dependencies."""
    return {"status": "ok"}


@router.get("/health/readiness")
def readiness(checker: ReadinessChecker = Depends(get_readiness_checker)):
    """Readiness probe — reports per-dependency health (db/redis/minio/gateway).

    Returns 200 with ``status: degraded`` (not an error) when any single
    dependency is down (AKTIF_GOREV.md §9.4).
    """
    return checker.run()


@router.get("/ready")
def ready(checker: ReadinessChecker = Depends(get_readiness_checker)):
    """Alias endpoint for the readiness probe."""
    return checker.run()
