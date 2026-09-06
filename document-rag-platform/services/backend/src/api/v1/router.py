"""Aggregates all v1 API routers into a single router for ``main.py``."""

from fastapi import APIRouter, Depends

from . import (
    chat,
    debug,
    documents,
    health,
    ingestion_jobs,
    projects,
    repositories,
    session,
)
from ...infrastructure.security.auth import get_principal_context

api_router = APIRouter(prefix="/api/v1", dependencies=[Depends(get_principal_context)])

api_router.include_router(health.router)
api_router.include_router(session.router)
api_router.include_router(projects.router)
api_router.include_router(documents.router)
api_router.include_router(ingestion_jobs.router)
api_router.include_router(chat.router)
api_router.include_router(debug.router)
api_router.include_router(repositories.router)
