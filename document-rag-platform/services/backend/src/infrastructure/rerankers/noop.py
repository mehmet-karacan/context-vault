"""Pass-through reranker, safe fallback wrapper and factory (Aşama 5.4).

The Reranker component is feature-gated and must never take down the
retrieval pipeline:

- ``NoopReranker`` — deterministic pass-through used when reranking is
  disabled (``FEATURE_RERANKER`` / ``RERANKER_ENABLED`` false) or the
  provider is ``none``. Returns candidates unchanged (fusion order).
- ``SafeReranker`` — defensive wrapper that catches any exception raised by
  an underlying reranker and falls back to the input order instead of
  failing the request ("Reranker başarısız olursa fusion sıralamasına güvenli
  fallback yap", AKTIF_GOREV.md §5.4).
- ``build_reranker`` — settings-driven factory with safe fallback semantics.

Each implementation exposes a ``provider`` / ``model`` descriptor so the
rerank result can materialize which model was actually used into debug
metadata (AKTIF_GOREV.md §5.5 / §8.10 ``reranker_score`` / ``model``).
"""

from __future__ import annotations

from typing import Any, List, Optional

from ...domain.ports import Reranker
from ...config import Settings, settings as default_settings


class NoopReranker:
    """Pass-through reranker preserving original order and scores.

    Conforms to ``domain.ports.Reranker`` but does no re-scoring: it returns
    the candidate list unchanged, which is exactly the fusion (RRF) order a
    caller should fall back to. ``top_k`` is intentionally ignored so the
    pass-through is a pure identity mapping — truncation, if desired, is left
    to the caller / context builder.
    """

    provider: str = "none"
    model: str = "none"

    def __init__(self, model: str = "none") -> None:
        self.model = model or "none"

    def rerank(
        self, query: str, candidates: List[Any], top_k: int
    ) -> List[Any]:
        return list(candidates)


class SafeReranker:
    """Wraps any reranker and degrades gracefully on failure.

    Catches exceptions raised by the underlying reranker during ``rerank``
    and returns the candidate list unchanged (fusion order), so an upstream
    remote failure never bubbles up into a failed retrieval request. Exposes
    the underlying provider/model descriptor (or a "fusion" marker when it is
    a plain fallback) for debug metadata.
    """

    def __init__(self, inner: Reranker, fallback_model: str = "fusion") -> None:
        self._inner = inner
        self.provider = getattr(inner, "provider", "unknown")
        self.model = getattr(inner, "model", fallback_model) or fallback_model
        self.last_error: Optional[Exception] = None

    def rerank(
        self, query: str, candidates: List[Any], top_k: int
    ) -> List[Any]:
        try:
            return self._inner.rerank(query, candidates, top_k)
        except Exception as exc:  # noqa: BLE001 - intentional safe fallback
            self.last_error = exc
            return list(candidates)


def build_reranker(settings: Optional[Settings] = None) -> Reranker:
    """Builds the appropriate reranker from ``Settings`` (safe fallback).

    Semantics:
      - Disabled (``FEATURE_RERANKER`` false, or ``RERANKER_ENABLED`` false)
        or provider == "none"  -> ``NoopReranker`` (no client is constructed).
      - Provider configured      -> ``RemoteReranker`` wrapped in a
        ``SafeReranker`` so any runtime failure falls back to fusion order.
      - Any other / unknown      -> ``NoopReranker`` (safe fallback).
    """
    settings = settings or default_settings

    provider = (settings.RERANKER_PROVIDER or "none").strip().lower()
    enabled = bool(settings.FEATURE_RERANKER and settings.RERANKER_ENABLED)

    if not enabled or provider == "none":
        return NoopReranker(model=_noop_model(settings))

    if provider not in _REMOTE_PROVIDERS:
        # Unknown provider — never attempt a network call; degrade to Noop.
        return NoopReranker(model=_noop_model(settings))

    inner = _build_remote(settings, provider)
    return SafeReranker(inner)


_REMOTE_PROVIDERS = frozenset({"remote", "openai_compatible", "litellm", "openai"})


def _build_remote(settings: Settings, provider: str) -> Reranker:
    from .remote import RemoteReranker

    model = (settings.RERANKER_MODEL or "").strip() or settings.CHAT_MODEL
    return RemoteReranker(
        base_url=settings.LITELLM_BASE_URL,
        api_key=settings.LITELLM_API_KEY,
        model=model,
        provider=provider,
        top_k_default=settings.RERANK_TOP_K,
    )


def _noop_model(settings: Settings) -> str:
    configured = (settings.RERANKER_MODEL or "").strip()
    return configured or "none"
