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
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from ...config import settings
from ...domain.retrieval import (
    ContextBundle,
    ContextItem,
    RejectedContextItem,
    ScopedNeighborKey,
)
from ...domain.retrieval_scope import RetrievalScope
from ..chunkers.base import NaiveTokenCounter

ContextBuildResult = ContextBundle

__all__ = [
    "ContextItem",
    "ContextBundle",
    "ContextBuildResult",
    "ContextBuilder",
    "context_item_from",
]

# A neighbour resolver returns the chunk that precedes/follows ``sequence_no``
# within ``source_id``, or None when there is no such chunk.
NeighborResolver = Callable[[RetrievalScope | None, ScopedNeighborKey], Optional[Any]]
ParentResolver = Callable[[RetrievalScope | None, str], Optional[Any]]


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
        self.max_chunks = int(
            max_chunks if max_chunks is not None else settings.CONTEXT_MAX_CHUNKS
        )
        self.max_tokens = int(
            max_tokens if max_tokens is not None else settings.CONTEXT_MAX_TOKENS
        )
        self.adjacent_window = int(
            adjacent_window
            if adjacent_window is not None
            else settings.CONTEXT_ADJACENT_WINDOW
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
        parent_resolver: Optional[ParentResolver] = None,
        scope: RetrievalScope | None = None,
        query_id: str = "",
        retrieval_run_id: str = "",
        reranker_profile: str = "none",
    ) -> ContextBuildResult:
        """Builds the context from ``candidates`` (in fused/reranked order).

        ``chunk_pool`` maps ``chunk_id`` -> chunk to resolve ``parent_chunk_id``
        links. ``neighbor_resolver(source_id, sequence_no)`` resolves adjacent
        chunks for controlled expansion. Both are optional and keep the builder
        DB-free and deterministic.
        """
        items: List[ContextItem] = []
        rejected: List[RejectedContextItem] = []
        total_tokens = 0
        truncated = False
        seen_hashes: set = set()
        remaining_chunks = self.max_chunks

        def add(item: ContextItem) -> bool:
            """Adds ``item`` respecting dedup + budgets. Returns True if it
            belongs to the context (either newly added or already present)."""
            nonlocal remaining_chunks, total_tokens, truncated
            if item.content_hash in seen_hashes:
                rejected.append(
                    RejectedContextItem(
                        item.chunk_id, item.rank, "duplicate_content", item.content_hash
                    )
                )
                return False
            if remaining_chunks <= 0:
                truncated = True
                rejected.append(
                    RejectedContextItem(
                        item.chunk_id, item.rank, "chunk_budget", item.content_hash
                    )
                )
                return False
            if total_tokens + item.token_count > self.max_tokens:
                truncated = True
                rejected.append(
                    RejectedContextItem(
                        item.chunk_id, item.rank, "token_budget", item.content_hash
                    )
                )
                return False
            seen_hashes.add(item.content_hash)
            items.append(item)
            total_tokens += item.token_count
            remaining_chunks -= 1
            return True

        pool = chunk_pool or {}

        for rank, candidate in enumerate(candidates or [], start=1):
            chunk = _chunk_of(candidate)
            if scope is not None and not self._in_scope(chunk, scope):
                rejected.append(
                    RejectedContextItem(
                        str(_get(chunk, "chunk_id") or ""),
                        rank,
                        "scope_mismatch",
                        _hash_of(chunk),
                    )
                )
                continue
            base = self._base_item(chunk, rank)
            if not base.content:
                rejected.append(
                    RejectedContextItem(
                        base.base.chunk_id,
                        rank,
                        "empty_content",
                        base.base.content_hash,
                    )
                )
                continue

            added_any = False
            if (
                base.row_group_header is not None
                and base.row_group_header.content.strip() not in base.content
            ):
                added_any = add(base.row_group_header) or added_any
            if (
                base.signature is not None
                and base.signature.content.strip() not in base.content
            ):
                added_any = add(base.signature) or added_any
            added_any = add(base.base) or added_any

            if not added_any:
                continue

            if remaining_chunks <= 0:
                truncated = True
                break

            parent = pool.get(str(_get(chunk, "parent_chunk_id") or ""))
            if (
                parent is None
                and parent_resolver is not None
                and _get(chunk, "parent_chunk_id")
            ):
                parent = parent_resolver(scope, str(_get(chunk, "parent_chunk_id")))
            if self.include_parents and parent is not None:
                if scope is None or self._in_scope(parent, scope):
                    add(self._item_from(parent, rank, relation="parent"))

            if self.include_adjacent and neighbor_resolver is not None:
                seq = int(_get(chunk, "sequence_no") or 0)
                key = self._neighbor_key(chunk, scope, seq)
                for offset in range(1, self.adjacent_window + 1):
                    for neighbor_seq in (seq - offset, seq + offset):
                        neighbor = neighbor_resolver(
                            scope,
                            ScopedNeighborKey(
                                workspace_id=key.workspace_id,
                                project_id=key.project_id,
                                document_id=key.document_id,
                                version_id=key.version_id,
                                source_file_id=key.source_file_id,
                                sequence_no=neighbor_seq,
                            ),
                        )
                        if neighbor is None:
                            continue
                        if scope is not None and not self._matches_neighbor_key(
                            neighbor, key
                        ):
                            rejected.append(
                                RejectedContextItem(
                                    str(_get(neighbor, "chunk_id") or ""),
                                    rank,
                                    "neighbor_boundary_mismatch",
                                    _hash_of(neighbor),
                                )
                            )
                            continue
                        if scope is not None and not self._in_scope(neighbor, scope):
                            rejected.append(
                                RejectedContextItem(
                                    str(_get(neighbor, "chunk_id") or ""),
                                    rank,
                                    "scope_mismatch",
                                    _hash_of(neighbor),
                                )
                            )
                            continue
                        add(self._item_from(neighbor, rank, relation="adjacent"))

            if remaining_chunks <= 0:
                truncated = True
                break

        reason_counts: Dict[str, int] = {}
        for item in rejected:
            reason_counts[item.reason] = reason_counts.get(item.reason, 0) + 1
        return ContextBundle(
            query_id=query_id,
            scope=scope,
            retrieval_run_id=retrieval_run_id,
            embedding_profile_id=str(scope.embedding_profile_id) if scope else None,
            reranker_profile=reranker_profile,
            selected_items=tuple(items),
            rejected_items=tuple(rejected),
            token_budget=self.max_tokens,
            chunk_budget=self.max_chunks,
            total_tokens=total_tokens,
            truncated=truncated,
            truncation_summary=reason_counts,
            provenance={"token_counter": type(self.token_counter).__name__},
        )

    # --- helpers ---------------------------------------------------------

    def _count(self, text: str) -> int:
        return max(0, int(self.token_counter.count(text or "")))

    @staticmethod
    def _identity(chunk: Any, name: str) -> str | None:
        value = _get(chunk, name)
        if value is None:
            value = dict(_get(chunk, "metadata") or {}).get(name)
        return str(value) if value not in (None, "") else None

    def _in_scope(self, chunk: Any, scope: RetrievalScope) -> bool:
        expected = {
            "workspace_id": str(scope.workspace_id),
            "project_id": str(scope.project_id),
        }
        for name, value in expected.items():
            actual = self._identity(chunk, name)
            if actual is None or actual != value:
                return False
        document_id = self._identity(chunk, "document_id")
        if scope.allowed_document_ids is not None and document_id not in {
            str(value) for value in scope.allowed_document_ids
        }:
            return False
        return True

    def _neighbor_key(
        self, chunk: Any, scope: RetrievalScope | None, sequence_no: int
    ) -> ScopedNeighborKey:
        return ScopedNeighborKey(
            workspace_id=self._identity(chunk, "workspace_id")
            or (str(scope.workspace_id) if scope else ""),
            project_id=self._identity(chunk, "project_id")
            or (str(scope.project_id) if scope else ""),
            document_id=self._identity(chunk, "document_id") or "",
            version_id=self._identity(chunk, "version_id") or "",
            source_file_id=self._identity(chunk, "source_file_id"),
            sequence_no=sequence_no,
        )

    def _context_identity(self, chunk: Any) -> Dict[str, Any]:
        metadata = dict(_get(chunk, "metadata") or {})
        return {
            "workspace_id": self._identity(chunk, "workspace_id"),
            "project_id": self._identity(chunk, "project_id"),
            "document_id": self._identity(chunk, "document_id"),
            "version_id": self._identity(chunk, "version_id"),
            "source_file_id": self._identity(chunk, "source_file_id"),
            "policy_classification": str(
                self._identity(chunk, "classification")
                or metadata.get("data_classification")
                or "internal"
            ),
            "metadata": metadata,
        }

    def _matches_neighbor_key(self, chunk: Any, key: ScopedNeighborKey) -> bool:
        expected = {
            "workspace_id": key.workspace_id,
            "project_id": key.project_id,
            "document_id": key.document_id,
            "version_id": key.version_id,
            "source_file_id": key.source_file_id,
        }
        return all(
            self._identity(chunk, name) == value for name, value in expected.items()
        )

    def _base_item(self, chunk: Any, rank: int) -> "_BaseItem":
        content = _text_of(chunk)
        chunk_id = str(_get(chunk, "chunk_id") or "")
        chunk_type = str(_get(chunk, "chunk_type") or "document")
        source_id = str(_get(chunk, "source_id") or "")
        metadata = dict(_get(chunk, "metadata") or {})
        identity = self._context_identity(chunk)

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
                **identity,
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
                **identity,
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
            **identity,
        )
        return _BaseItem(
            base=base, row_group_header=row_group_header, signature=signature
        )

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
            **self._context_identity(chunk),
        )

    def _make_item(self, **kwargs: Any) -> ContextItem:
        content = kwargs.pop("content", "") or ""
        content_hash = kwargs.pop("content_hash", "") or ""
        if not content_hash:
            content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        return ContextItem(
            content=content,
            content_hash=content_hash,
            evidence_hash=hashlib.sha256(
                (content_hash + "|" + str(kwargs.get("chunk_id", ""))).encode("utf-8")
            ).hexdigest(),
            workspace_id=kwargs.pop("workspace_id", None),
            project_id=kwargs.pop("project_id", None),
            document_id=kwargs.pop("document_id", None),
            version_id=kwargs.pop("version_id", None),
            source_file_id=kwargs.pop("source_file_id", None),
            policy_classification=kwargs.pop("policy_classification", "internal"),
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
        workspace_id=None,
        project_id=None,
        document_id=None,
        version_id=str(_get(chunk, "version_id"))
        if _get(chunk, "version_id")
        else None,
        source_file_id=None,
        metadata=dict(_get(chunk, "metadata") or {}),
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
