"""Document upload, listing and lifecycle endpoints.

Moved verbatim from ``main.py`` — no behavior change. Text extraction and
chunking helpers live here because they are only used by the upload
endpoint; they will move to dedicated parser/chunker adapters in a later
stage (see AKTIF_GOREV.md Aşama 3/4).
"""

import os
import tempfile
import uuid
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from ...db import get_db
from ...llm import PASSAGE_INSTRUCTION, embed_texts
from ...models import Chunk, Document, Project

router = APIRouter(tags=["documents"])


# --- Text extraction -------------------------------------------------------


def extract_text_from_docx(file_path: str) -> str:
    import docx

    doc = docx.Document(file_path)
    return "\n".join(para.text for para in doc.paragraphs)


def extract_text_from_pdf(file_path: str) -> str:
    from PyPDF2 import PdfReader

    reader = PdfReader(file_path)
    return "\n".join(page.extract_text() for page in reader.pages if page.extract_text())


def extract_text_from_txt(file_path: str) -> str:
    with open(file_path, "r", encoding="utf-8") as f:
        return f.read()


def extract_text(file_path: str, filename: str) -> str:
    ext = os.path.splitext(filename)[1].lower()
    if ext == ".docx":
        return extract_text_from_docx(file_path)
    if ext == ".pdf":
        return extract_text_from_pdf(file_path)
    if ext in (".txt", ".md"):
        return extract_text_from_txt(file_path)
    raise ValueError(f"Desteklenmeyen dosya türü: {ext}")


def chunk_text(text: str, chunk_size: int = 500, overlap: int = 50) -> List[str]:
    chunks = []
    start = 0
    text_length = len(text)

    while start < text_length:
        end = start + chunk_size
        chunk = text[start:end]

        if end < text_length:
            last_period = chunk.rfind(".")
            last_newline = chunk.rfind("\n")
            break_point = max(last_period, last_newline)
            if break_point > chunk_size * 0.5:
                chunk = chunk[: break_point + 1]
                end = start + break_point + 1
            else:
                # No good sentence break — fall back to the last word
                # boundary so we never cut a word in half.
                last_space = chunk.rfind(" ")
                if last_space > 0:
                    chunk = chunk[:last_space]
                    end = start + last_space

        stripped = chunk.strip()
        if stripped:
            chunks.append(stripped)
        start = end - overlap

    return chunks


# --- Serialization -----------------------------------------------------------


def serialize_document(doc: Document, chunks_count: Optional[int] = None) -> dict:
    return {
        "id": str(doc.id),
        "name": doc.name,
        "size": doc.size,
        "status": doc.status,
        "uploaded_at": doc.uploaded_at.strftime("%Y-%m-%d %H:%M"),
        "chunks_count": chunks_count if chunks_count is not None else len(doc.chunks),
        "error_message": doc.error_message,
        "project_id": str(doc.project_id),
        "project_name": doc.project.name if doc.project else None,
    }


# --- Routes --------------------------------------------------------------


@router.post("/documents/upload")
def upload_document(
    file: UploadFile = File(...),
    project_id: str = Form(...),
    chunk_size: int = Form(500),
    instruction: str = Form(PASSAGE_INSTRUCTION),
    db: Session = Depends(get_db),
):
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    chunk_size = max(50, min(chunk_size, 4000))
    document = Document(
        id=uuid.uuid4(),
        project_id=project.id,
        name=file.filename,
        size=file.size or 0,
        status="uploaded",
        uploaded_at=datetime.utcnow(),
    )
    db.add(document)
    db.commit()
    db.refresh(document)

    tmp_path = None
    try:
        suffix = os.path.splitext(file.filename)[1]
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp_file:
            tmp_file.write(file.file.read())
            tmp_path = tmp_file.name

        document.status = "processing"
        db.commit()

        text = extract_text(tmp_path, file.filename)
        if not text.strip():
            raise ValueError("Belgenin okunabilir metin içeriği bulunamadı.")

        chunks = chunk_text(text, chunk_size=chunk_size, overlap=min(50, chunk_size // 5))
        embeddings = embed_texts(chunks, instruction=instruction)

        for index, (content, embedding) in enumerate(zip(chunks, embeddings)):
            db.add(
                Chunk(
                    id=uuid.uuid4(),
                    document_id=document.id,
                    chunk_index=index,
                    content=content,
                    embedding=embedding,
                )
            )

        document.status = "indexed"
        db.commit()
        db.refresh(document)

        return {
            "success": True,
            "document": serialize_document(document, chunks_count=len(chunks)),
            "message": f"{file.filename} {len(chunks)} parçaya bölünüp vektörlendi.",
        }

    except Exception as e:
        document.status = "error"
        document.error_message = str(e)
        db.commit()
        return {"success": False, "document": serialize_document(document), "error": str(e)}

    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)


@router.get("/documents")
def list_documents(project_id: Optional[str] = None, db: Session = Depends(get_db)):
    query = db.query(Document)
    if project_id:
        query = query.filter(Document.project_id == project_id)
    documents = query.order_by(Document.uploaded_at.desc()).all()
    return [serialize_document(doc) for doc in documents]


@router.get("/documents/{doc_id}")
def get_document(doc_id: str, db: Session = Depends(get_db)):
    document = db.get(Document, doc_id)
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    return serialize_document(document)


@router.get("/documents/{doc_id}/status")
def get_document_status(doc_id: str, db: Session = Depends(get_db)):
    document = db.get(Document, doc_id)
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    progress = {"uploaded": 20, "processing": 60, "indexed": 100, "error": 0}
    return {
        "id": str(document.id),
        "status": document.status,
        "chunks_count": len(document.chunks),
        "progress": progress.get(document.status, 0),
    }


@router.post("/documents/{doc_id}/delete")
def delete_document(doc_id: str, db: Session = Depends(get_db)):
    document = db.get(Document, doc_id)
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    db.delete(document)
    db.commit()
    return {"success": True, "message": f"Document {doc_id} deleted"}
