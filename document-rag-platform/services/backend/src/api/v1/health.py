"""Root and health-check endpoints.

Moved verbatim from ``main.py`` — no behavior change.
"""

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from ...db import get_db
from ...models import Document

router = APIRouter(tags=["health"])


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
