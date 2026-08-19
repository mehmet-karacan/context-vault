"""Remote OpenAI-compatible reranker adapter (Aşama 5.4).

Calls an OpenAI-compatible gateway's ``/rerank`` endpoint, following the same
style as ``infrastructure.llm.chat_client`` and
``infrastructure.embeddings.openai_compatible`` (an ``OpenAI`` SDK client).

Client construction is lazy — the SDK client is only instantiated on the
first ``rerank`` call — so building the adapter (or building it eagerly via
the factory) never touches the network; provider == "none" never constructs a
client at all. ``rerank`` re-orders the input candidates by the gateway's
relevance scores and keeps the top ``top_k``, exposing ``provider`` /
``model`` for debug metadata.
"""

from __future__ import annotations

from typing import Any, List, Optional

from ...domain.ports import Reranker


class RemoteReranker:
    """Re-scores candidates through a remote OpenAI-compatible /rerank gate."""

    provider: str = "remote"

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        provider: str = "remote",
        top_k_default: Optional[int] = None,
        client: Any = None,
    ) -> None:
        self._base_url = base_url
        self._api_key = api_key
        self.model = model
        self.provider = provider
        self._top_k_default = top_k_default
        self._client = client
        self._client_factory = None

    # -- client construction ------------------------------------------------

    def _get_client(self) -> Any:
        """Lazily builds (once) the OpenAI SDK client for the gateway."""
        if self._client is None:
            from openai import OpenAI

            self._client = OpenAI(base_url=self._base_url, api_key=self._api_key)
        return self._client

    # -- Reranker port ------------------------------------------------------

    def rerank(
        self, query: str, candidates: List[Any], top_k: int
    ) -> List[Any]:
        if not candidates:
            return list(candidates)

        top_k = top_k if top_k is not None and top_k > 0 else (self._top_k_default or 0)
        if top_k <= 0:
            return list(candidates)

        client = self._get_client()
        results = self._call_rerank(
            client,
            query=query,
            documents=[_extract_text(c) for c in candidates],
            model=self.model,
            top_n=top_k,
        )
        return _reorder(candidates, results, top_k)

    def _call_rerank(
        self,
        client: Any,
        *,
        query: str,
        documents: List[str],
        model: str,
        top_n: int,
    ) -> List[Any]:
        """Invokes the gateway. Uses the SDK low-level ``/rerank`` POST."""
        response = client.post(
            "/rerank",
            body={
                "model": model,
                "query": query,
                "documents": documents,
                "top_n": top_n,
            },
        )
        return _extract_results(response)


def _extract_text(candidate: Any) -> str:
    if isinstance(candidate, dict):
        for key in ("text", "content", "chunk_text", "embedding_text"):
            value = candidate.get(key)
            if value:
                return str(value)
        return ""
    for attr in ("text", "content", "chunk_text", "embedding_text"):
        value = getattr(candidate, attr, None)
        if value:
            return str(value)
    return ""


def _extract_results(response: Any) -> List[Any]:
    results = getattr(response, "results", None)
    if results is None and isinstance(response, dict):
        results = response.get("results")
    return results or []


def _reorder(
    candidates: List[Any], results: List[Any], top_k: int
) -> List[Any]:
    """Maps gateway results back to the original candidates, re-ordered.

    Handles both dict-style and object-style candidate/result shapes. Each
    result is expected to carry an ``index`` (into ``candidates``) and a
    ``relevance_score``. Candidates without a result are dropped when a
    re-rank hit exists; results that reference out-of-range indices are
    skipped defensively.
    """
    if not results:
        return list(candidates)

    scored: List[tuple] = []
    for result in results:
        if isinstance(result, dict):
            index = result.get("index")
            score = result.get("relevance_score")
        else:
            index = getattr(result, "index", None)
            score = getattr(result, "relevance_score", None)
        if index is None or not (0 <= index < len(candidates)):
            continue
        scored.append((index, score))

    ranked = sorted(scored, key=lambda pair: _safe_score(pair[1]), reverse=True)
    ordered: List[Any] = []
    for index, score in ranked:
        candidate = list(candidates)[index]
        ordered.append(_attach_score(candidate, score))
        if len(ordered) >= top_k:
            break
    return ordered


def _safe_score(score: Any) -> float:
    try:
        return float(score)
    except (TypeError, ValueError):
        return 0.0


def _attach_score(candidate: Any, score: Any) -> Any:
    if isinstance(candidate, dict):
        enriched = dict(candidate)
        enriched["rerank_score"] = score
        return enriched
    try:
        # Only attach when the object is genuinely mutable (no dataclass
        # frozen/resolve flag); otherwise return the object untouched.
        candidate.rerank_score = score  # type: ignore[attr-defined]
    except (AttributeError, TypeError):
        pass
    return candidate
