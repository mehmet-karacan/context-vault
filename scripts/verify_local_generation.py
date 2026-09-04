#!/usr/bin/env python3
"""Verify an exact offline Qwen2 generation snapshot with real guarded prompts."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import math
import os
import re
import secrets
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


TOOL_VERSION = "1.0.0"
MAX_SNAPSHOT_FILES = 64
MAX_SNAPSHOT_BYTES = 4_000_000_000
DEFAULT_TIMEOUT_SECONDS = 600
MAX_TOTAL_PROMPT_TOKENS = 4096
MAX_TOTAL_GENERATED_TOKENS = 72
EXPECTED_TORCH_VERSION = "2.14.0"
EXPECTED_TRANSFORMERS_VERSION = "5.15.1"
WORKER_CAPABILITY_ENV = "CV_LOCAL_GENERATION_WORKER_CAPABILITY_SHA256"
REPO = Path(__file__).resolve().parents[1]
DEPENDENCY_LOCK = REPO / "document-rag-platform/services/backend/uv.lock"
GROUNDED_MESSAGES = (
    {
        "role": "system",
        "content": (
            "Use only CONTEXT. If CONTEXT contains an ANSWER_TOKEN, output exactly "
            "its value and nothing else. Otherwise output exactly NO_CONTEXT. Do "
            "not invent facts."
        ),
    },
    {
        "role": "user",
        "content": (
            "CONTEXT: The mandatory migration action is "
            "ANSWER_TOKEN=KEEP_EXISTING_USER_DATA.\n"
            "QUESTION: What action is mandatory?"
        ),
    },
)
EXPECTED_GROUNDED_OUTPUT = "KEEP_EXISTING_USER_DATA"
EXPECTED_NO_ANSWER_OUTPUT = "NO_CONTEXT"
INFERENCE_FIELDS = {
    "load_seconds",
    "generation_seconds",
    "grounded_guard_pass",
    "no_answer_guard_pass",
    "deterministic_repeat_match",
    "prompt_tokens",
    "generated_tokens",
    "grounded_output_sha256",
    "no_answer_output_sha256",
    "runtime_versions",
}
NO_ANSWER_MESSAGES = (
    GROUNDED_MESSAGES[0],
    {
        "role": "user",
        "content": (
            "CONTEXT: Context Vault is a local knowledge system.\n"
            "QUESTION: How many years are weekly backups retained?"
        ),
    },
)


class LocalGenerationError(RuntimeError):
    """The local generation model did not satisfy the admission contract."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def snapshot_bundle(snapshot: Path) -> dict[str, Any]:
    """Return a bounded hash over paths, link identities and resolved file bytes."""

    if not snapshot.is_dir():
        raise LocalGenerationError("model snapshot directory is unavailable")
    pending: list[tuple[Path, dict[str, Any]]] = []
    for path in sorted(snapshot.rglob("*"), key=lambda item: item.as_posix()):
        if not path.is_file():
            continue
        try:
            size = path.stat().st_size
            symlink_target = os.readlink(path) if path.is_symlink() else None
        except OSError as exc:
            raise LocalGenerationError("model snapshot file is unreadable") from exc
        pending.append(
            (
                path,
                {
                    "path": path.relative_to(snapshot).as_posix(),
                    "bytes": size,
                    "symlink_target": symlink_target,
                },
            )
        )
    if not pending:
        raise LocalGenerationError("model snapshot contains no files")
    if len(pending) > MAX_SNAPSHOT_FILES:
        raise LocalGenerationError("model snapshot exceeds file-count limit")
    total_bytes = sum(entry["bytes"] for _, entry in pending)
    if total_bytes > MAX_SNAPSHOT_BYTES:
        raise LocalGenerationError("model snapshot exceeds byte-size limit")
    entries: list[dict[str, Any]] = []
    for path, entry in pending:
        try:
            entry["sha256"] = _sha256(path)
        except OSError as exc:
            raise LocalGenerationError("model snapshot file is unreadable") from exc
        entries.append(entry)
    canonical = json.dumps(entries, sort_keys=True, separators=(",", ":")).encode()
    return {
        "sha256": hashlib.sha256(canonical).hexdigest(),
        "files": len(entries),
        "bytes": total_bytes,
    }


def validate_snapshot(
    snapshot: Path, *, expected_model: str, expected_revision: str
) -> dict[str, Any]:
    """Validate exact cache identity, symlink confinement and Qwen2 file shape."""

    if not re.fullmatch(r"[A-Za-z0-9._-]+/[A-Za-z0-9._-]+", expected_model):
        raise LocalGenerationError("expected model must be an exact owner/name id")
    if not re.fullmatch(
        r"[A-Fa-f0-9]{7,64}|revision-[A-Za-z0-9._-]+", expected_revision
    ):
        raise LocalGenerationError("expected revision is invalid")
    resolved = snapshot.resolve(strict=True)
    expected_cache_name = "models--" + expected_model.replace("/", "--")
    if (
        resolved.parent.name != "snapshots"
        or resolved.parent.parent.name != expected_cache_name
    ):
        raise LocalGenerationError(
            "model snapshot identity does not match expected model"
        )
    if resolved.name != expected_revision:
        raise LocalGenerationError(
            "model snapshot revision does not match expected revision"
        )
    model_cache_root = resolved.parent.parent
    for path in resolved.rglob("*"):
        if not path.is_symlink():
            continue
        try:
            path.resolve(strict=True).relative_to(model_cache_root)
        except (OSError, ValueError) as exc:
            raise LocalGenerationError(
                "model snapshot symlink escapes model cache root"
            ) from exc

    for name in ("config.json", "generation_config.json", "tokenizer_config.json"):
        if not (resolved / name).is_file():
            raise LocalGenerationError(f"model snapshot lacks {name}")
    if not any(
        (resolved / name).is_file()
        for name in ("model.safetensors", "pytorch_model.bin")
    ):
        raise LocalGenerationError("model snapshot lacks model weights")
    if not any(
        (resolved / name).is_file() for name in ("tokenizer.json", "tokenizer.model")
    ):
        raise LocalGenerationError("model snapshot lacks tokenizer")
    try:
        config = json.loads((resolved / "config.json").read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise LocalGenerationError("model config is unreadable") from exc
    if not isinstance(config, dict):
        raise LocalGenerationError("model config must be a JSON object")
    architectures = config.get("architectures")
    if (
        config.get("model_type") != "qwen2"
        or not isinstance(architectures, list)
        or not architectures
        or not all(isinstance(item, str) for item in architectures)
        or "Qwen2ForCausalLM" not in architectures
    ):
        raise LocalGenerationError("model architecture is not Qwen2ForCausalLM")
    return snapshot_bundle(resolved)


def _grounded_guard_pass(text: str) -> bool:
    return text.strip() == EXPECTED_GROUNDED_OUTPUT


def _prompt_case_bundle_sha256() -> str:
    cases = {
        "grounded": {
            "messages": GROUNDED_MESSAGES,
            "expected_output": EXPECTED_GROUNDED_OUTPUT,
        },
        "no_answer": {
            "messages": NO_ANSWER_MESSAGES,
            "expected_output": EXPECTED_NO_ANSWER_OUTPUT,
        },
    }
    canonical = json.dumps(cases, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(canonical).hexdigest()


def _generate_once(model: Any, tokenizer: Any, messages: tuple[dict[str, str], ...]):
    import torch

    prompt = tokenizer.apply_chat_template(
        list(messages), tokenize=False, add_generation_prompt=True
    )
    inputs = tokenizer(prompt, return_tensors="pt").to(model.device)
    prompt_tokens = int(inputs["input_ids"].shape[-1])
    with torch.inference_mode():
        outputs = model.generate(
            **inputs,
            max_new_tokens=24,
            do_sample=False,
            pad_token_id=tokenizer.eos_token_id,
        )
    generated = outputs[0][prompt_tokens:]
    text = tokenizer.decode(generated, skip_special_tokens=True).strip()
    return text, prompt_tokens, int(generated.shape[-1])


def _load_and_generate(snapshot: Path, device: str) -> dict[str, Any]:
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["TOKENIZERS_PARALLELISM"] = "false"
    try:
        import torch
        import transformers
        from transformers import AutoModelForCausalLM, AutoTokenizer
    except ImportError as exc:
        raise LocalGenerationError(
            "local generation runtime is unavailable; install the pinned eval runtime"
        ) from exc

    started = time.perf_counter()
    try:
        tokenizer = AutoTokenizer.from_pretrained(
            str(snapshot), local_files_only=True, trust_remote_code=False
        )
        model = AutoModelForCausalLM.from_pretrained(
            str(snapshot),
            local_files_only=True,
            trust_remote_code=False,
            dtype=torch.float16 if device == "mps" else "auto",
        )
        model.to(device)
        model.eval()
    except Exception as exc:  # noqa: BLE001 - local model boundary
        raise LocalGenerationError(
            "local generation model failed to load offline"
        ) from exc
    loaded = time.perf_counter()
    try:
        grounded, grounded_prompt_tokens, grounded_tokens = _generate_once(
            model, tokenizer, GROUNDED_MESSAGES
        )
        repeated, repeat_prompt_tokens, repeat_tokens = _generate_once(
            model, tokenizer, GROUNDED_MESSAGES
        )
        no_answer, no_answer_prompt_tokens, no_answer_tokens = _generate_once(
            model, tokenizer, NO_ANSWER_MESSAGES
        )
    except Exception as exc:  # noqa: BLE001 - local model boundary
        raise LocalGenerationError("local generation inference failed") from exc
    generated = time.perf_counter()
    return {
        "load_seconds": round(loaded - started, 3),
        "generation_seconds": round(generated - loaded, 3),
        "grounded_guard_pass": _grounded_guard_pass(grounded),
        "no_answer_guard_pass": no_answer.strip() == EXPECTED_NO_ANSWER_OUTPUT,
        "deterministic_repeat_match": grounded == repeated,
        "prompt_tokens": (
            grounded_prompt_tokens + repeat_prompt_tokens + no_answer_prompt_tokens
        ),
        "generated_tokens": grounded_tokens + repeat_tokens + no_answer_tokens,
        "grounded_output_sha256": hashlib.sha256(grounded.encode()).hexdigest(),
        "no_answer_output_sha256": hashlib.sha256(no_answer.encode()).hexdigest(),
        "runtime_versions": {
            "python": ".".join(map(str, sys.version_info[:3])),
            "torch": torch.__version__,
            "transformers": transformers.__version__,
        },
    }


def _validated_inference(inference: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(inference, dict) or set(inference) != INFERENCE_FIELDS:
        raise LocalGenerationError("inference report fields are invalid")
    for name in (
        "grounded_guard_pass",
        "no_answer_guard_pass",
        "deterministic_repeat_match",
    ):
        if not isinstance(inference[name], bool):
            raise LocalGenerationError("inference report boolean is invalid")
    for name in ("load_seconds", "generation_seconds"):
        value = inference[name]
        if (
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or not math.isfinite(value)
            or value < 0
        ):
            raise LocalGenerationError("inference report duration is invalid")
    if (
        inference["load_seconds"] + inference["generation_seconds"]
        > DEFAULT_TIMEOUT_SECONDS
    ):
        raise LocalGenerationError("inference report duration exceeds limit")
    for name in ("prompt_tokens", "generated_tokens"):
        value = inference[name]
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise LocalGenerationError("inference report token count is invalid")
    if inference["prompt_tokens"] > MAX_TOTAL_PROMPT_TOKENS:
        raise LocalGenerationError("inference report prompt-token limit exceeded")
    if inference["generated_tokens"] > MAX_TOTAL_GENERATED_TOKENS:
        raise LocalGenerationError("inference report generated-token limit exceeded")
    for name in ("grounded_output_sha256", "no_answer_output_sha256"):
        if not isinstance(inference[name], str) or not re.fullmatch(
            r"[a-f0-9]{64}", inference[name]
        ):
            raise LocalGenerationError("inference report output hash is invalid")
    expected_hashes = {
        "grounded_output_sha256": hashlib.sha256(
            EXPECTED_GROUNDED_OUTPUT.encode()
        ).hexdigest(),
        "no_answer_output_sha256": hashlib.sha256(
            EXPECTED_NO_ANSWER_OUTPUT.encode()
        ).hexdigest(),
    }
    if any(inference[name] != value for name, value in expected_hashes.items()):
        raise LocalGenerationError("inference report output hash does not match case")
    versions = inference["runtime_versions"]
    if (
        not isinstance(versions, dict)
        or set(versions) != {"python", "torch", "transformers"}
        or not all(isinstance(value, str) and value for value in versions.values())
    ):
        raise LocalGenerationError("inference report runtime versions are invalid")
    if (
        not re.fullmatch(r"3\.12\.\d+", versions["python"])
        or versions["torch"] != EXPECTED_TORCH_VERSION
        or versions["transformers"] != EXPECTED_TRANSFORMERS_VERSION
    ):
        raise LocalGenerationError("inference report runtime is not pinned")
    return {name: inference[name] for name in sorted(INFERENCE_FIELDS)}


def _provenance() -> dict[str, str]:
    try:
        revision = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=REPO,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise LocalGenerationError("repository revision is unavailable") from exc
    if not re.fullmatch(r"[a-f0-9]{40}", revision):
        raise LocalGenerationError("repository revision is invalid")
    if not DEPENDENCY_LOCK.is_file():
        raise LocalGenerationError("dependency lock is unavailable")
    return {
        "repository_revision": revision,
        "dependency_lock_sha256": _sha256(DEPENDENCY_LOCK),
        "verifier_sha256": _sha256(Path(__file__).resolve(strict=True)),
    }


def verify_local_generation(
    *,
    snapshot: Path,
    expected_model: str,
    expected_revision: str,
    expected_bundle_sha256: str,
    device: str,
) -> dict[str, Any]:
    """Hash-admit and invoke an exact local Qwen2 generation snapshot."""

    if not re.fullmatch(r"[a-f0-9]{64}", expected_bundle_sha256):
        raise LocalGenerationError("expected bundle SHA-256 is invalid")
    if device not in {"cpu", "mps"}:
        raise LocalGenerationError("device must be cpu or mps")
    bundle = validate_snapshot(
        snapshot,
        expected_model=expected_model,
        expected_revision=expected_revision,
    )
    if bundle["sha256"] != expected_bundle_sha256:
        raise LocalGenerationError("model bundle SHA-256 mismatch")
    inference = _validated_inference(
        _load_and_generate(snapshot.resolve(strict=True), device)
    )
    post_inference_bundle = validate_snapshot(
        snapshot,
        expected_model=expected_model,
        expected_revision=expected_revision,
    )
    if post_inference_bundle != bundle:
        raise LocalGenerationError("model snapshot changed during inference")
    if any(
        inference.get(name) is not True
        for name in (
            "grounded_guard_pass",
            "no_answer_guard_pass",
            "deterministic_repeat_match",
        )
    ):
        raise LocalGenerationError("local generation guard failed")
    provenance = _provenance()
    return {
        "schema_version": "1.0",
        "request_type": "local-generation-runtime-verification",
        "tool_version": TOOL_VERSION,
        "result": "PASS",
        "model": expected_model,
        "model_revision": expected_revision,
        "model_bundle_sha256": bundle["sha256"],
        "model_bundle_files": bundle["files"],
        "model_bundle_bytes": bundle["bytes"],
        "model_resource_limits": {
            "max_files": MAX_SNAPSHOT_FILES,
            "max_bytes": MAX_SNAPSHOT_BYTES,
        },
        "execution_wall_clock_limit_seconds": DEFAULT_TIMEOUT_SECONDS,
        "prompt_case_bundle_sha256": _prompt_case_bundle_sha256(),
        "device": device,
        "local_model_invoked": True,
        "remote_provider_invoked": False,
        "network_policy": "huggingface-local-files-only",
        "library_offline_mode": True,
        "network_isolation_verified": False,
        "generation_text_retained": False,
        **inference,
        **provenance,
    }


def write_json_output(path: Path, report: dict[str, Any]) -> None:
    """Create a private-by-default report without following or overwriting links."""

    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() or path.is_symlink():
        raise LocalGenerationError("JSON output must be a new regular file")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags, 0o600)
    except FileExistsError as exc:
        raise LocalGenerationError("JSON output must be a new regular file") from exc
    except OSError as exc:
        raise LocalGenerationError("JSON output could not be securely created") from exc
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            os.fchmod(handle.fileno(), 0o600)
            json.dump(report, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
    except OSError as exc:
        raise LocalGenerationError("JSON output could not be securely written") from exc


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--expected-model", required=True)
    parser.add_argument("--expected-revision", required=True)
    parser.add_argument("--expected-bundle-sha256", required=True)
    parser.add_argument("--device", choices=("cpu", "mps"), default="cpu")
    parser.add_argument("--json-output", type=Path)
    parser.add_argument("--timeout-seconds", type=int, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument("--worker-fd", type=int, help=argparse.SUPPRESS)
    return parser.parse_args()


def _run_bounded_worker(timeout_seconds: int) -> int:
    if timeout_seconds < 1 or timeout_seconds > DEFAULT_TIMEOUT_SECONDS:
        print(json.dumps({"result": "FAIL", "error": "timeout is out of range"}))
        return 2
    read_fd, write_fd = os.pipe()
    token = secrets.token_bytes(32)
    worker_env = os.environ.copy()
    worker_env[WORKER_CAPABILITY_ENV] = hashlib.sha256(token).hexdigest()
    try:
        os.set_blocking(read_fd, False)
        os.write(write_fd, token)
        os.close(write_fd)
        write_fd = -1
        completed = subprocess.run(
            [
                sys.executable,
                str(Path(__file__).resolve()),
                *sys.argv[1:],
                "--worker-fd",
                str(read_fd),
            ],
            timeout=timeout_seconds,
            check=False,
            env=worker_env,
            pass_fds=(read_fd,),
        )
    except subprocess.TimeoutExpired:
        print(
            json.dumps(
                {"result": "FAIL", "error": "local generation verification timed out"},
                sort_keys=True,
            )
        )
        return 2
    finally:
        if write_fd >= 0:
            os.close(write_fd)
        os.close(read_fd)
    return completed.returncode


def _consume_worker_capability(worker_fd: int) -> bool:
    expected = os.environ.pop(WORKER_CAPABILITY_ENV, "")
    if worker_fd <= 2 or not re.fullmatch(r"[a-f0-9]{64}", expected):
        return False
    try:
        if os.get_blocking(worker_fd):
            return False
        token = os.read(worker_fd, 33)
        os.close(worker_fd)
    except OSError:
        return False
    return len(token) == 32 and hmac.compare_digest(
        hashlib.sha256(token).hexdigest(), expected
    )


def main() -> int:
    args = _parse_args()
    if args.worker_fd is None:
        return _run_bounded_worker(args.timeout_seconds)
    if not _consume_worker_capability(args.worker_fd):
        print(json.dumps({"result": "FAIL", "error": "worker capability is invalid"}))
        return 2
    try:
        report = verify_local_generation(
            snapshot=args.snapshot,
            expected_model=args.expected_model,
            expected_revision=args.expected_revision,
            expected_bundle_sha256=args.expected_bundle_sha256,
            device=args.device,
        )
    except (LocalGenerationError, OSError) as exc:
        print(json.dumps({"result": "FAIL", "error": str(exc)}, sort_keys=True))
        return 2
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.json_output:
        write_json_output(args.json_output, report)
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
