"""Chat / RAG query endpoints.

Moved verbatim from ``main.py`` — no behavior change. Retrieval logic
(vector + keyword fusion, threshold) is intentionally left exactly as-is;
it will be replaced by the hybrid retrieval pipeline in a later stage
(see AKTIF_GOREV.md Aşama 5).
"""

import re
from typing import List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import or_
from sqlalchemy.orm import Session

from ...db import get_db
from ...llm import AVAILABLE_CHAT_MODELS, CHAT_MODEL, QUERY_INSTRUCTION, embed_text, generate_answer
from ...models import Chunk, Document

router = APIRouter(tags=["chat"])


class ChatQuery(BaseModel):
    query: str
    project_id: Optional[str] = None
    model: Optional[str] = None


STOPWORDS = {
    "ve", "veya", "ile", "bir", "bu", "şu", "o", "de", "da", "mi", "mı", "mu", "mü",
    "nedir", "nasıl", "ne", "için", "gibi", "kadar", "çok", "az", "the", "and", "or",
    "is", "what", "how", "olan", "olarak", "var", "yok",
}


def extract_keywords(query: str) -> List[str]:
    words = re.findall(r"\w+", query.lower())
    return [w for w in words if len(w) >= 2 and w not in STOPWORDS]


@router.get("/chat/models")
def list_chat_models():
    return {"models": AVAILABLE_CHAT_MODELS, "default": CHAT_MODEL}


@router.post("/chat/query")
def query_chat(chat_query: ChatQuery, db: Session = Depends(get_db)):
    query_embedding = embed_text(chat_query.query, instruction=QUERY_INSTRUCTION)

    distance = Chunk.embedding.cosine_distance(query_embedding)
    search = (
        db.query(Chunk, Document, distance.label("distance"))
        .join(Document, Chunk.document_id == Document.id)
        .filter(Document.status == "indexed")
    )
    if chat_query.project_id:
        search = search.filter(Document.project_id == chat_query.project_id)

    TOP_K = 3

    # This is a per-corpus noise floor, not a fixed physical constant: chit-chat
    # queries ("selam") scored ~0.44-0.49 against one document set but up to
    # 0.53 against another (a relative top-vs-rest gap was tested and rejected
    # — it's *smaller* for genuine-but-weak matches than for "selam", so it
    # would reject real matches instead of chit-chat). 0.55 clears every
    # chit-chat probe measured so far; rely on the keyword-match path below
    # (not this threshold) to rescue literal term matches like "5G FWA" that
    # score low on pure vector similarity.
    SIMILARITY_THRESHOLD = 0.55
    vector_candidates = search.order_by(distance).limit(10).all()

    # Pure semantic ranking can bury a chunk that only mentions the query's
    # exact term once in passing (e.g. a product name cited in an unrelated
    # scoping paragraph) below chunks that are topically close but never
    # mention it at all — this is exactly what happened with "5G FWA": three
    # unrelated ETL chunks outranked the one chunk that names it. A chunk
    # containing a literal keyword from the query is pulled in regardless of
    # its vector rank, so an exact textual match is never lost to embedding
    # noise.
    keywords = extract_keywords(chat_query.query)
    keyword_candidates = []
    if keywords:
        keyword_search = search.filter(or_(*[Chunk.content.ilike(f"%{kw}%") for kw in keywords]))
        keyword_candidates = keyword_search.order_by(distance).limit(5).all()

    # Keyword matches get a reserved slot instead of competing with vector
    # candidates on similarity — that competition is exactly what buried
    # "5G FWA" in the first place. Fill remaining slots with the best
    # vector-only candidates above the threshold.
    keyword_ranked = sorted(
        [(chunk, doc, max(0.0, 1 - dist)) for chunk, doc, dist in keyword_candidates],
        key=lambda item: item[2],
        reverse=True,
    )[:TOP_K]
    seen_ids = {chunk.id for chunk, _, _ in keyword_ranked}

    vector_ranked = sorted(
        [
            (chunk, doc, max(0.0, 1 - dist))
            for chunk, doc, dist in vector_candidates
            if chunk.id not in seen_ids and max(0.0, 1 - dist) >= SIMILARITY_THRESHOLD
        ],
        key=lambda item: item[2],
        reverse=True,
    )

    ranked = keyword_ranked + vector_ranked[: TOP_K - len(keyword_ranked)]
    sources = [
        {"document": {"name": doc.name}, "chunk": chunk.content, "similarity": sim}
        for chunk, doc, sim in ranked
    ]

    answer = generate_answer(chat_query.query, [s["chunk"] for s in sources], model=chat_query.model)
    return {"answer": answer, "sources": sources}
