"""RAG context builder (Aşama 5.5 - Context genişletme).

Turns the top-N fused / reranked retrieval candidates into the final model
context:

- For each selected chunk it expands surrounding context by adding the chunk's
  parent (by ``parent_chunk_id``) and/or controlled adjacent chunks (by
  ``sequence_no`` within the same source).
- It never adds the same text twice (dedup by ``content_hash``), never exceeds
  the token budget (``CONTEXT_MAX_TOKENS``) or the chunk cap
  (``CONTEXT_MAX_CHUNKS``).
- Table row-groups already carry repeated header context from chunking; when a
  table chunk additionally exposes an explicit ``header`` in its metadata it is
  emitted as a ``table_header`` item if not already contained in the body. Code
  chunks may expose a ``signature`` that is emitted as a ``code_signature`` item.
- Output is a deterministic, ordered list of :class:`ContextItem` preserving the
  raw ``content`` (preferring it over ``embedding_text``) plus citation / locator
  metadata, a rank and a total token count within budget.
- The dependency-free ``TokenCounter`` is injected as a ``count(text)`` receiver
  (like the chunkers do; defaults to the conservative naive counter). No config
  value is hardcoded — budgets/thresholds come from ``settings`` with injectable
  overrides for tests.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from ...config import settings
from ..chunkers.base import NaiveTokenCounter

__all__ = ["ContextItem", "ContextBuildResult", "ContextBuilder", "context_item_from"]

# A neighbour resolver returns the chunk that precedes/follows ``sequence_no``
# within ``source_id``, or None when there is no such chunk.
NeighborResolver = Callable[[str, int], Optional[Any]]


def _get(obj: Any, name: str, default: Any = None) -> Any:
    """Reads a field from a dict or any object (dataclass), duck-typed."""
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _chunk_of(candidate: Any) -> Any:
    """Returns the actual chunk object for a retrieval candidate.

    Accepts a bare chunk or a ``RetrievalCandidate`` object holding a nested
    ``chunk`` field (the shared ``RetrievalCandidate`` in ``retrieval/base.py``
    is owned by a concurrent worker, so we only duck-type it here, never import).
    """
    nested = _get(candidate, "chunk")
    return nested if nested is not None else candidate


def _text_of(chunk: Any) -> str:
    """Raw ``content`` preferred; falls back to ``embedding_text`` when empty."""
    content = _get(chunk, "content", "")
    if content:
        return content
    return _get(chunk, "embedding_text", "") or ""


def _hash_of(chunk: Any) -> str:
    explicit = _get(chunk, "content_hash")
    if explicit:
        return str(explicit)
    return hashlib.sha256(_text_of(chunk).encode("utf-8")).hexdigest()


def _heading_path(chunk: Any) -> List[str]:
    return list(_get(chunk, "heading_path") or [])


def _locator(chunk: Any) -> Dict[str, Any]:
    return dict(_get(chunk, "locator") or {})


@dataclass(frozen=True)
class ContextItem:
    """A single context unit handed to the model with citation metadata."""

    chunk_id: str
    source_id: str
    chunk_type: str
    content: str
    heading_path: List[str] = field(default_factory=list)
    locator: Dict[str, Any] = field(default_factory=dict)
    content_hash: str = ""
    token_count: int = 0
    rank: int = 0
    sequence_no: int = 0
    relation: str = "selected"  # selected | parent | adjacent | table_header | code_signature

    def to_dict(self) -> Dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,
            "source_id": self.source_id,
            "chunk_type": self.chunk_type,
            "content": self.content,
            "heading_path": list(self.heading_path),
            "locator": dict(self.locator),
            "content_hash": self.content_hash,
            "token_count": self.token_count,
            "rank": self.rank,
            "sequence_no": self.sequence_no,
            "relation": self.relation,
        }


@dataclass
class ContextBuildResult:
    """Final RAG context: ordered items + accounting within budget."""

    items: List[ContextItem] = field(default_factory=list)
    total_tokens: int = 0
    max_tokens: int = 0
    max_chunks: int = 0
    truncated: bool = False
    selected_chunk_ids: List[str] = field(default_factory=list)
    expanded_chunk_ids: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "items": [item.to_dict() for item in self.items],
            "total_tokens": self.total_tokens,
            "max_tokens": self.max_tokens,
            "max_chunks": self.max_chunks,
            "truncated": self.truncated,
            "selected_chunk_ids": list(self.selected_chunk_ids),
            "expanded_chunk_ids": list(self.expanded_chunk_ids),
        }


class ContextBuilder:
    """Builds the final RAG context from fused/reranked candidates."""

    def __init__(
        self,
        token_counter: Optional[Callable[[str], int]] = None,
        *,
        max_chunks: Optional[int] = None,
        max_tokens: Optional[int] = None,
        adjacent_window: Optional[int] = None,
        include_parents: bool = True,
        include_adjacent: bool = True,
    ):
        self.token_counter: Callable[[str], int] = token_counter or NaiveTokenCounter()
        self.max_chunks = int(max_chunks if max_chunks is not None else settings.CONTEXT_MAX_CHUNKS)
        self.max_tokens = int(max_tokens if max_tokens is not None else settings.CONTEXT_MAX_TOKENS)
        self.adjacent_window = int(
            adjacent_window if adjacent_window is not None else settings.CONTEXT_ADJACENT_WINDOW
        )
        self.include_parents = include_parents
        self.include_adjacent = include_adjacent

    # --- public API ------------------------------------------------------

    def build(
        self,
        candidates: List[Any],
        *,
        chunk_pool: Optional[Dict[str, Any]] = None,
        neighbor_resolver: Optional[NeighborResolver] = None,
    ) -> ContextBuildResult:
        """Builds the context from ``candidates`` (in fused/reranked order).

        ``chunk_pool`` maps ``chunk_id`` -> chunk to resolve ``parent_chunk_id``
        links. ``neighbor_resolver(source_id, sequence_no)`` resolves adjacent
        chunks for controlled expansion. Both are optional and keep the builder
        DB-free and deterministic.
        """
        result = ContextBuildResult(
            max_tokens=self.max_tokens,
            max_chunks=self.max_chunks,
        )
        seen_hashes: set = set()
        remaining_chunks = self.max_chunks

        def add(item: ContextItem) -> bool:
            """Adds ``item`` respecting dedup + budgets. Returns True if it
            belongs to the context (either newly added or already present)."""
            nonlocal remaining_chunks
            if item.content_hash in seen_hashes:
                return True
            if remaining_chunks <= 0:
                return False
            if result.total_tokens + item.token_count > self.max_tokens:
                result.truncated = True
                return False
            seen_hashes.add(item.content_hash)
            result.items.append(item)
            result.total_tokens += item.token_count
            remaining_chunks -= 1
            return True

        pool = chunk_pool or {}

        for rank, candidate in enumerate(candidates or [], start=1):
            chunk = _chunk_of(candidate)
            base = self._base_item(chunk, rank)
            if not base.content:
                continue

            added_any = False
            if base.row_group_header is not None and base.row_group_header.content.strip() not in base.content:
                added_any = add(base.row_group_header) or added_any
            if base.signature is not None and base.signature.content.strip() not in base.content:
                added_any = add(base.signature) or added_any
            added_any = add(base.base) or added_any

            if not added_any:
                continue

            if remaining_chunks <= 0:
                result.truncated = True
                break

            parent = pool.get(str(_get(chunk, "parent_chunk_id") or ""))
            if self.include_parents and parent is not None:
                parent_item = self._item_from(parent, rank, relation="parent")
                if add(parent_item):
                    result.expanded_chunk_ids.append(parent_item.chunk_id)

            if self.include_adjacent and neighbor_resolver is not None:
                seq = int(_get(chunk, "sequence_no") or 0)
                source_id = str(_get(chunk, "source_id") or "")
                for offset in range(1, self.adjacent_window + 1):
                    for neighbor in (
                        neighbor_resolver(source_id, seq - offset),
                        neighbor_resolver(source_id, seq + offset),
                    ):
                        if neighbor is None:
                            continue
                        n_item = self._item_from(neighbor, rank, relation="adjacent")
                        if add(n_item):
                            result.expanded_chunk_ids.append(n_item.chunk_id)

            if remaining_chunks <= 0:
                result.truncated = True
                break

        return result

    # --- helpers ---------------------------------------------------------

    def _count(self, text: str) -> int:
        return max(0, int(self.token_counter.count(text or "")))

    def _base_item(self, chunk: Any, rank: int) -> "_BaseItem":
        content = _text_of(chunk)
        chunk_id = str(_get(chunk, "chunk_id") or "")
        chunk_type = str(_get(chunk, "chunk_type") or "document")
        source_id = str(_get(chunk, "source_id") or "")
        metadata = dict(_get(chunk, "metadata") or {})

        row_group_header: Optional[ContextItem] = None
        if chunk_type == "table" and metadata.get("header"):
            header_text = str(metadata["header"])
            row_group_header = self._make_item(
                chunk_id=chunk_id,
                source_id=source_id,
                chunk_type=chunk_type,
                content=header_text,
                heading_path=_heading_path(chunk),
                locator=_locator(chunk),
                content_hash=hashlib.sha256(header_text.encode("utf-8")).hexdigest(),
                rank=rank,
                sequence_no=int(_get(chunk, "sequence_no") or 0),
                relation="table_header",
            )

        signature: Optional[ContextItem] = None
        if chunk_type == "code" and metadata.get("signature"):
            sig_text = str(metadata["signature"])
            signature = self._make_item(
                chunk_id=chunk_id,
                source_id=source_id,
                chunk_type=chunk_type,
                content=sig_text,
                heading_path=_heading_path(chunk),
                locator=_locator(chunk),
                content_hash=hashlib.sha256(sig_text.encode("utf-8")).hexdigest(),
                rank=rank,
                sequence_no=int(_get(chunk, "sequence_no") or 0),
                relation="code_signature",
            )

        base = self._make_item(
            chunk_id=chunk_id,
            source_id=source_id,
            chunk_type=chunk_type,
            content=content,
            heading_path=_heading_path(chunk),
            locator=_locator(chunk),
            content_hash=_hash_of(chunk),
            rank=rank,
            sequence_no=int(_get(chunk, "sequence_no") or 0),
            relation="selected",
        )
        return _BaseItem(base=base, row_group_header=row_group_header, signature=signature)

    def _item_from(self, chunk: Any, rank: int, *, relation: str) -> ContextItem:
        return self._make_item(
            chunk_id=str(_get(chunk, "chunk_id") or ""),
            source_id=str(_get(chunk, "source_id") or ""),
            chunk_type=str(_get(chunk, "chunk_type") or "document"),
            content=_text_of(chunk),
            heading_path=_heading_path(chunk),
            locator=_locator(chunk),
            content_hash=_hash_of(chunk),
            rank=rank,
            sequence_no=int(_get(chunk, "sequence_no") or 0),
            relation=relation,
        )

    def _make_item(self, **kwargs: Any) -> ContextItem:
        content = kwargs.pop("content", "") or ""
        content_hash = kwargs.pop("content_hash", "") or ""
        if not content_hash:
            content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        return ContextItem(
            content=content,
            content_hash=content_hash,
            token_count=self._count(content),
            **kwargs,
        )


@dataclass
class _BaseItem:
    base: ContextItem
    row_group_header: Optional[ContextItem] = None
    signature: Optional[ContextItem] = None

    @property
    def content(self) -> str:
        return self.base.content


def context_item_from(chunk: Any) -> ContextItem:
    """Convenience: build a single ``ContextItem`` from one chunk (mostly tests)."""
    content = _text_of(chunk)
    return ContextItem(
        chunk_id=str(_get(chunk, "chunk_id") or ""),
        source_id=str(_get(chunk, "source_id") or ""),
        chunk_type=str(_get(chunk, "chunk_type") or "document"),
        content=content,
        heading_path=_heading_path(chunk),
        locator=_locator(chunk),
        content_hash=_hash_of(chunk),
        token_count=NaiveTokenCounter().count(content),
        rank=0,
        sequence_no=int(_get(chunk, "sequence_no") or 0),
        relation="selected",
    )
