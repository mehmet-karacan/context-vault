"""Project CRUD endpoints.

Moved verbatim from ``main.py`` — no behavior change.
"""

import uuid
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ...db import get_db
from ...models import Project
from src.domain.identity import PrincipalContext
from src.domain.clock import utc_now
from src.infrastructure.security.auth import (
    get_principal_context,
    require_project_access,
)

router = APIRouter(tags=["projects"])


class ProjectCreate(BaseModel):
    name: str


def serialize_project(project: Project, document_count: Optional[int] = None) -> dict:
    return {
        "id": str(project.id),
        "name": project.name,
        "created_at": project.created_at.strftime("%Y-%m-%d %H:%M"),
        "document_count": document_count
        if document_count is not None
        else len(project.documents),
    }


@router.get("/projects")
def list_projects(
    db: Session = Depends(get_db),
    principal: PrincipalContext = Depends(get_principal_context),
):
    projects = (
        db.query(Project)
        .filter(
            Project.workspace_id == principal.workspace_id,
            Project.deleted_at.is_(None),
        )
        .order_by(Project.created_at.desc())
        .all()
    )
    return [serialize_project(p) for p in projects]


@router.post("/projects")
def create_project(
    payload: ProjectCreate,
    db: Session = Depends(get_db),
    principal: PrincipalContext = Depends(get_principal_context),
):
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Proje adı boş olamaz")
    if (
        db.query(Project)
        .filter(
            Project.workspace_id == principal.workspace_id,
            Project.name == name,
            Project.deleted_at.is_(None),
        )
        .first()
    ):
        raise HTTPException(status_code=409, detail="Bu isimde bir proje zaten var")
    project = Project(
        id=uuid.uuid4(),
        workspace_id=principal.workspace_id,
        name=name,
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    db.add(project)
    db.commit()
    db.refresh(project)
    return serialize_project(project)


@router.delete("/projects/{project_id}")
def delete_project(
    project_id: UUID,
    db: Session = Depends(get_db),
    principal: PrincipalContext = Depends(get_principal_context),
):
    project = require_project_access(db, principal, project_id)
    now = utc_now()
    project.deleted_at = now
    project.updated_at = now
    db.commit()
    return {"success": True, "message": f"Project {project_id} deleted"}
