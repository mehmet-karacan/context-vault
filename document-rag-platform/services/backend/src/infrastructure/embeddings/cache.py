"""Deterministic embedding cache (Aşama 4).

A cache miss/duplicate layer for the ingestion pipeline: identical content
under the same embedding profile is never re-embedded. The cache key is
derived from ``content_hash + embedding_profile.config_hash`` (see
AKTIF_GOREV.md Bölüm 4 / Bölüm 7 / Bölüm 8.8 / Bölüm 8.9), so:

- same content + same profile -> same key -> one embedding, reused.
- same content + different profile -> different key -> recomputed.
- different content + same profile -> different key -> recomputed.

``EmbeddingCache`` is a thin facade over a pluggable backing store
(in-memory ``dict`` by default; SQLite/Redis can be supplied by handing an
object with ``get`` / ``set`` to the constructor).
"""

from __future__ import annotations

import hashlib
from typing import Any, Callable, Dict, Optional

# Fields of EmbeddingProfile that actually affect the embedding output. This
# is the *canonical* config-hash field set — adding/renaming a field here is a
# deliberate, reviewed change because it invalidates every cached vector.
PROFILE_HASH_FIELDS: tuple = (
    "provider",
    "model",
    "dimension",
    "distance_metric",
    "query_prefix",
    "passage_prefix",
    "profile_version",
)


def _normalize(value: Any) -> str:
    """Stable string form of a single config value (None-independent)."""
    if value is None:
        return "<__none__>"
    return str(value)


def profile_config_hash(profile: Any) -> str:
    """Deterministic SHA-256 over the fields that affect embedding output.

    Pure — no database access. Uses ``getattr`` so a plain object with the
    same attribute names (e.g. a test double) hashes identically to a real
    ``EmbeddingProfile`` instance. Each field is length-normalized via a
    ``name=NULvalueNUL`` framing so adjacent fields can never be confused
    (e.g. provider="ab" + model="c" vs provider="a" + model="bc").
    """
    hasher = hashlib.sha256()
    for field in PROFILE_HASH_FIELDS:
        value = _normalize(getattr(profile, field, None))
        hasher.update(f"{field}\x00{value}\x00".encode("utf-8"))
    return hasher.hexdigest()


def embedding_cache_key(content_hash: str, profile_config_hash: str) -> str:
    """Deterministic, collision-safe cache key for a chunk under a profile.

    Uses ``content_hash + ':' + profile_config_hash``. The ``':'`` separator
    is a structural delimiter, so two (content_hash, config_hash) pairs can
    only collide if both components match bit-for-bit; the SHA-256
    ``profile_config_hash`` makes the right half collision-resistant, and
    ``content_hash`` is itself a digest of the chunk content.
    """
    return f"{content_hash}:{profile_config_hash}"


class EmbeddingCache:
    """Keyed embedding cache backed by a pluggable ``get``/``set`` store.

    Defaults to an in-memory ``dict``. Pass any object exposing
    ``get(key) -> Optional[embedding]`` and ``set(key, embedding)`` (e.g. a
    thin SQLite or Redis adapter) to switch the backing store without
    changing callers.
    """

    def __init__(self, store: Optional[Any] = None):
        self._store: Dict[str, Any] = store if store is not None else {}

    def get(self, key: str) -> Optional[Any]:
        getter = getattr(self._store, "get", None)
        if callable(getter):
            return getter(key)
        return self._store[key] if key in self._store else None  # type: ignore[operator]

    def set(self, key: str, embedding: Any) -> None:
        setter = getattr(self._store, "set", None)
        if callable(setter):
            setter(key, embedding)
        else:
            self._store[key] = embedding  # type: ignore[index]

    def get_or_compute(
        self,
        content_hash: str,
        profile: Any,
        compute_fn: Callable[[], Any],
        batch_fn: Optional[Callable[..., Any]] = None,
    ) -> Any:
        """Return the cached embedding for ``content_hash``+``profile`` if
        present; otherwise compute, store, and return it.

        ``compute_fn`` is called *at most once per key* — the pipeline can
        call ``get_or_compute`` for every chunk and identical content under
        the same profile will never re-embed. ``batch_fn`` is accepted for
        call-site signature compatibility (future batch path) but the
        per-chunk path always uses ``compute_fn``.
        """
        key = embedding_cache_key(content_hash, profile_config_hash(profile))
        cached = self.get(key)
        if cached is not None:
            return cached
        embedding = compute_fn()
        self.set(key, embedding)
        return embedding
