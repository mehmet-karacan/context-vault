"""Tests for the deterministic embedding cache (Aşama 4).

The cache key is ``content_hash + embedding_profile.config_hash``; identical
content under the same profile must never be re-embedded. A plain object
stands in for ``EmbeddingProfile`` — no database is touched.
"""

from src.infrastructure.embeddings.cache import (
    EmbeddingCache,
    embedding_cache_key,
    profile_config_hash,
)


class FakeProfile:
    """Plain-object stand-in for EmbeddingProfile (see models.py)."""

    def __init__(
        self,
        provider="openai_compatible",
        model="openai/BAAI/bge-m3",
        dimension=1024,
        distance_metric="cosine",
        query_prefix=None,
        passage_prefix=None,
        profile_version=1,
        config_hash=None,
    ):
        self.provider = provider
        self.model = model
        self.dimension = dimension
        self.distance_metric = distance_metric
        self.query_prefix = query_prefix
        self.passage_prefix = passage_prefix
        self.profile_version = profile_version
        self.config_hash = config_hash


def make_profile(**overrides):
    base = {
        "provider": "openai_compatible",
        "model": "openai/BAAI/bge-m3",
        "dimension": 1024,
        "distance_metric": "cosine",
        "query_prefix": None,
        "passage_prefix": None,
        "profile_version": 1,
    }
    base.update(overrides)
    return FakeProfile(**base)


def test_key_formula_is_deterministic():
    k1 = embedding_cache_key("abc", "xyz")
    k2 = embedding_cache_key("abc", "xyz")
    assert k1 == k2
    assert k1 == "abc:xyz"


def test_key_changes_when_content_hash_changes():
    k1 = embedding_cache_key("abc", "xyz")
    k2 = embedding_cache_key("abd", "xyz")
    assert k1 != k2


def test_key_changes_when_profile_config_hash_changes():
    k1 = embedding_cache_key("abc", "xyz")
    k2 = embedding_cache_key("abc", "xyy")
    assert k1 != k2


def test_same_content_same_profile_same_key():
    p = make_profile()
    k1 = embedding_cache_key("c1", profile_config_hash(p))
    k2 = embedding_cache_key("c1", profile_config_hash(p))
    assert k1 == k2


def test_same_content_different_profile_different_key():
    p1 = make_profile()
    p2 = make_profile(model="openai/BAAI/bge-large")
    k1 = embedding_cache_key("c1", profile_config_hash(p1))
    k2 = embedding_cache_key("c1", profile_config_hash(p2))
    assert k1 != k2


def test_profile_config_hash_respects_embedding_affecting_fields():
    assert profile_config_hash(make_profile()) != profile_config_hash(
        make_profile(model="other")
    )
    assert profile_config_hash(make_profile()) != profile_config_hash(
        make_profile(dimension=768)
    )
    assert profile_config_hash(make_profile()) != profile_config_hash(
        make_profile(provider="ollama")
    )
    assert profile_config_hash(make_profile()) != profile_config_hash(
        make_profile(query_prefix="query: ")
    )
    assert profile_config_hash(make_profile()) != profile_config_hash(
        make_profile(passage_prefix="passage: ")
    )
    assert profile_config_hash(make_profile()) != profile_config_hash(
        make_profile(profile_version=2)
    )


def test_get_or_compute_computes_once_and_reuses_for_identical_key():
    cache = EmbeddingCache()
    calls = []

    def compute():
        calls.append(1)
        return [0.1, 0.2, 0.3]

    first = cache.get_or_compute("c1", make_profile(), compute)
    second = cache.get_or_compute("c1", make_profile(), compute)

    assert first == [0.1, 0.2, 0.3]
    assert second == first
    assert len(calls) == 1


def test_get_or_compute_recomputes_with_different_profile():
    cache = EmbeddingCache()
    calls = []
    p1 = make_profile()
    p2 = make_profile(profile_version=2)

    def compute():
        calls.append(1)
        return [1.0, 2.0]

    cache.get_or_compute("c1", p1, compute)
    cache.get_or_compute("c1", p2, compute)

    assert len(calls) == 2


def test_get_or_compute_recomputes_with_different_content():
    cache = EmbeddingCache()
    calls = []
    p = make_profile()

    def compute():
        calls.append(1)
        return [7.0]

    cache.get_or_compute("c1", p, compute)
    cache.get_or_compute("c2", p, compute)

    assert len(calls) == 2


def test_get_and_set_work_on_default_in_memory_store():
    cache = EmbeddingCache()
    key = embedding_cache_key("c1", profile_config_hash(make_profile()))
    cache.set(key, [5.0])
    assert cache.get(key) == [5.0]
    assert cache.get("missing:key") is None


def test_pluggable_backing_store_is_used():
    calls = {"get": 0, "set": 0}
    backing = {}

    class RecordingStore:
        def get(self, key):
            calls["get"] += 1
            return backing.get(key)

        def set(self, key, value):
            calls["set"] += 1
            backing[key] = value

    cache = EmbeddingCache(RecordingStore())
    cache.set("k", [1.0])
    assert cache.get("k") == [1.0]
    assert calls["get"] == 1
    assert calls["set"] == 1
    assert backing == {"k": [1.0]}
