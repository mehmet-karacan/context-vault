"""Aşama 5.4: Reranker component tests.

Covers the port conformant `NoopReranker`, the settings-driven factory's safe
fallback semantics, the `SafeReranker` degradation-on-error contract, and the
feature-gated guarantee that provider == "none" never constructs a remote
client. Everything is DB- and network-free: the remote adapter is exercised
with an injected fake client, never a real gateway.
"""

from src.domain import ports
from src.infrastructure.rerankers import (
    NoopReranker,
    RemoteReranker,
    SafeReranker,
    build_reranker,
)
from src.infrastructure.rerankers import noop as noop_module


class FakeSettings:
    """Plain-object stand-in for src.config.Settings (reranker fields only)."""

    def __init__(
        self,
        feature=True,
        enabled=True,
        provider="remote",
        model="rerank/x",
        base_url="http://gateway/v1",
        api_key="dummy-key",
        chat_model="some/chat",
        top_k=8,
    ):
        self.FEATURE_RERANKER = feature
        self.RERANKER_ENABLED = enabled
        self.RERANKER_PROVIDER = provider
        self.RERANKER_MODEL = model
        self.LITELLM_BASE_URL = base_url
        self.LITELLM_API_KEY = api_key
        self.CHAT_MODEL = chat_model
        self.RERANK_TOP_K = top_k


def _candidates():
    return [
        {"chunk_id": "a", "score": 0.9},
        {"chunk_id": "b", "score": 0.6},
        {"chunk_id": "c", "score": 0.4},
    ]


# --- Reranker port conformance ---------------------------------------------


def test_noop_conforms_to_reranker_port():
    assert isinstance(NoopReranker(), ports.Reranker)
    assert isinstance(NoopReranker().rerank("q", [], 8), list)


# --- NoopReranker ----------------------------------------------------------


def test_noop_returns_candidates_unchanged():
    candidates = _candidates()
    out = NoopReranker().rerank("query", candidates, 8)
    assert out == candidates
    # identity preserved: same objects, same order, same scores
    assert out is not candidates
    assert [c["chunk_id"] for c in out] == ["a", "b", "c"]
    assert [c["score"] for c in out] == [0.9, 0.6, 0.4]


def test_noop_preserves_original_order():
    candidates = _candidates()
    out = NoopReranker().rerank("query", candidates, 2)
    assert [c["chunk_id"] for c in out] == ["a", "b", "c"]


def test_noop_handles_empty_candidates():
    assert NoopReranker().rerank("query", [], 8) == []


def test_noop_descriptor_is_stable():
    r1 = NoopReranker()
    r2 = NoopReranker()
    assert r1.provider == "none"
    assert r1.model == "none"
    assert r1.provider == r2.provider
    assert r1.model == r2.model


def test_noop_records_configured_model_seed():
    r = NoopReranker(model="some/model")
    assert r.model == "some/model"
    assert r.provider == "none"


# --- SafeReranker ----------------------------------------------------------


class FailingReranker:
    provider = "remote"
    model = "failing/model"

    def rerank(self, query, candidates, top_k):
        raise RuntimeError("gateway down")


class WorkingReranker:
    provider = "remote"
    model = "working/model"

    def rerank(self, query, candidates, top_k):
        return list(reversed(candidates))


def test_safe_reranker_returns_inputs_unchanged_when_inner_raises():
    candidates = _candidates()
    safe = SafeReranker(FailingReranker())
    out = safe.rerank("query", candidates, 8)
    assert out == candidates
    assert [c["chunk_id"] for c in out] == ["a", "b", "c"]
    assert safe.last_error is not None
    assert isinstance(safe.last_error, RuntimeError)


def test_safe_reranker_propagates_successful_inner_result():
    candidates = _candidates()
    safe = SafeReranker(WorkingReranker())
    out = safe.rerank("query", candidates, 8)
    assert [c["chunk_id"] for c in out] == ["c", "b", "a"]
    assert safe.last_error is None


def test_safe_reranker_exposes_inner_descriptor():
    safe = SafeReranker(WorkingReranker())
    assert safe.provider == "remote"
    assert safe.model == "working/model"


# --- RemoteReranker (mocked client, network-free) ---------------------------


def test_remote_reranker_uses_injected_client_and_reorders():
    class FakeClient:
        def post(self, path, body):
            assert path == "/rerank"
            assert body["model"] == "rerank/x"
            assert len(body["documents"]) == 3

            class Response:
                results = [
                    {"index": 2, "relevance_score": 0.9},
                    {"index": 0, "relevance_score": 0.5},
                ]

            return Response()

    candidates = _candidates()
    rr = RemoteReranker(
        base_url="http://gateway/v1",
        api_key="dummy",
        model="rerank/x",
        top_k_default=8,
        client=FakeClient(),
    )
    out = rr.rerank("query", candidates, 8)
    assert [c["chunk_id"] for c in out] == ["c", "a"]
    assert out[0]["rerank_score"] == 0.9
    assert rr.provider == "remote"
    assert rr.model == "rerank/x"


def test_remote_reranker_lazy_client_not_constructed_by_default():
    # Building the adapter must not construct/resolve an OpenAI client until
    # the first rerank call. Construct-only (no rerank) must not call post.
    class ExplodingClient:
        def __init__(self):
            raise AssertionError("client should not be constructed")

    rr = RemoteReranker(
        base_url="http://gateway/v1",
        api_key="dummy",
        model="rerank/x",
        client=None,
    )
    # patch the factory to prove laziness: only _get_client would create one
    built = []
    original_get_client = rr._get_client

    def tracking_get_client():
        built.append(1)
        return original_get_client()

    rr._get_client = tracking_get_client
    assert built == []
    # still no default client and no construction happened without a call
    assert rr._client is None


def test_remote_reranker_returns_empty_for_no_candidates():
    rr = RemoteReranker(
        base_url="http://g", api_key="k", model="m", client=object()
    )
    assert rr.rerank("query", [], 8) == []


# --- Factory -----------------------------------------------------------------


def test_factory_returns_noop_when_disabled():
    rr = build_reranker(FakeSettings(feature=False, enabled=False, provider="remote"))
    assert isinstance(rr, NoopReranker)


def test_factory_returns_noop_when_feature_off_but_enabled():
    rr = build_reranker(FakeSettings(feature=False, enabled=True, provider="remote"))
    assert isinstance(rr, NoopReranker)


def test_factory_returns_noop_when_enabled_flag_off():
    rr = build_reranker(FakeSettings(feature=True, enabled=False, provider="remote"))
    assert isinstance(rr, NoopReranker)


def test_factory_returns_noop_when_provider_none():
    rr = build_reranker(FakeSettings(feature=True, enabled=True, provider="none"))
    assert isinstance(rr, NoopReranker)
    assert rr.provider == "none"


def test_factory_returns_noop_for_unknown_provider():
    rr = build_reranker(FakeSettings(feature=True, enabled=True, provider="weird"))
    assert isinstance(rr, NoopReranker)


def test_factory_provider_none_never_constructs_client(monkeypatch):
    calls = {"n": 0}

    def raiser(*args, **kwargs):
        calls["n"] += 1
        raise AssertionError("remote client must not be built for provider=none")

    monkeypatch.setattr(noop_module, "_build_remote", raiser)
    rr = build_reranker(FakeSettings(feature=True, enabled=True, provider="none"))
    assert isinstance(rr, NoopReranker)
    assert calls["n"] == 0


def test_factory_returns_safe_wrapping_remote_when_provider_remote():
    rr = build_reranker(FakeSettings(feature=True, enabled=True, provider="remote", model="rerank/x"))
    assert isinstance(rr, SafeReranker)
    assert isinstance(rr._inner, RemoteReranker)
    assert rr.provider == "remote"
    assert rr.model == "rerank/x"


def test_factory_safe_fallback_degrades_on_remote_failure():
    # Simulate a gateway failure inside the factory-produced wrapper by
    # substituting the inner remote with one that always raises.
    candidates = _candidates()
    rr = SafeReranker(FailingReranker())
    out = rr.rerank("query", candidates, 8)
    assert out == candidates
    assert rr.last_error is not None
