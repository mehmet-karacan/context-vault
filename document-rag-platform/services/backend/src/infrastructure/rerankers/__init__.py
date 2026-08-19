"""Reranker components (Aşama 5.4).

Public API:
- ``NoopReranker``  — deterministic pass-through (fusion order fallback).
- ``RemoteReranker`` — OpenAI-compatible remote /rerank adapter (lazy client).
- ``SafeReranker``  — defensive wrapper that degrades to fusion order on error.
- ``build_reranker`` — settings-driven factory with safe fallback semantics.
"""

from __future__ import annotations

from .noop import NoopReranker, SafeReranker, build_reranker
from .remote import RemoteReranker

__all__ = [
    "NoopReranker",
    "RemoteReranker",
    "SafeReranker",
    "build_reranker",
]
