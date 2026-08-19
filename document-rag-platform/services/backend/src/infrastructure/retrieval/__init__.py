"""Retrieval components (Aşama 5): dense / lexical / identifier + RRF.

Public surface shared by the retrieval pipeline and tests.
"""

from __future__ import annotations

from .base import (
    RetrievalCandidate,
    FilterTerm,
    normalize_filters,
    filter_spec,
    render_where,
    to_candidates,
)
from .dense import DenseVectorRetriever, dense_sql_from_spec
from .lexical import LexicalRetriever, lexical_sql_from_spec
from .identifier import IdentifierRetriever, identifier_sql_from_spec, extract_identifiers
from .rrf import reciprocal_rank_fusion, fuse, dedupe

__all__ = [
    "RetrievalCandidate",
    "FilterTerm",
    "normalize_filters",
    "filter_spec",
    "render_where",
    "to_candidates",
    "DenseVectorRetriever",
    "dense_sql_from_spec",
    "LexicalRetriever",
    "lexical_sql_from_spec",
    "IdentifierRetriever",
    "identifier_sql_from_spec",
    "extract_identifiers",
    "reciprocal_rank_fusion",
    "fuse",
    "dedupe",
]
