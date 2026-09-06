"""Independent local request-capture protocol contracts."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import secrets
import socket
import tempfile
import threading
import time
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[5]
SCRIPT = REPO / "scripts/capture_local_provider_boundary.py"


def _module():
    spec = importlib.util.spec_from_file_location("cv_capture_boundary", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _bindings(golden: bytes) -> dict[str, str]:
    return {
        "repository_revision": "a" * 40,
        "runner_bundle_sha256": "b" * 64,
        "private_pack_manifest_sha256": "c" * 64,
        "dataset_sha256": "d" * 64,
        "golden_dataset_sha256": hashlib.sha256(golden).hexdigest(),
        "execution_dataset_sha256": "e" * 64,
        "execution_projection_sha256": "f" * 64,
        "environment_hash": "1" * 64,
        "embedding_provider": "local-sentence-transformers",
        "embedding_model": "BAAI/bge-m3@revision",
        "generation_provider": "local-transformers",
        "generation_model": "Qwen/Qwen2.5-1.5B-Instruct@revision",
    }


def _start(module, tmp_path: Path, golden: bytes):
    socket_path = Path(tempfile.gettempdir()) / f"cv-cap-{secrets.token_hex(6)}.sock"
    evidence_path = tmp_path / "capture-evidence.json"
    golden_path = tmp_path / "golden.jsonl"
    golden_path.write_bytes(golden)
    nonce = "9" * 64
    errors = []

    def target():
        try:
            module.serve(
                socket_path=socket_path,
                nonce=nonce,
                golden_path=golden_path,
                evidence_path=evidence_path,
                bindings=_bindings(golden),
            )
        except Exception as exc:  # test thread transports the failure
            errors.append(exc)

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    deadline = time.monotonic() + 2
    while not socket_path.exists() and time.monotonic() < deadline:
        time.sleep(0.005)
    assert socket_path.exists()
    return socket_path, evidence_path, nonce, thread, errors


def test_capture_seals_before_first_golden_open_and_retains_no_raw(tmp_path):
    module = _module()
    prompt_canary = "PRIVATE_PROMPT_CANARY"
    golden_canary = b'{"golden":"PRIVATE_GOLDEN_CANARY"}\n'
    socket_path, evidence_path, nonce, thread, errors = _start(
        module, tmp_path, golden_canary
    )
    client = module.CaptureClient(socket_path, nonce, _bindings(golden_canary))
    client.select("private-case-id")
    raw = [prompt_canary]
    event = {
        "ordinal": 1,
        "kind": "query",
        "model": "BAAI/bge-m3@revision",
        "item_count": 1,
        "byte_count": len(prompt_canary.encode()),
        "token_count": 4,
        "payload_sha256": hashlib.sha256(
            json.dumps(raw, ensure_ascii=False, separators=(",", ":")).encode()
        ).hexdigest(),
    }
    assert len(client.capture(event, raw)) == 64
    labels, summary = client.seal_and_read_labels(1)
    thread.join(timeout=2)

    assert errors == []
    assert labels == golden_canary
    assert summary["request_count"] == 1
    evidence = json.loads(evidence_path.read_text())
    assert evidence["status"] == "SEALED_BEFORE_GOLDEN_OPEN"
    assert evidence["requests_sealed_at_utc"] <= evidence["golden_first_open_at_utc"]
    assert evidence["ordered_request_digest"] == summary["ordered_request_digest"]
    assert evidence["session_sha256"] == summary["session_sha256"]
    assert evidence["raw_request_retained"] is False
    assert evidence["raw_response_retained"] is False
    assert evidence["golden_payload_retained"] is False
    serialized = evidence_path.read_text()
    assert prompt_canary not in serialized
    assert golden_canary.decode().strip() not in serialized
    assert str(tmp_path) not in serialized
    assert os.stat(evidence_path).st_mode & 0o777 == 0o600


def test_capture_payload_mismatch_fails_without_evidence(tmp_path):
    module = _module()
    golden = b"{}\n"
    socket_path, evidence_path, nonce, thread, errors = _start(module, tmp_path, golden)
    client = module.CaptureClient(socket_path, nonce, _bindings(golden))
    client.select("case")
    event = {
        "ordinal": 1,
        "kind": "query",
        "model": "BAAI/bge-m3@revision",
        "item_count": 1,
        "byte_count": 5,
        "token_count": 1,
        "payload_sha256": "0" * 64,
    }
    with pytest.raises(module.CaptureBoundaryError):
        client.capture(event, ["wrong"])
    thread.join(timeout=2)
    assert errors
    assert not evidence_path.exists()


def test_socket_substitution_cannot_learn_nonce_or_authenticate(tmp_path):
    module = _module()
    socket_path = Path(tempfile.gettempdir()) / f"cv-cap-{secrets.token_hex(6)}.sock"
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(socket_path))
    socket_path.chmod(0o600)
    listener.listen(1)
    observed = []

    def imposter():
        connection, _ = listener.accept()
        with connection:
            frame = module._receive_frame(connection)
            observed.append(frame)
            module._send_frame(
                connection,
                {
                    "status": "ready",
                    "session_sha256": "1" * 64,
                    "server_challenge": "2" * 64,
                    "auth": "0" * 64,
                },
            )

    thread = threading.Thread(target=imposter, daemon=True)
    thread.start()
    nonce = "9" * 64
    try:
        with pytest.raises(module.CaptureBoundaryError, match="authentication"):
            module.CaptureClient(socket_path, nonce, _bindings(b"{}\n"))
        thread.join(timeout=2)
        assert observed
        assert "nonce" not in observed[0]
        assert nonce not in json.dumps(observed[0])
    finally:
        listener.close()
        socket_path.unlink(missing_ok=True)


def test_client_keeps_authenticated_connection_after_socket_path_swap(tmp_path):
    module = _module()
    golden = b"{}\n"
    socket_path, evidence_path, nonce, thread, errors = _start(module, tmp_path, golden)
    client = module.CaptureClient(socket_path, nonce, _bindings(golden))

    socket_path.unlink()
    imposter = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    imposter.bind(str(socket_path))
    socket_path.chmod(0o600)
    imposter.listen(1)
    imposter.settimeout(0.2)
    client.select("case")
    raw = ["request"]
    event = {
        "ordinal": 1,
        "kind": "query",
        "model": "BAAI/bge-m3@revision",
        "item_count": 1,
        "byte_count": 7,
        "token_count": 1,
        "payload_sha256": hashlib.sha256(
            json.dumps(raw, ensure_ascii=False, separators=(",", ":")).encode()
        ).hexdigest(),
    }
    try:
        client.capture(event, raw)
        labels, summary = client.seal_and_read_labels(1)
        with pytest.raises(TimeoutError):
            imposter.accept()
    finally:
        imposter.close()
        socket_path.unlink(missing_ok=True)
    thread.join(timeout=2)
    assert errors == []
    assert labels == golden
    assert summary["request_count"] == 1
    assert evidence_path.exists()
