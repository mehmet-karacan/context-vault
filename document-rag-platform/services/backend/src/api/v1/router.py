"""Aggregates all v1 API routers into a single router for ``main.py``."""

from fastapi import APIRouter

from . import chat, documents, health, ingestion_jobs, projects

api_router = APIRouter()

api_router.include_router(health.router)
api_router.include_router(projects.router)
api_router.include_router(documents.router)
api_router.include_router(ingestion_jobs.router)
api_router.include_router(chat.router)
