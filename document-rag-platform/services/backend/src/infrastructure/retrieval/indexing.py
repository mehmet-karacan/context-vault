"""Non-dense retrieval index builders (Aşama 5.2).

The live retrieval pipeline reads three indexes per chunk:

- dense    -> ``chunk_embeddings.embedding`` (or the legacy ``chunks.embedding``)
- lexical  -> ``chunks.search_vector`` (a ``simple``-config tsvector)
- identifi -> ``chunks.identifiers`` (array of technical identifier tokens)

Historically only the dense vector was persisted, so LexicalRetriever /
IdentifierRetriever found nothing and a correctly-indexed document could still
produce a "no answer" result. This module centralizes building the lexical and
identifier maps so every ingestion write path (sync upload, async worker,
re-index) populates them identically.

``chunk_identifiers`` derives the identifier array from a chunk's content with
the same tokenizer IdentifierRetriever uses at query time (extract_identifiers),
so what gets stored can actually be matched.

``build_search_vector_stmt`` returns a live SQL UPDATE that computes
``to_tsvector('simple', content)`` inside Postgres (tsvector is not derivable
client-side) for every chunk of a document.
"""

from __future__ import annotations

from typing import List, Optional

from sqlalchemy import text

from src.infrastructure.retrieval.identifier import extract_identifiers

#: Text-search config used both by LexicalRetriever and the tsvector builder.
#: ``simple`` (not ``turkish``/``english``) so technical identifiers survive
#: unstemmed (mirrors lexical.py DEFAULT_TEXT_SEARCH_CONFIG).
SEARCH_VECTOR_TS_CONFIG: str = "simple"


def chunk_identifiers(content: Optional[str]) -> Optional[List[str]]:
    """Return the identifier array to persist for ``content`` (or None)."""
    ids = extract_identifiers(content or "")
    return ids or None


def build_search_vector_stmt(document_id):
    """A parameterized ``UPDATE`` that sets ``search_vector`` from ``content``
    for all chunks of ``document_id`` (executed with ``{"document_id": id}``)."""
    return text(
        "UPDATE chunks "
        f"SET search_vector = to_tsvector('{SEARCH_VECTOR_TS_CONFIG}', content) "
        "WHERE document_id = :document_id"
    )
