"""Unit contracts for the offline local benchmark model adapters."""

from __future__ import annotations

import importlib.util
import json
import math
import sys
import types
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[5]
SCRIPT = REPO / "scripts/local_benchmark_providers.py"


def _module():
    spec = importlib.util.spec_from_file_location("cv_local_providers", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _bge_snapshot(tmp_path: Path) -> Path:
    path = tmp_path / "models--BAAI--bge-m3" / "snapshots" / "revision-a"
    path.mkdir(parents=True)
    (path / "config.json").write_text('{"model_type":"xlm-roberta"}\n')
    (path / "modules.json").write_text("[]\n")
    (path / "model.safetensors").write_bytes(b"bge-weights")
    (path / "tokenizer.json").write_text("{}\n")
    return path


def _qwen_snapshot(tmp_path: Path) -> Path:
    path = tmp_path / "models--Qwen--Qwen2.5-1.5B-Instruct" / "snapshots" / "revision-a"
    path.mkdir(parents=True)
    (path / "config.json").write_text(
        '{"model_type":"qwen2","architectures":["Qwen2ForCausalLM"]}\n'
    )
    (path / "generation_config.json").write_text("{}\n")
    (path / "model.safetensors").write_bytes(b"qwen-weights")
    (path / "tokenizer.json").write_text("{}\n")
    (path / "tokenizer_config.json").write_text("{}\n")
    return path


class _Counter:
    def count(self, text: str) -> int:
        return len(text.split())


class _EmbeddingModel:
    def __init__(self, vectors=None, mutate=None):
        self.vectors = vectors
        self.mutate = mutate
        self.calls = []

    def encode(self, texts, **kwargs):
        self.calls.append((list(texts), kwargs))
        if self.mutate:
            self.mutate()
        if self.vectors is not None:
            return self.vectors
        return [[1.0] + [0.0] * 1023 for _ in texts]


def _bge(module, snapshot, model, **kwargs):
    bundle = module.bge_verifier.snapshot_bundle(snapshot)
    return module.LocalBgeProvider(
        snapshot=snapshot,
        expected_model="BAAI/bge-m3",
        expected_revision="revision-a",
        expected_bundle_sha256=bundle["sha256"],
        loader=lambda *_args, **loader_kwargs: (
            kwargs.setdefault("loader_kwargs", loader_kwargs),
            model,
        )[1],
        token_counter=_Counter(),
        **{key: value for key, value in kwargs.items() if key != "loader_kwargs"},
    )


def test_bge_is_offline_exact_normalized_and_counts_query_vs_batch(tmp_path):
    module = _module()
    snapshot = _bge_snapshot(tmp_path)
    model = _EmbeddingModel()
    captured = {}
    events = []
    bundle = module.bge_verifier.snapshot_bundle(snapshot)

    provider = module.LocalBgeProvider(
        snapshot=snapshot,
        expected_model="BAAI/bge-m3",
        expected_revision="revision-a",
        expected_bundle_sha256=bundle["sha256"],
        loader=lambda path, **kwargs: (captured.update(path=path, **kwargs), model)[1],
        token_counter=_Counter(),
        request_observer=events.append,
    )

    batch = provider.embed(["alpha beta", "gamma"], instruction="passage: ")
    query = provider.embed_one("needle text")
    documents = provider.embed_documents(["delta"], instruction="document: ")

    assert captured == {
        "path": str(snapshot.resolve()),
        "device": "cpu",
        "local_files_only": True,
        "trust_remote_code": False,
    }
    assert model.calls[0][0] == ["passage: alpha beta", "passage: gamma"]
    assert model.calls[1][0] == [module.DEFAULT_BGE_QUERY_INSTRUCTION + "needle text"]
    assert model.calls[2][0] == ["document: delta"]
    assert model.calls[0][1] == {
        "normalize_embeddings": True,
        "convert_to_numpy": True,
    }
    assert all(len(vector) == 1024 for vector in [*batch, query, *documents])
    assert all(math.isclose(sum(x * x for x in vector), 1.0) for vector in batch)
    assert provider.counters() == {
        "batch_calls": 2,
        "batch_tokens": 7,
        "query_calls": 1,
        "query_tokens": _Counter().count(
            module.DEFAULT_BGE_QUERY_INSTRUCTION + "needle text"
        ),
    }
    assert module.DEFAULT_BGE_QUERY_INSTRUCTION == (
        "Bu soruyu ilgili belge parçalarını bulmak için temsil et: "
    )
    assert [event["kind"] for event in events] == ["embedding", "query", "embedding"]
    assert [event["item_count"] for event in events] == [2, 1, 1]
    assert all(event["model"] == "BAAI/bge-m3@revision-a" for event in events)
    assert all(
        set(event)
        == {
            "ordinal",
            "kind",
            "model",
            "item_count",
            "byte_count",
            "token_count",
            "payload_sha256",
        }
        for event in events
    )
    assert "passage" not in json.dumps(events)
    assert "needle" not in json.dumps(events)
    assert len({event["payload_sha256"] for event in events}) == 3


@pytest.mark.parametrize(
    "vectors",
    [
        [[1.0] + [0.0] * 1022],
        [[float("nan")] + [0.0] * 1023],
        [[0.0] * 1024],
    ],
)
def test_bge_rejects_bad_dimension_nonfinite_and_zero_norm(tmp_path, vectors):
    module = _module()
    provider = _bge(module, _bge_snapshot(tmp_path), _EmbeddingModel(vectors))
    with pytest.raises(module.LocalBenchmarkProviderError):
        provider.embed(["text"])


def test_bge_wrong_identity_and_preload_bundle_drift_fail_closed(tmp_path):
    module = _module()
    snapshot = _bge_snapshot(tmp_path)
    loaded = False

    def loader(*_args, **_kwargs):
        nonlocal loaded
        loaded = True

    with pytest.raises(module.LocalBenchmarkProviderError):
        module.LocalBgeProvider(
            snapshot=snapshot,
            expected_model="wrong/model",
            expected_revision="revision-a",
            expected_bundle_sha256="0" * 64,
            loader=loader,
        )
    assert loaded is False


def test_bge_snapshot_drift_after_inference_fails_closed(tmp_path):
    module = _module()
    snapshot = _bge_snapshot(tmp_path)
    model = _EmbeddingModel(
        mutate=lambda: (snapshot / "tokenizer.json").write_text('{"drift":true}\n')
    )
    provider = _bge(module, snapshot, model)
    with pytest.raises(module.LocalBenchmarkProviderError, match="changed"):
        provider.embed(["text"])


def test_bge_close_releases_runtime_but_preserves_bounded_report(tmp_path):
    module = _module()
    provider = _bge(module, _bge_snapshot(tmp_path), _EmbeddingModel())
    provider.embed(["text"])
    before = provider.report()

    provider.close()
    provider.close()

    assert provider.report() == before
    with pytest.raises(module.LocalBenchmarkProviderError, match="closed"):
        provider.embed(["another"])


def test_bge_observer_failure_stops_before_model_encode(tmp_path):
    module = _module()
    model = _EmbeddingModel()

    def observer(_event):
        raise RuntimeError("observer unavailable")

    provider = _bge(
        module,
        _bge_snapshot(tmp_path),
        model,
        request_observer=observer,
    )
    with pytest.raises(module.LocalBenchmarkProviderError, match="observer"):
        provider.embed(["private text"], instruction="passage: ")
    assert model.calls == []


class _Batch(dict):
    def to(self, _device):
        return self


class _Tokenizer:
    eos_token_id = 7
    pad_token_id = 7

    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.templates = []

    def apply_chat_template(self, messages, *, tokenize, add_generation_prompt):
        assert tokenize is False and add_generation_prompt is True
        self.templates.append(messages)
        return json.dumps(messages, sort_keys=True)

    def __call__(self, prompt, *, return_tensors):
        assert return_tensors == "pt"
        return _Batch(input_ids=[[1] * max(1, len(prompt.split()))])

    def decode(self, _tokens, *, skip_special_tokens):
        assert skip_special_tokens is True
        return self.outputs.pop(0)


class _Model:
    device = "cpu"

    def __init__(self, mutate=None):
        self.mutate = mutate
        self.kwargs = []

    def generate(self, **kwargs):
        self.kwargs.append(kwargs)
        if self.mutate:
            self.mutate()
        return [list(kwargs["input_ids"][0]) + [91, 92]]


def _qwen(module, snapshot, outputs, **kwargs):
    bundle = module.generation_verifier.snapshot_bundle(snapshot)
    tokenizer = kwargs.pop("tokenizer", _Tokenizer(outputs))
    model = kwargs.pop("model_instance", _Model())
    client = module.LocalQwenClient(
        snapshot=snapshot,
        expected_model="Qwen/Qwen2.5-1.5B-Instruct",
        expected_revision="revision-a",
        expected_bundle_sha256=bundle["sha256"],
        tokenizer=tokenizer,
        model_instance=model,
        **kwargs,
    )
    return client, model, tokenizer


def test_qwen_exact_identity_determinism_observer_and_safe_report(tmp_path):
    module = _module()
    snapshot = _qwen_snapshot(tmp_path)
    events = []
    client, model, _tokenizer = _qwen(
        module, snapshot, ["answer"], request_observer=events.append
    )

    assert client.is_remote is False
    exact_identity = "Qwen/Qwen2.5-1.5B-Instruct@revision-a"
    assert client.exact_model_identity == exact_identity
    assert client.resolve_model() == exact_identity
    assert client.resolve_model(exact_identity) == exact_identity
    with pytest.raises(module.LocalBenchmarkProviderError):
        client.resolve_model("Qwen/Qwen2.5-1.5B-Instruct")
    with pytest.raises(module.LocalBenchmarkProviderError):
        client.resolve_model("Qwen/other")
    assert client.complete("system secret", "user secret") == "answer"
    assert model.kwargs[0]["do_sample"] is False
    assert model.kwargs[0]["max_new_tokens"] == client.max_output_tokens
    assert model.kwargs[0]["use_cache"] is True
    assert set(events[0]) == {
        "ordinal",
        "kind",
        "model",
        "field_names",
        "byte_counts",
        "token_counts",
        "payload_sha256",
    }
    assert "system secret" not in json.dumps(events)
    assert "user secret" not in json.dumps(events)
    report = client.report()
    assert report["model"] == exact_identity
    assert "answer" not in json.dumps(report)


def test_qwen_loaders_are_forced_offline_without_remote_code(tmp_path):
    module = _module()
    snapshot = _qwen_snapshot(tmp_path)
    bundle = module.generation_verifier.snapshot_bundle(snapshot)
    calls = []
    tokenizer = _Tokenizer(["ok"])
    model = _Model()

    client = module.LocalQwenClient(
        snapshot=snapshot,
        expected_model="Qwen/Qwen2.5-1.5B-Instruct",
        expected_revision="revision-a",
        expected_bundle_sha256=bundle["sha256"],
        tokenizer_loader=lambda path, **kwargs: (
            calls.append(("tokenizer", path, kwargs)),
            tokenizer,
        )[1],
        model_loader=lambda path, **kwargs: (
            calls.append(("model", path, kwargs)),
            model,
        )[1],
    )

    assert client.complete("s", "u") == "ok"
    assert [call[0] for call in calls] == ["tokenizer", "model"]
    for _, path, kwargs in calls:
        assert path == str(snapshot.resolve())
        assert kwargs["local_files_only"] is True
        assert kwargs["trust_remote_code"] is False


@pytest.mark.parametrize(
    ("device", "expected_dtype"), [("cpu", "auto"), ("mps", "float16-marker")]
)
def test_default_qwen_model_loader_uses_admitted_device_dtype(
    monkeypatch, device, expected_dtype
):
    module = _module()
    calls = []

    class _AutoModel:
        @staticmethod
        def from_pretrained(path, **kwargs):
            calls.append((path, kwargs))
            return object()

    fake_torch = types.ModuleType("torch")
    fake_torch.float16 = "float16-marker"
    fake_transformers = types.ModuleType("transformers")
    fake_transformers.AutoModelForCausalLM = _AutoModel
    monkeypatch.setitem(sys.modules, "torch", fake_torch)
    monkeypatch.setitem(sys.modules, "transformers", fake_transformers)

    module._default_model_loader(
        "/admitted/snapshot",
        device=device,
        local_files_only=True,
        trust_remote_code=False,
    )

    assert calls == [
        (
            "/admitted/snapshot",
            {
                "local_files_only": True,
                "trust_remote_code": False,
                "dtype": expected_dtype,
            },
        )
    ]


def test_qwen_structured_repairs_once_and_returns_only_json_object(tmp_path):
    module = _module()
    client, _model, _tokenizer = _qwen(
        module,
        _qwen_snapshot(tmp_path),
        ["not json TOP_SECRET", '```json\n{"answer":"ok"}\n```'],
    )

    result = client.complete_structured("system", "user", schema={"type": "object"})

    assert result == {"answer": "ok"}
    assert client.generation_calls == 1
    assert client.repair_calls == 1
    assert "TOP_SECRET" not in json.dumps(client.report())


def test_qwen_malformed_json_after_repair_fails_without_retaining_raw(tmp_path):
    module = _module()
    client, _model, _tokenizer = _qwen(
        module, _qwen_snapshot(tmp_path), ["TOP_SECRET {", "still malformed"]
    )

    with pytest.raises(module.LocalBenchmarkProviderError, match="JSON object") as exc:
        client.complete_structured("s", "u", schema={"type": "object"})

    assert "TOP_SECRET" not in str(exc.value)
    assert "TOP_SECRET" not in json.dumps(client.report())
    assert client.generation_calls == 1
    assert client.repair_calls == 1


@pytest.mark.parametrize(
    ("kwargs", "system", "user"),
    [
        ({"max_prompt_tokens": 2}, "many system tokens", "many user tokens"),
        ({"max_context_tokens": 4, "max_output_tokens": 3}, "s", "u"),
        ({"max_prompt_bytes": 3}, "long", "u"),
    ],
)
def test_qwen_prompt_output_and_context_bounds_fail_closed(
    tmp_path, kwargs, system, user
):
    module = _module()
    client, model, _tokenizer = _qwen(
        module, _qwen_snapshot(tmp_path), ["never"], **kwargs
    )
    with pytest.raises(module.LocalBenchmarkProviderError, match="limit|context"):
        client.complete(system, user)
    assert model.kwargs == []


def test_qwen_snapshot_drift_after_generation_fails_closed(tmp_path):
    module = _module()
    snapshot = _qwen_snapshot(tmp_path)
    model = _Model(
        mutate=lambda: (snapshot / "tokenizer.json").write_text('{"drift":true}\n')
    )
    client, _model, _tokenizer = _qwen(
        module, snapshot, ["unknown raw"], model_instance=model
    )
    with pytest.raises(module.LocalBenchmarkProviderError, match="changed"):
        client.complete("s", "u")


def test_qwen_close_releases_runtime_but_preserves_bounded_report(tmp_path):
    module = _module()
    client, _model, _tokenizer = _qwen(module, _qwen_snapshot(tmp_path), ["answer"])
    client.complete("s", "u")
    before = client.report()

    client.close()
    client.close()

    assert client.report() == before
    with pytest.raises(module.LocalBenchmarkProviderError, match="closed"):
        client.complete("s", "u")
