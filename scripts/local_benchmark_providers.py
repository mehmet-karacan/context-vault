#!/usr/bin/env python3
"""Fail-closed, offline model adapters for the local A9 benchmark runner.

This module deliberately contains no runner, database, object-store or network
integration.  It turns snapshots already admitted by the two runtime verifiers
into the small embedding and generation interfaces used by a benchmark.  Heavy
runtime objects and their loaders are injectable so the contract is unit-testable
without loading model weights.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import os
import re
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence


SCRIPT_DIR = Path(__file__).resolve().parent
EMBEDDING_DIMENSION = 1024
DEFAULT_BGE_QUERY_INSTRUCTION = (
    "Bu soruyu ilgili belge parçalarını bulmak için temsil et: "
)
DEFAULT_MAX_PROMPT_BYTES = 32_768
DEFAULT_MAX_PROMPT_TOKENS = 3_584
DEFAULT_MAX_OUTPUT_BYTES = 32_768
DEFAULT_MAX_OUTPUT_TOKENS = 512
DEFAULT_MAX_CONTEXT_BYTES = 24_576
DEFAULT_MAX_CONTEXT_TOKENS = 4_096
HARD_MAX_PROMPT_BYTES = 131_072
HARD_MAX_PROMPT_TOKENS = 4_096
HARD_MAX_OUTPUT_BYTES = 131_072
HARD_MAX_OUTPUT_TOKENS = 1_024
HARD_MAX_CONTEXT_BYTES = 262_144
HARD_MAX_CONTEXT_TOKENS = 8_192
_SHA256_RE = re.compile(r"[a-f0-9]{64}")


class LocalBenchmarkProviderError(RuntimeError):
    """A local adapter input, snapshot, runtime or output failed its contract."""


LocalBgeProviderError = LocalBenchmarkProviderError
LocalQwenClientError = LocalBenchmarkProviderError


def _load_verifier(name: str, filename: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, SCRIPT_DIR / filename)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"local verifier {filename} is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


bge_verifier = _load_verifier("cv_benchmark_verify_local_bge", "verify_local_bge.py")
generation_verifier = _load_verifier(
    "cv_benchmark_verify_local_generation", "verify_local_generation.py"
)


def _load_structured_prompt_contract() -> Any:
    path = (
        SCRIPT_DIR.parent
        / "document-rag-platform/services/backend/src/application"
        / "structured_prompt_contract.py"
    )
    spec = importlib.util.spec_from_file_location(
        "cv_local_structured_prompt_contract", path
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("local structured prompt contract is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


structured_prompt_contract = _load_structured_prompt_contract()


def _offline_environment() -> None:
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["TOKENIZERS_PARALLELISM"] = "false"


class _SnapshotGuard:
    def __init__(
        self,
        *,
        verifier: Any,
        snapshot: Path,
        expected_model: str,
        expected_revision: str,
        expected_bundle_sha256: str,
    ) -> None:
        if not isinstance(expected_bundle_sha256, str) or not _SHA256_RE.fullmatch(
            expected_bundle_sha256
        ):
            raise LocalBenchmarkProviderError("expected bundle SHA-256 is invalid")
        self._verifier = verifier
        self.snapshot = Path(snapshot)
        self.model = expected_model
        self.revision = expected_revision
        self.bundle_sha256 = expected_bundle_sha256
        self.bundle = self.verify()
        self.metadata_sha256 = self.metadata_fingerprint()

    def verify(self) -> dict[str, Any]:
        try:
            bundle = self._verifier.validate_snapshot(
                self.snapshot,
                expected_model=self.model,
                expected_revision=self.revision,
            )
        except Exception as exc:  # noqa: BLE001 - verifier boundary
            raise LocalBenchmarkProviderError(
                "local model snapshot identity or structure is invalid"
            ) from exc
        if not isinstance(bundle, dict) or bundle.get("sha256") != self.bundle_sha256:
            raise LocalBenchmarkProviderError("local model bundle SHA-256 mismatch")
        return dict(bundle)

    def verify_unchanged(self, before: Mapping[str, Any]) -> None:
        try:
            after = self.verify()
        except LocalBenchmarkProviderError as exc:
            raise LocalBenchmarkProviderError(
                "local model snapshot changed during inference"
            ) from exc
        if after != dict(before):
            raise LocalBenchmarkProviderError(
                "local model snapshot changed during inference"
            )

    def metadata_fingerprint(self) -> str:
        digest = hashlib.sha256(b"context-vault/snapshot-metadata/v1\0")
        try:
            paths = sorted(
                self.snapshot.rglob("*"),
                key=lambda path: path.relative_to(self.snapshot).as_posix(),
            )
            for path in paths:
                relative = path.relative_to(self.snapshot).as_posix()
                stat = path.lstat()
                digest.update(relative.encode("utf-8"))
                digest.update(b"\0")
                digest.update(
                    f"{stat.st_mode}:{stat.st_size}:{stat.st_mtime_ns}:{stat.st_ino}".encode()
                )
                digest.update(b"\0")
        except OSError as exc:
            raise LocalBenchmarkProviderError(
                "local model snapshot metadata is unavailable"
            ) from exc
        return digest.hexdigest()

    def verify_metadata_unchanged(self, before: str) -> None:
        after = self.metadata_fingerprint()
        if before != self.metadata_sha256 or after != before:
            raise LocalBenchmarkProviderError(
                "local model snapshot changed during inference"
            )


def _default_token_count(text: str) -> int:
    if not text:
        return 0
    return max(1, (len(text.encode("utf-8")) + 3) // 4)


def _counter_value(counter: Any, text: str) -> int:
    try:
        value = (
            counter.count(text) if counter is not None else _default_token_count(text)
        )
    except Exception as exc:  # noqa: BLE001 - injected counter boundary
        raise LocalBenchmarkProviderError("token counter failed") from exc
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise LocalBenchmarkProviderError("token counter returned an invalid count")
    return value


def _default_bge_loader(path: str, **kwargs: Any) -> Any:
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as exc:
        raise LocalBenchmarkProviderError(
            "local BGE runtime is unavailable; install the local-eval extra"
        ) from exc
    return SentenceTransformer(path, **kwargs)


class LocalBgeProvider:
    """Exact-snapshot BGE-M3 adapter with separate document/query accounting."""

    is_remote = False

    def __init__(
        self,
        *,
        snapshot: Path,
        expected_model: str,
        expected_revision: str,
        expected_bundle_sha256: str,
        device: str = "cpu",
        query_instruction: str = DEFAULT_BGE_QUERY_INSTRUCTION,
        loader: Callable[..., Any] | None = None,
        model_instance: Any | None = None,
        model: Any | None = None,
        token_counter: Any | None = None,
        request_observer: Callable[[dict[str, Any]], None] | None = None,
        request_capture: Callable[[dict[str, Any], Any], str] | None = None,
    ) -> None:
        if device not in {"cpu", "mps"}:
            raise LocalBenchmarkProviderError("device must be cpu or mps")
        if not isinstance(query_instruction, str) or not query_instruction.strip():
            raise LocalBenchmarkProviderError("query instruction must be non-empty")
        if model_instance is not None and model is not None:
            raise LocalBenchmarkProviderError("only one injected BGE model is allowed")
        injected_model = model_instance if model_instance is not None else model
        if injected_model is not None and loader is not None:
            raise LocalBenchmarkProviderError(
                "BGE model and loader are mutually exclusive"
            )
        if request_observer is not None and not callable(request_observer):
            raise LocalBenchmarkProviderError("request observer must be callable")
        if request_capture is not None and not callable(request_capture):
            raise LocalBenchmarkProviderError("request capture must be callable")

        self._guard = _SnapshotGuard(
            verifier=bge_verifier,
            snapshot=snapshot,
            expected_model=expected_model,
            expected_revision=expected_revision,
            expected_bundle_sha256=expected_bundle_sha256,
        )
        self.model_name = expected_model
        self.model_revision = expected_revision
        self.model_bundle_sha256 = expected_bundle_sha256
        self.exact_model_identity = f"{expected_model}@{expected_revision}"
        self.device = device
        self.query_instruction = query_instruction
        self._token_counter = token_counter
        self._request_observer = request_observer
        self._request_capture = request_capture
        self._request_ordinal = 0
        self.batch_calls = 0
        self.batch_tokens = 0
        self.query_calls = 0
        self.query_tokens = 0

        before = self._guard.metadata_fingerprint()
        _offline_environment()
        if injected_model is None:
            selected_loader = loader or _default_bge_loader
            try:
                injected_model = selected_loader(
                    str(self._guard.snapshot.resolve(strict=True)),
                    device=device,
                    local_files_only=True,
                    trust_remote_code=False,
                )
            except LocalBenchmarkProviderError:
                raise
            except Exception as exc:  # noqa: BLE001 - local loader boundary
                raise LocalBenchmarkProviderError(
                    "local BGE model failed to load offline"
                ) from exc
        self._guard.verify_metadata_unchanged(before)
        if not callable(getattr(injected_model, "encode", None)):
            raise LocalBenchmarkProviderError("local BGE model lacks encode")
        self._model = injected_model
        self._closed = False

    def _validated_texts(self, texts: Sequence[str]) -> list[str]:
        if isinstance(texts, (str, bytes)) or not isinstance(texts, Sequence):
            raise LocalBenchmarkProviderError("embedding input must be a text sequence")
        values = list(texts)
        if not values or any(
            not isinstance(text, str) or not text.strip() for text in values
        ):
            raise LocalBenchmarkProviderError(
                "embedding input requires non-empty text values"
            )
        return values

    def _observe_encode(self, *, texts: list[str], kind: str, token_count: int) -> None:
        self._request_ordinal += 1
        canonical = json.dumps(texts, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
        event = {
            "ordinal": self._request_ordinal,
            "kind": kind,
            "model": self.exact_model_identity,
            "item_count": len(texts),
            "byte_count": sum(len(text.encode("utf-8")) for text in texts),
            "token_count": token_count,
            "payload_sha256": hashlib.sha256(canonical).hexdigest(),
        }
        if self._request_capture is not None:
            try:
                capture_sha256 = self._request_capture(event, texts)
            except Exception as exc:  # noqa: BLE001 - independent capture boundary
                raise LocalBenchmarkProviderError("request capture failed") from exc
            if not isinstance(capture_sha256, str) or not _SHA256_RE.fullmatch(
                capture_sha256
            ):
                raise LocalBenchmarkProviderError(
                    "request capture acknowledgement failed"
                )
            event = {**event, "capture_sha256": capture_sha256}
        if self._request_observer is not None:
            try:
                self._request_observer(event)
            except Exception as exc:  # noqa: BLE001 - observer boundary
                raise LocalBenchmarkProviderError("request observer failed") from exc

    def _encode(
        self, texts: list[str], *, kind: str, token_count: int
    ) -> list[list[float]]:
        if self._closed:
            raise LocalBenchmarkProviderError("local BGE provider is closed")
        before = self._guard.metadata_fingerprint()
        self._observe_encode(texts=texts, kind=kind, token_count=token_count)
        error: Exception | None = None
        raw_vectors: Any = None
        try:
            raw_vectors = self._model.encode(
                texts,
                normalize_embeddings=True,
                convert_to_numpy=True,
            )
        except Exception as exc:  # noqa: BLE001 - local inference boundary
            error = exc
        self._guard.verify_metadata_unchanged(before)
        if error is not None:
            raise LocalBenchmarkProviderError("local BGE inference failed") from error
        return self._normalize_vectors(raw_vectors, expected_count=len(texts))

    @staticmethod
    def _normalize_vectors(
        raw_vectors: Any, *, expected_count: int
    ) -> list[list[float]]:
        if hasattr(raw_vectors, "tolist"):
            raw_vectors = raw_vectors.tolist()
        if (
            not isinstance(raw_vectors, (list, tuple))
            or len(raw_vectors) != expected_count
        ):
            raise LocalBenchmarkProviderError("local BGE output shape is invalid")
        normalized: list[list[float]] = []
        for raw_vector in raw_vectors:
            if hasattr(raw_vector, "tolist"):
                raw_vector = raw_vector.tolist()
            if (
                not isinstance(raw_vector, (list, tuple))
                or len(raw_vector) != EMBEDDING_DIMENSION
            ):
                raise LocalBenchmarkProviderError(
                    "local BGE output dimension is invalid"
                )
            vector: list[float] = []
            for value in raw_vector:
                if not isinstance(value, (int, float)) or isinstance(value, bool):
                    raise LocalBenchmarkProviderError(
                        "local BGE output value is invalid"
                    )
                converted = float(value)
                if not math.isfinite(converted):
                    raise LocalBenchmarkProviderError("local BGE output is not finite")
                vector.append(converted)
            norm = math.sqrt(math.fsum(value * value for value in vector))
            if not math.isfinite(norm) or norm <= 0:
                raise LocalBenchmarkProviderError("local BGE output norm is invalid")
            unit = [value / norm for value in vector]
            if not math.isclose(
                math.sqrt(math.fsum(value * value for value in unit)),
                1.0,
                abs_tol=1e-6,
            ):
                raise LocalBenchmarkProviderError(
                    "local BGE output normalization failed"
                )
            normalized.append(unit)
        return normalized

    def embed(self, texts: list[str], *, instruction: str = "") -> list[list[float]]:
        values = self._validated_texts(texts)
        if not isinstance(instruction, str):
            raise LocalBenchmarkProviderError("embedding instruction must be text")
        instructed = [instruction + text for text in values]
        tokens = sum(_counter_value(self._token_counter, text) for text in instructed)
        self.batch_calls += 1
        self.batch_tokens += tokens
        return self._encode(instructed, kind="embedding", token_count=tokens)

    def embed_documents(
        self, texts: list[str], *, instruction: str = ""
    ) -> list[list[float]]:
        return self.embed(texts, instruction=instruction)

    def embed_one(self, text: str) -> list[float]:
        if not isinstance(text, str) or not text.strip():
            raise LocalBenchmarkProviderError("query text must be non-empty")
        instructed = self.query_instruction + text
        tokens = _counter_value(self._token_counter, instructed)
        self.query_calls += 1
        self.query_tokens += tokens
        return self._encode([instructed], kind="query", token_count=tokens)[0]

    def embed_query(self, text: str) -> list[float]:
        return self.embed_one(text)

    def counters(self) -> dict[str, int]:
        return {
            "batch_calls": self.batch_calls,
            "batch_tokens": self.batch_tokens,
            "query_calls": self.query_calls,
            "query_tokens": self.query_tokens,
        }

    def close(self) -> None:
        """Release model references before golden labels become readable."""

        self._model = None
        self._closed = True

    def report(self) -> dict[str, Any]:
        return {
            "is_remote": False,
            "model": self.exact_model_identity,
            "model_revision": self.model_revision,
            "model_bundle_sha256": self.model_bundle_sha256,
            "embedding_dimension": EMBEDDING_DIMENSION,
            "counters": self.counters(),
        }


def _default_tokenizer_loader(path: str, **kwargs: Any) -> Any:
    try:
        from transformers import AutoTokenizer
    except ImportError as exc:
        raise LocalBenchmarkProviderError(
            "local Qwen runtime is unavailable; install the local-eval extra"
        ) from exc
    return AutoTokenizer.from_pretrained(path, **kwargs)


def _default_model_loader(path: str, **kwargs: Any) -> Any:
    try:
        import torch
        from transformers import AutoModelForCausalLM
    except ImportError as exc:
        raise LocalBenchmarkProviderError(
            "local Qwen runtime is unavailable; install the local-eval extra"
        ) from exc
    device = kwargs.pop("device", None)
    if device not in {"cpu", "mps"}:
        raise LocalBenchmarkProviderError("local Qwen loader device is invalid")
    kwargs["dtype"] = torch.float16 if device == "mps" else "auto"
    return AutoModelForCausalLM.from_pretrained(path, **kwargs)


def _positive_bound(name: str, value: int, hard_limit: int) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or value <= 0
        or value > hard_limit
    ):
        raise LocalBenchmarkProviderError(f"{name} is outside its hard limit")
    return value


def _sequence_length(value: Any) -> int:
    shape = getattr(value, "shape", None)
    if shape is not None:
        try:
            return int(shape[-1])
        except (IndexError, TypeError, ValueError) as exc:
            raise LocalBenchmarkProviderError("token shape is invalid") from exc
    try:
        first = value[0]
        return len(first)
    except (IndexError, KeyError, TypeError) as exc:
        raise LocalBenchmarkProviderError("token sequence is invalid") from exc


def _strict_object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object field")
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> None:
    raise ValueError("non-standard JSON numeric constant")


class LocalQwenClient:
    """Deterministic exact-snapshot Qwen client for bounded local evaluation."""

    is_remote = False

    def __init__(
        self,
        *,
        snapshot: Path,
        expected_model: str,
        expected_revision: str,
        expected_bundle_sha256: str,
        device: str = "cpu",
        max_prompt_bytes: int = DEFAULT_MAX_PROMPT_BYTES,
        max_prompt_tokens: int = DEFAULT_MAX_PROMPT_TOKENS,
        max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
        max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
        max_context_bytes: int = DEFAULT_MAX_CONTEXT_BYTES,
        max_context_tokens: int = DEFAULT_MAX_CONTEXT_TOKENS,
        loader: Callable[..., Any] | None = None,
        tokenizer_loader: Callable[..., Any] | None = None,
        model_loader: Callable[..., Any] | None = None,
        tokenizer: Any | None = None,
        model_instance: Any | None = None,
        model: Any | None = None,
        request_observer: Callable[[dict[str, Any]], None] | None = None,
        request_capture: Callable[[dict[str, Any], Any], str] | None = None,
    ) -> None:
        if device not in {"cpu", "mps"}:
            raise LocalBenchmarkProviderError("device must be cpu or mps")
        self.max_prompt_bytes = _positive_bound(
            "max_prompt_bytes", max_prompt_bytes, HARD_MAX_PROMPT_BYTES
        )
        self.max_prompt_tokens = _positive_bound(
            "max_prompt_tokens", max_prompt_tokens, HARD_MAX_PROMPT_TOKENS
        )
        self.max_output_bytes = _positive_bound(
            "max_output_bytes", max_output_bytes, HARD_MAX_OUTPUT_BYTES
        )
        self.max_output_tokens = _positive_bound(
            "max_output_tokens", max_output_tokens, HARD_MAX_OUTPUT_TOKENS
        )
        self.max_context_bytes = _positive_bound(
            "max_context_bytes", max_context_bytes, HARD_MAX_CONTEXT_BYTES
        )
        self.max_context_tokens = _positive_bound(
            "max_context_tokens", max_context_tokens, HARD_MAX_CONTEXT_TOKENS
        )
        if request_observer is not None and not callable(request_observer):
            raise LocalBenchmarkProviderError("request observer must be callable")
        if request_capture is not None and not callable(request_capture):
            raise LocalBenchmarkProviderError("request capture must be callable")
        injected_model = model_instance if model_instance is not None else model
        if model_instance is not None and model is not None:
            raise LocalBenchmarkProviderError("only one injected Qwen model is allowed")
        if loader is not None and any(
            value is not None
            for value in (tokenizer_loader, model_loader, tokenizer, injected_model)
        ):
            raise LocalBenchmarkProviderError(
                "combined Qwen loader cannot be mixed with component injection"
            )
        if (tokenizer is None) != (injected_model is None) and (
            tokenizer_loader is None or model_loader is None
        ):
            raise LocalBenchmarkProviderError(
                "Qwen tokenizer and model must both be supplied or loadable"
            )

        self._guard = _SnapshotGuard(
            verifier=generation_verifier,
            snapshot=snapshot,
            expected_model=expected_model,
            expected_revision=expected_revision,
            expected_bundle_sha256=expected_bundle_sha256,
        )
        self.model_name = expected_model
        self.model_revision = expected_revision
        self.model_bundle_sha256 = expected_bundle_sha256
        self.exact_model_identity = f"{expected_model}@{expected_revision}"
        self.device = device
        self._request_observer = request_observer
        self._request_capture = request_capture
        self._request_ordinal = 0
        self.generation_calls = 0
        self.repair_calls = 0
        self.prompt_tokens = 0
        self.generated_tokens = 0

        before = self._guard.metadata_fingerprint()
        _offline_environment()
        path = str(self._guard.snapshot.resolve(strict=True))
        load_kwargs = {"local_files_only": True, "trust_remote_code": False}
        try:
            if loader is not None:
                loaded = loader(path, device=device, **load_kwargs)
                if isinstance(loaded, Mapping):
                    tokenizer = loaded.get("tokenizer")
                    injected_model = loaded.get("model")
                else:
                    injected_model, tokenizer = loaded
            else:
                if tokenizer is None:
                    tokenizer = (tokenizer_loader or _default_tokenizer_loader)(
                        path, **load_kwargs
                    )
                if injected_model is None:
                    selected_model_loader = model_loader or _default_model_loader
                    model_load_kwargs = dict(load_kwargs)
                    if model_loader is None:
                        model_load_kwargs["device"] = device
                    injected_model = selected_model_loader(path, **model_load_kwargs)
        except LocalBenchmarkProviderError:
            raise
        except Exception as exc:  # noqa: BLE001 - local loader boundary
            raise LocalBenchmarkProviderError(
                "local Qwen model failed to load offline"
            ) from exc
        if not callable(getattr(tokenizer, "apply_chat_template", None)):
            raise LocalBenchmarkProviderError("local Qwen tokenizer is invalid")
        if not callable(getattr(tokenizer, "decode", None)):
            raise LocalBenchmarkProviderError("local Qwen tokenizer lacks decode")
        if not callable(getattr(injected_model, "generate", None)):
            raise LocalBenchmarkProviderError("local Qwen model lacks generate")
        self._tokenizer = tokenizer
        self._model = injected_model
        self._closed = False
        if callable(getattr(self._model, "to", None)):
            try:
                self._model.to(device)
            except Exception as exc:  # noqa: BLE001 - local runtime boundary
                raise LocalBenchmarkProviderError(
                    "local Qwen device setup failed"
                ) from exc
        if callable(getattr(self._model, "eval", None)):
            self._model.eval()
        self._guard.verify_metadata_unchanged(before)

    def resolve_model(self, requested: str | None = None) -> str:
        if requested is None or requested == self.exact_model_identity:
            return self.exact_model_identity
        raise LocalBenchmarkProviderError(
            "requested model is not the exact approved model"
        )

    @staticmethod
    def _text(name: str, value: Any) -> str:
        if not isinstance(value, str) or not value.strip():
            raise LocalBenchmarkProviderError(f"{name} must be non-empty text")
        return value

    def _payload_bytes(self, fields: Mapping[str, str]) -> dict[str, int]:
        counts = {name: len(value.encode("utf-8")) for name, value in fields.items()}
        if sum(counts.values()) > self.max_prompt_bytes:
            raise LocalBenchmarkProviderError("prompt byte limit exceeded")
        return counts

    def _encode_prompt(self, messages: list[dict[str, str]]) -> tuple[Any, int, str]:
        if self._closed:
            raise LocalBenchmarkProviderError("local Qwen client is closed")
        try:
            prompt = self._tokenizer.apply_chat_template(
                messages, tokenize=False, add_generation_prompt=True
            )
            if not isinstance(prompt, str):
                raise TypeError("chat template did not return text")
            encoded = self._tokenizer(prompt, return_tensors="pt")
            if callable(getattr(encoded, "to", None)):
                encoded = encoded.to(self.device)
            input_ids = encoded["input_ids"]
            prompt_tokens = _sequence_length(input_ids)
        except LocalBenchmarkProviderError:
            raise
        except Exception as exc:  # noqa: BLE001 - tokenizer boundary
            raise LocalBenchmarkProviderError("local Qwen tokenization failed") from exc
        if prompt_tokens <= 0 or prompt_tokens > self.max_prompt_tokens:
            raise LocalBenchmarkProviderError("prompt token limit exceeded")
        if prompt_tokens + self.max_output_tokens > self.max_context_tokens:
            raise LocalBenchmarkProviderError("request exceeds model context limit")
        return encoded, prompt_tokens, prompt

    def _observe(
        self,
        *,
        kind: str,
        fields: Mapping[str, str],
        byte_counts: Mapping[str, int],
        prompt_tokens: int,
    ) -> None:
        self._request_ordinal += 1
        canonical = json.dumps(
            dict(fields), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        event = {
            "ordinal": self._request_ordinal,
            "kind": kind,
            "model": self.exact_model_identity,
            "field_names": sorted(fields),
            "byte_counts": {name: byte_counts[name] for name in sorted(byte_counts)},
            "token_counts": {
                "prompt": prompt_tokens,
                "maximum_output": self.max_output_tokens,
            },
            "payload_sha256": hashlib.sha256(canonical).hexdigest(),
        }
        if self._request_capture is not None:
            try:
                capture_sha256 = self._request_capture(event, dict(fields))
            except Exception as exc:  # noqa: BLE001 - independent capture boundary
                raise LocalBenchmarkProviderError("request capture failed") from exc
            if not isinstance(capture_sha256, str) or not _SHA256_RE.fullmatch(
                capture_sha256
            ):
                raise LocalBenchmarkProviderError(
                    "request capture acknowledgement failed"
                )
            event = {**event, "capture_sha256": capture_sha256}
        if self._request_observer is not None:
            try:
                self._request_observer(event)
            except Exception as exc:  # noqa: BLE001 - observer boundary
                raise LocalBenchmarkProviderError("request observer failed") from exc

    def _generate_request(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        model: str | None,
        kind: str,
    ) -> str:
        selected_model = self.resolve_model(model)
        system = self._text("system prompt", system_prompt)
        user = self._text("user prompt", user_prompt)
        fields = {"system": system, "user": user}
        byte_counts = self._payload_bytes(fields)
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        encoded, prompt_tokens, _prompt = self._encode_prompt(messages)
        self._observe(
            kind=kind,
            fields=fields,
            byte_counts=byte_counts,
            prompt_tokens=prompt_tokens,
        )
        if kind == "repair":
            self.repair_calls += 1
        else:
            self.generation_calls += 1
        self.prompt_tokens += prompt_tokens

        before = self._guard.metadata_fingerprint()
        error: Exception | None = None
        text = ""
        generated_count = 0
        try:
            import torch

            generate_kwargs = dict(encoded)
            pad_token_id = getattr(self._tokenizer, "pad_token_id", None)
            if pad_token_id is None:
                pad_token_id = getattr(self._tokenizer, "eos_token_id", None)
            generate_kwargs.update(
                {
                    "max_new_tokens": self.max_output_tokens,
                    "do_sample": False,
                    "pad_token_id": pad_token_id,
                    "use_cache": True,
                }
            )
            with torch.inference_mode():
                outputs = self._model.generate(**generate_kwargs)
            generated = outputs[0][prompt_tokens:]
            generated_count = len(generated)
            if generated_count > self.max_output_tokens:
                raise LocalBenchmarkProviderError("generated token limit exceeded")
            text = self._tokenizer.decode(generated, skip_special_tokens=True).strip()
            if len(text.encode("utf-8")) > self.max_output_bytes:
                raise LocalBenchmarkProviderError("generated byte limit exceeded")
        except Exception as exc:  # noqa: BLE001 - local generation boundary
            error = exc
        self._guard.verify_metadata_unchanged(before)
        if error is not None:
            if isinstance(error, LocalBenchmarkProviderError):
                raise error
            raise LocalBenchmarkProviderError("local Qwen generation failed") from error
        self.generated_tokens += generated_count
        if not isinstance(selected_model, str):  # defensive unreachable identity check
            raise LocalBenchmarkProviderError("resolved model identity is invalid")
        return text

    def generate(
        self,
        system_prompt: str,
        user_prompt: str | None = None,
        *,
        model: str | None = None,
    ) -> str:
        if user_prompt is None:
            user_prompt = system_prompt
            system_prompt = "Answer the user request using only the supplied input."
        return self._generate_request(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            model=model,
            kind="generation",
        )

    def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        model: str | None = None,
    ) -> str:
        return self.generate(system_prompt, user_prompt, model=model)

    def generate_answer(
        self,
        query: str,
        context_chunks: list[str],
        model: str | None = None,
    ) -> str:
        query = self._text("query", query)
        if not isinstance(context_chunks, list) or any(
            not isinstance(chunk, str) or not chunk.strip() for chunk in context_chunks
        ):
            raise LocalBenchmarkProviderError("context chunks must be non-empty text")
        context = "\n\n---\n\n".join(context_chunks)
        if len(context.encode("utf-8")) > self.max_context_bytes:
            raise LocalBenchmarkProviderError("context byte limit exceeded")
        system = "Answer only from CONTEXT. Return NO_CONTEXT when it is insufficient."
        user = f"CONTEXT:\n{context}\n\nQUESTION:\n{query}" if context else query
        return self.generate(system, user, model=model)

    @staticmethod
    def _json_object(raw: str) -> dict[str, Any]:
        candidate = raw.strip()
        fence = re.fullmatch(r"```(?:json)?\s*([\s\S]*?)\s*```", candidate)
        if fence:
            candidate = fence.group(1).strip()
        try:
            parsed = json.loads(
                candidate,
                object_pairs_hook=_strict_object_pairs,
                parse_constant=_reject_json_constant,
            )
        except (TypeError, ValueError):
            raise LocalBenchmarkProviderError(
                "structured generation did not return a strict JSON object"
            ) from None
        if not isinstance(parsed, dict):
            raise LocalBenchmarkProviderError(
                "structured generation did not return a strict JSON object"
            )
        return parsed

    @staticmethod
    def _validate_schema(instance: dict[str, Any], schema: dict[str, Any]) -> None:
        try:
            from jsonschema import Draft202012Validator

            Draft202012Validator.check_schema(schema)
            Draft202012Validator(schema).validate(instance)
        except ImportError as exc:
            raise LocalBenchmarkProviderError(
                "JSON schema runtime is unavailable"
            ) from exc
        except Exception as exc:  # noqa: BLE001 - schema implementation boundary
            raise LocalBenchmarkProviderError(
                "structured generation did not satisfy the JSON schema"
            ) from exc

    def complete_structured(
        self,
        system: str,
        user: str,
        *,
        schema: dict[str, Any],
        model: str | None = None,
    ) -> dict[str, Any]:
        if not isinstance(schema, dict) or not schema:
            raise LocalBenchmarkProviderError("structured schema must be an object")
        try:
            structured_system, structured_user = (
                structured_prompt_contract.local_structured_prompts(
                    self._text("system prompt", system),
                    self._text("user prompt", user),
                    schema,
                )
            )
        except (TypeError, ValueError) as exc:
            raise LocalBenchmarkProviderError("structured schema is not JSON") from exc
        raw = self._generate_request(
            system_prompt=structured_system,
            user_prompt=structured_user,
            model=model,
            kind="generation",
        )
        try:
            parsed = self._json_object(raw)
            self._validate_schema(parsed, schema)
            return parsed
        except LocalBenchmarkProviderError:
            del raw
            repair_system, repair_user = (
                structured_prompt_contract.local_structured_prompts(
                    self._text("system prompt", system),
                    structured_user,
                    schema,
                    internal_repair=True,
                )
            )
            repaired = self._generate_request(
                system_prompt=repair_system,
                user_prompt=repair_user,
                model=model,
                kind="repair",
            )
            parsed = self._json_object(repaired)
            self._validate_schema(parsed, schema)
            return parsed

    def counters(self) -> dict[str, int]:
        return {
            "generation_calls": self.generation_calls,
            "repair_calls": self.repair_calls,
            "prompt_tokens": self.prompt_tokens,
            "generated_tokens": self.generated_tokens,
        }

    def close(self) -> None:
        """Release tokenizer/model references before labels are parsed."""

        self._model = None
        self._tokenizer = None
        self._closed = True

    def report(self) -> dict[str, Any]:
        return {
            "is_remote": False,
            "model": self.exact_model_identity,
            "model_revision": self.model_revision,
            "model_bundle_sha256": self.model_bundle_sha256,
            "deterministic_generation": True,
            "counters": self.counters(),
        }


class DeferredLocalQwenClient:
    """Admit an exact Qwen snapshot now and load its runtime on first use."""

    is_remote = False

    def __init__(
        self,
        *,
        snapshot: Path,
        expected_model: str,
        expected_revision: str,
        expected_bundle_sha256: str,
        device: str = "cpu",
        max_prompt_bytes: int = DEFAULT_MAX_PROMPT_BYTES,
        max_prompt_tokens: int = DEFAULT_MAX_PROMPT_TOKENS,
        max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
        max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
        max_context_bytes: int = DEFAULT_MAX_CONTEXT_BYTES,
        max_context_tokens: int = DEFAULT_MAX_CONTEXT_TOKENS,
        loader: Callable[..., Any] | None = None,
        tokenizer_loader: Callable[..., Any] | None = None,
        model_loader: Callable[..., Any] | None = None,
        tokenizer: Any | None = None,
        model_instance: Any | None = None,
        model: Any | None = None,
        request_observer: Callable[[dict[str, Any]], None] | None = None,
        request_capture: Callable[[dict[str, Any], Any], str] | None = None,
    ) -> None:
        if device not in {"cpu", "mps"}:
            raise LocalBenchmarkProviderError("device must be cpu or mps")
        bounds = {
            "max_prompt_bytes": _positive_bound(
                "max_prompt_bytes", max_prompt_bytes, HARD_MAX_PROMPT_BYTES
            ),
            "max_prompt_tokens": _positive_bound(
                "max_prompt_tokens", max_prompt_tokens, HARD_MAX_PROMPT_TOKENS
            ),
            "max_output_bytes": _positive_bound(
                "max_output_bytes", max_output_bytes, HARD_MAX_OUTPUT_BYTES
            ),
            "max_output_tokens": _positive_bound(
                "max_output_tokens", max_output_tokens, HARD_MAX_OUTPUT_TOKENS
            ),
            "max_context_bytes": _positive_bound(
                "max_context_bytes", max_context_bytes, HARD_MAX_CONTEXT_BYTES
            ),
            "max_context_tokens": _positive_bound(
                "max_context_tokens", max_context_tokens, HARD_MAX_CONTEXT_TOKENS
            ),
        }
        if request_observer is not None and not callable(request_observer):
            raise LocalBenchmarkProviderError("request observer must be callable")
        if request_capture is not None and not callable(request_capture):
            raise LocalBenchmarkProviderError("request capture must be callable")
        injected_model = model_instance if model_instance is not None else model
        if model_instance is not None and model is not None:
            raise LocalBenchmarkProviderError("only one injected Qwen model is allowed")
        if loader is not None and any(
            value is not None
            for value in (tokenizer_loader, model_loader, tokenizer, injected_model)
        ):
            raise LocalBenchmarkProviderError(
                "combined Qwen loader cannot be mixed with component injection"
            )
        if (tokenizer is None) != (injected_model is None) and (
            tokenizer_loader is None or model_loader is None
        ):
            raise LocalBenchmarkProviderError(
                "Qwen tokenizer and model must both be supplied or loadable"
            )

        self._guard = _SnapshotGuard(
            verifier=generation_verifier,
            snapshot=snapshot,
            expected_model=expected_model,
            expected_revision=expected_revision,
            expected_bundle_sha256=expected_bundle_sha256,
        )
        self.model_name = expected_model
        self.model_revision = expected_revision
        self.model_bundle_sha256 = expected_bundle_sha256
        self.exact_model_identity = f"{expected_model}@{expected_revision}"
        self.device = device
        self._delegate: LocalQwenClient | None = None
        self._closed = False
        self._load_kwargs = {
            "snapshot": snapshot,
            "expected_model": expected_model,
            "expected_revision": expected_revision,
            "expected_bundle_sha256": expected_bundle_sha256,
            "device": device,
            **bounds,
            "loader": loader,
            "tokenizer_loader": tokenizer_loader,
            "model_loader": model_loader,
            "tokenizer": tokenizer,
            "model_instance": model_instance,
            "model": model,
            "request_observer": request_observer,
            "request_capture": request_capture,
        }

    def _client(self) -> LocalQwenClient:
        if self._closed:
            raise LocalBenchmarkProviderError("local Qwen client is closed")
        if self._delegate is None:
            before = self._guard.metadata_fingerprint()
            loaded = LocalQwenClient(**self._load_kwargs)
            try:
                self._guard.verify_metadata_unchanged(before)
            except Exception:
                loaded.close()
                raise
            self._delegate = loaded
            for name in (
                "loader",
                "tokenizer_loader",
                "model_loader",
                "tokenizer",
                "model_instance",
                "model",
            ):
                self._load_kwargs[name] = None
        return self._delegate

    def resolve_model(self, requested: str | None = None) -> str:
        if requested is None or requested == self.exact_model_identity:
            return self.exact_model_identity
        raise LocalBenchmarkProviderError(
            "requested model is not the exact approved model"
        )

    def generate(
        self,
        system_prompt: str,
        user_prompt: str | None = None,
        *,
        model: str | None = None,
    ) -> str:
        return self._client().generate(system_prompt, user_prompt, model=model)

    def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        model: str | None = None,
    ) -> str:
        return self._client().complete(system_prompt, user_prompt, model=model)

    def generate_answer(
        self,
        query: str,
        context_chunks: list[str],
        model: str | None = None,
    ) -> str:
        return self._client().generate_answer(query, context_chunks, model=model)

    def complete_structured(
        self,
        system: str,
        user: str,
        *,
        schema: dict[str, Any],
        model: str | None = None,
    ) -> dict[str, Any]:
        return self._client().complete_structured(
            system, user, schema=schema, model=model
        )

    def counters(self) -> dict[str, int]:
        if self._delegate is None:
            return {
                "generation_calls": 0,
                "repair_calls": 0,
                "prompt_tokens": 0,
                "generated_tokens": 0,
            }
        return self._delegate.counters()

    def close(self) -> None:
        if self._delegate is not None:
            self._delegate.close()
        for name in (
            "loader",
            "tokenizer_loader",
            "model_loader",
            "tokenizer",
            "model_instance",
            "model",
        ):
            self._load_kwargs[name] = None
        self._closed = True

    def report(self) -> dict[str, Any]:
        return {
            "is_remote": False,
            "model": self.exact_model_identity,
            "model_revision": self.model_revision,
            "model_bundle_sha256": self.model_bundle_sha256,
            "deterministic_generation": True,
            "counters": self.counters(),
        }
