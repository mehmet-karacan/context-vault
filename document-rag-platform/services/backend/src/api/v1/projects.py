"""Project CRUD endpoints.

Moved verbatim from ``main.py`` — no behavior change.
"""

import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ...db import get_db
from ...models import Project

router = APIRouter(tags=["projects"])


class ProjectCreate(BaseModel):
    name: str


def serialize_project(project: Project, document_count: Optional[int] = None) -> dict:
    return {
        "id": str(project.id),
        "name": project.name,
        "created_at": project.created_at.strftime("%Y-%m-%d %H:%M"),
        "document_count": document_count if document_count is not None else len(project.documents),
    }


@router.get("/projects")
def list_projects(db: Session = Depends(get_db)):
    projects = db.query(Project).order_by(Project.created_at.desc()).all()
    return [serialize_project(p) for p in projects]


@router.post("/projects")
def create_project(payload: ProjectCreate, db: Session = Depends(get_db)):
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Proje adı boş olamaz")
    if db.query(Project).filter(Project.name == name).first():
        raise HTTPException(status_code=409, detail="Bu isimde bir proje zaten var")
    project = Project(id=uuid.uuid4(), name=name, created_at=datetime.utcnow())
    db.add(project)
    db.commit()
    db.refresh(project)
    return serialize_project(project)


@router.delete("/projects/{project_id}")
def delete_project(project_id: str, db: Session = Depends(get_db)):
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    db.delete(project)
    db.commit()
    return {"success": True, "message": f"Project {project_id} deleted"}
