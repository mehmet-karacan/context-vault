"""Domain port definitions (Aşama 1).

These are pure interfaces (``typing.Protocol``) describing the contracts
future infrastructure adapters must satisfy. No implementation lives here
and nothing in the codebase depends on these yet — they exist so later
stages (parsers, chunkers, embeddings, retrieval, OCR, storage, repository
scanning — see AKTIF_GOREV.md Aşama 2+) have a stable shape to implement
against.

Concrete request/response dataclasses are formalized in the domain models
package. ``DocumentParser`` now returns the real ``NormalizedSource`` model
(see ``domain.normalized_content``, AKTIF_GOREV.md Bölüm 6). Other ports
(e.g. ``OcrResult``, chunk shapes) intentionally remain loosely typed until
their models are formalized in a later stage.
"""

from __future__ import annotations

from typing import Any, List, Optional, Protocol, runtime_checkable

from .normalized_content import NormalizedSource


@runtime_checkable
class DocumentParser(Protocol):
    """Parses a raw source file into a normalized content model."""

    def supports(self, mime_type: str, extension: str) -> bool:
        ...

    def parse(
        self,
        file_path: str,
        filename: str,
        options: Optional[dict] = None,
    ) -> NormalizedSource:
        """Returns a ``NormalizedSource`` for the given file.

        ``options`` is an optional passthrough dict (e.g. parser-specific
        hints); parsers should tolerate it being ``None``.
        """
        ...


@runtime_checkable
class OcrProvider(Protocol):
    """Extracts text from an image or a rendered page."""

    def extract(
        self,
        image_or_page: Any,
        languages: List[str],
        options: Optional[dict] = None,
    ) -> Any:
        """Returns an OcrResult (full_text, blocks, confidence, ...)."""
        ...


@runtime_checkable
class Chunker(Protocol):
    """Splits normalized content units into chunk-sized pieces."""

    def chunk(self, content: Any, **options: Any) -> List[Any]:
        """Returns a list of chunk-like objects for the given content."""
        ...


@runtime_checkable
class TokenCounter(Protocol):
    """Counts tokens for a given text under a specific tokenizer/model."""

    def count(self, text: str) -> int:
        ...


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Produces dense embedding vectors for text."""

    def embed(self, texts: List[str]) -> List[List[float]]:
        ...

    def embed_one(self, text: str) -> List[float]:
        ...


@runtime_checkable
class VectorRetriever(Protocol):
    """Retrieves candidate chunks by dense vector similarity."""

    def search(
        self,
        query_embedding: List[float],
        top_k: int,
        filters: Optional[dict] = None,
    ) -> List[Any]:
        ...


@runtime_checkable
class LexicalRetriever(Protocol):
    """Retrieves candidate chunks by lexical/full-text match."""

    def search(
        self,
        query_text: str,
        top_k: int,
        filters: Optional[dict] = None,
    ) -> List[Any]:
        ...


@runtime_checkable
class Reranker(Protocol):
    """Re-scores a candidate list of chunks against a query."""

    def rerank(self, query: str, candidates: List[Any], top_k: int) -> List[Any]:
        ...


@runtime_checkable
class ObjectStorage(Protocol):
    """Stores and retrieves immutable binary artifacts (e.g. MinIO)."""

    def put(self, key: str, data: bytes, content_type: Optional[str] = None) -> str:
        """Writes an object and returns its storage key."""
        ...

    def get(self, key: str) -> bytes:
        ...

    def delete(self, key: str) -> None:
        ...

    def exists(self, key: str) -> bool:
        ...


@runtime_checkable
class SourceScanner(Protocol):
    """Discovers ingestible files from a repository, archive or directory."""

    def scan(self, source_ref: str, **options: Any) -> List[Any]:
        """Returns a list of discovered file descriptors for the given source."""
        ...
