#!/usr/bin/env python3
"""Verify an exact, offline BGE-M3 snapshot with a real embedding smoke run.

The verifier never resolves a model id over the network.  It accepts only an
existing Hugging Face snapshot directory, hashes every resolved file byte, then
loads that directory with ``local_files_only=True`` while the Hugging Face and
Transformers offline guards are forced on.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Sequence


TOOL_VERSION = "1.0.0"
EXPECTED_DIMENSION = 1024
REPO = Path(__file__).resolve().parents[1]
DEPENDENCY_LOCK = REPO / "document-rag-platform/services/backend/uv.lock"
DEFAULT_TEXTS = (
    "Represent this sentence for searching relevant passages: "
    "Context Vault migration güvenliği",
    "Migration drift mevcut veriyi silmeden doğrulanmalıdır.",
)


class LocalBgeError(RuntimeError):
    """The local model or its runtime did not satisfy the admission contract."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def snapshot_bundle(snapshot: Path) -> dict[str, Any]:
    """Return a deterministic, path-relative hash over resolved file bytes."""

    if not snapshot.is_dir():
        raise LocalBgeError("model snapshot directory is unavailable")
    entries: list[dict[str, Any]] = []
    for path in sorted(snapshot.rglob("*"), key=lambda item: item.as_posix()):
        if not path.is_file():
            continue
        try:
            size = path.stat().st_size
            digest = _sha256(path)
        except OSError as exc:
            raise LocalBgeError("model snapshot file is unreadable") from exc
        entries.append(
            {
                "path": path.relative_to(snapshot).as_posix(),
                "bytes": size,
                "sha256": digest,
            }
        )
    if not entries:
        raise LocalBgeError("model snapshot contains no files")
    canonical = json.dumps(entries, sort_keys=True, separators=(",", ":")).encode()
    return {
        "sha256": hashlib.sha256(canonical).hexdigest(),
        "files": len(entries),
        "bytes": sum(entry["bytes"] for entry in entries),
    }


def validate_snapshot(
    snapshot: Path, *, expected_model: str, expected_revision: str
) -> dict[str, Any]:
    """Validate snapshot identity and the minimum complete BGE-M3 file set."""

    if not re.fullmatch(r"[A-Za-z0-9._-]+/[A-Za-z0-9._-]+", expected_model):
        raise LocalBgeError("expected model must be an exact owner/name id")
    if not re.fullmatch(
        r"[A-Fa-f0-9]{7,64}|revision-[A-Za-z0-9._-]+", expected_revision
    ):
        raise LocalBgeError("expected revision is invalid")
    resolved = snapshot.resolve(strict=True)
    expected_cache_name = "models--" + expected_model.replace("/", "--")
    if (
        resolved.parent.name != "snapshots"
        or resolved.parent.parent.name != expected_cache_name
    ):
        raise LocalBgeError("model snapshot identity does not match expected model")
    if resolved.name != expected_revision:
        raise LocalBgeError("model snapshot revision does not match expected revision")
    model_cache_root = resolved.parent.parent
    for path in resolved.rglob("*"):
        if not path.is_symlink():
            continue
        try:
            path.resolve(strict=True).relative_to(model_cache_root)
        except (OSError, ValueError) as exc:
            raise LocalBgeError(
                "model snapshot symlink escapes model cache root"
            ) from exc

    required = ("config.json", "modules.json")
    for name in required:
        if not (resolved / name).is_file():
            raise LocalBgeError(f"model snapshot lacks {name}")
    if not any(
        (resolved / name).is_file()
        for name in ("pytorch_model.bin", "model.safetensors")
    ):
        raise LocalBgeError("model snapshot lacks model weights")
    if not any(
        (resolved / name).is_file()
        for name in ("tokenizer.json", "sentencepiece.bpe.model")
    ):
        raise LocalBgeError("model snapshot lacks tokenizer")
    return snapshot_bundle(resolved)


def _load_and_encode(
    snapshot: Path, texts: Sequence[str], device: str
) -> dict[str, Any]:
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["TOKENIZERS_PARALLELISM"] = "false"
    try:
        import numpy as np
        import sentence_transformers
        import torch
        from sentence_transformers import SentenceTransformer
    except ImportError as exc:
        raise LocalBgeError(
            "local BGE runtime is unavailable; install the pinned eval runtime"
        ) from exc

    started = time.perf_counter()
    try:
        model = SentenceTransformer(
            str(snapshot),
            device=device,
            local_files_only=True,
            trust_remote_code=False,
        )
    except Exception as exc:  # noqa: BLE001 - provider boundary
        raise LocalBgeError("local BGE model failed to load offline") from exc
    loaded = time.perf_counter()
    try:
        vectors = np.asarray(
            model.encode(
                list(texts),
                normalize_embeddings=True,
                convert_to_numpy=True,
            )
        )
    except Exception as exc:  # noqa: BLE001 - provider boundary
        raise LocalBgeError("local BGE embedding inference failed") from exc
    encoded = time.perf_counter()
    norms = np.linalg.norm(vectors, axis=1)
    cosine = float(vectors[0] @ vectors[1]) if len(vectors) >= 2 else None
    return {
        "shape": list(vectors.shape),
        "finite": bool(np.isfinite(vectors).all()),
        "norms": [round(float(value), 6) for value in norms],
        "cosine": round(cosine, 6) if cosine is not None else None,
        "load_seconds": round(loaded - started, 3),
        "encode_seconds": round(encoded - loaded, 3),
        "runtime_versions": {
            "python": ".".join(map(str, sys.version_info[:3])),
            "torch": torch.__version__,
            "sentence_transformers": sentence_transformers.__version__,
        },
    }


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
        raise LocalBgeError("repository revision is unavailable") from exc
    if not re.fullmatch(r"[a-f0-9]{40}", revision):
        raise LocalBgeError("repository revision is invalid")
    if not DEPENDENCY_LOCK.is_file():
        raise LocalBgeError("dependency lock is unavailable")
    return {
        "repository_revision": revision,
        "dependency_lock_sha256": _sha256(DEPENDENCY_LOCK),
        "verifier_sha256": _sha256(Path(__file__).resolve(strict=True)),
    }


def verify_local_bge(
    *,
    snapshot: Path,
    expected_model: str,
    expected_revision: str,
    expected_bundle_sha256: str,
    texts: Sequence[str],
    device: str,
) -> dict[str, Any]:
    """Hash-admit and invoke an exact local BGE snapshot."""

    if not re.fullmatch(r"[a-f0-9]{64}", expected_bundle_sha256):
        raise LocalBgeError("expected bundle SHA-256 is invalid")
    if len(texts) < 2 or any(
        not isinstance(text, str) or not text.strip() for text in texts
    ):
        raise LocalBgeError("at least two non-empty smoke texts are required")
    if device not in {"cpu", "mps"}:
        raise LocalBgeError("device must be cpu or mps")
    bundle = validate_snapshot(
        snapshot,
        expected_model=expected_model,
        expected_revision=expected_revision,
    )
    if bundle["sha256"] != expected_bundle_sha256:
        raise LocalBgeError("model bundle SHA-256 mismatch")
    inference = _load_and_encode(snapshot.resolve(strict=True), texts, device)
    post_inference_bundle = validate_snapshot(
        snapshot,
        expected_model=expected_model,
        expected_revision=expected_revision,
    )
    if post_inference_bundle != bundle:
        raise LocalBgeError("model snapshot changed during inference")
    shape = inference.get("shape")
    norms = inference.get("norms")
    if (
        shape != [len(texts), EXPECTED_DIMENSION]
        or inference.get("finite") is not True
        or not isinstance(norms, list)
        or len(norms) != len(texts)
        or any(not math.isclose(value, 1.0, abs_tol=1e-4) for value in norms)
    ):
        raise LocalBgeError("local BGE output failed dimension/finite/norm checks")
    return {
        "schema_version": "1.0",
        "request_type": "local-bge-runtime-verification",
        "tool_version": TOOL_VERSION,
        "result": "PASS",
        "model": expected_model,
        "model_revision": expected_revision,
        "model_bundle_sha256": bundle["sha256"],
        "model_bundle_files": bundle["files"],
        "model_bundle_bytes": bundle["bytes"],
        "device": device,
        "embedding_dimension": EXPECTED_DIMENSION,
        "local_model_invoked": True,
        "remote_provider_invoked": False,
        "network_policy": "huggingface-local-files-only",
        "library_offline_mode": True,
        "network_isolation_verified": False,
        **_provenance(),
        **inference,
    }


def write_json_output(path: Path, report: dict[str, Any]) -> None:
    """Create a private-by-default report without following or overwriting links."""

    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() or path.is_symlink():
        raise LocalBgeError("JSON output must be a new regular file")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags, 0o600)
    except FileExistsError as exc:
        raise LocalBgeError("JSON output must be a new regular file") from exc
    except OSError as exc:
        raise LocalBgeError("JSON output could not be securely created") from exc
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            os.fchmod(handle.fileno(), 0o600)
            json.dump(report, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
    except OSError as exc:
        raise LocalBgeError("JSON output could not be securely written") from exc


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--expected-model", required=True)
    parser.add_argument("--expected-revision", required=True)
    parser.add_argument("--expected-bundle-sha256", required=True)
    parser.add_argument("--device", choices=("cpu", "mps"), default="cpu")
    parser.add_argument("--json-output", type=Path)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    try:
        report = verify_local_bge(
            snapshot=args.snapshot,
            expected_model=args.expected_model,
            expected_revision=args.expected_revision,
            expected_bundle_sha256=args.expected_bundle_sha256,
            texts=DEFAULT_TEXTS,
            device=args.device,
        )
    except (LocalBgeError, OSError) as exc:
        print(json.dumps({"result": "FAIL", "error": str(exc)}, sort_keys=True))
        return 2
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.json_output:
        write_json_output(args.json_output, report)
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
