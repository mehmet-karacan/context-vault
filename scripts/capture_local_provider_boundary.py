#!/usr/bin/env python3
"""Independent, fail-closed capture for local benchmark request boundaries.

The collector receives raw request values only over a private Unix socket, hashes
them in memory, and persists only bounded digests/counters.  Golden bytes are first
opened after the request stream is sealed and are returned to the runner in memory.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import re
import socket
import stat
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


TOOL_VERSION = "1.0.0"
REPO = Path(__file__).resolve().parents[1]
MAX_FRAME_BYTES = 32_000_000
MAX_GOLDEN_BYTES = 8_000_000
MAX_REQUESTS = 1_000_000
_SHA256 = re.compile(r"[a-f0-9]{64}\Z")
_NONCE = re.compile(r"[a-f0-9]{64}\Z")
_KINDS = {"embedding", "query", "generation", "repair"}
_BINDING_FIELDS = {
    "repository_revision",
    "runner_bundle_sha256",
    "private_pack_manifest_sha256",
    "dataset_sha256",
    "golden_dataset_sha256",
    "execution_dataset_sha256",
    "execution_projection_sha256",
    "environment_hash",
    "embedding_provider",
    "embedding_model",
    "generation_provider",
    "generation_model",
}


class CaptureBoundaryError(RuntimeError):
    """The independent capture protocol or evidence contract failed."""


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _domain_hash(domain: str, value: Any) -> str:
    digest = hashlib.sha256((domain + "\0").encode())
    digest.update(_canonical(value))
    return digest.hexdigest()


def _auth_tag(key: bytes, value: Mapping[str, Any]) -> str:
    return hmac.new(key, _canonical(value), hashlib.sha256).hexdigest()


def _session_key(
    nonce: str,
    *,
    client_challenge: str,
    server_challenge: str,
    session_sha256: str,
    bindings: Mapping[str, str],
) -> bytes:
    material = {
        "domain": "context-vault/local-provider-capture-session/v1",
        "client_challenge": client_challenge,
        "server_challenge": server_challenge,
        "session_sha256": session_sha256,
        "bindings": dict(bindings),
    }
    return hmac.new(bytes.fromhex(nonce), _canonical(material), hashlib.sha256).digest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _safe_sha(value: Any, name: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise CaptureBoundaryError(f"{name} binding is invalid")
    return value


def _stable_read(path: Path, *, maximum: int, name: str) -> bytes:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise CaptureBoundaryError(f"{name} is unavailable or unsafe") from exc
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= maximum:
            raise CaptureBoundaryError(f"{name} is not a bounded regular file")
        blocks: list[bytes] = []
        remaining = before.st_size
        while remaining:
            block = os.read(fd, min(1024 * 1024, remaining))
            if not block:
                raise CaptureBoundaryError(f"{name} changed while being read")
            blocks.append(block)
            remaining -= len(block)
        after = os.fstat(fd)
        identity = lambda item: (  # noqa: E731 - compact stable identity
            item.st_dev,
            item.st_ino,
            item.st_size,
            item.st_mtime_ns,
        )
        if identity(before) != identity(after):
            raise CaptureBoundaryError(f"{name} changed while being read")
        return b"".join(blocks)
    finally:
        os.close(fd)


def read_nonce(path: Path) -> str:
    payload = _stable_read(path, maximum=256, name="capture nonce")
    try:
        value = payload.decode("ascii").strip()
    except UnicodeDecodeError as exc:
        raise CaptureBoundaryError("capture nonce is invalid") from exc
    if not _NONCE.fullmatch(value):
        raise CaptureBoundaryError("capture nonce is invalid")
    return value


def _write_exclusive(path: Path, value: Mapping[str, Any]) -> None:
    if path.exists() or path.is_symlink() or not path.parent.is_dir():
        raise CaptureBoundaryError("capture evidence output must be a new safe path")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(path, flags, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            os.fchmod(handle.fileno(), 0o600)
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
    except OSError as exc:
        raise CaptureBoundaryError("capture evidence could not be written") from exc


def _send_frame(connection: socket.socket, value: Mapping[str, Any]) -> None:
    payload = _canonical(value) + b"\n"
    if len(payload) > MAX_FRAME_BYTES:
        raise CaptureBoundaryError("capture protocol response is oversized")
    connection.sendall(payload)


def _receive_frame(connection: socket.socket) -> dict[str, Any]:
    chunks: list[bytes] = []
    total = 0
    while True:
        block = connection.recv(min(1024 * 1024, MAX_FRAME_BYTES + 1 - total))
        if not block:
            break
        chunks.append(block)
        total += len(block)
        if total > MAX_FRAME_BYTES:
            raise CaptureBoundaryError("capture protocol request is oversized")
        if b"\n" in block:
            break
    payload = b"".join(chunks)
    if not payload.endswith(b"\n") or payload.count(b"\n") != 1:
        raise CaptureBoundaryError("capture protocol frame is malformed")
    try:
        value = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CaptureBoundaryError("capture protocol frame is malformed") from exc
    if not isinstance(value, dict):
        raise CaptureBoundaryError("capture protocol frame is malformed")
    return value


def _safe_bindings(value: Any) -> dict[str, str]:
    if not isinstance(value, dict) or set(value) != _BINDING_FIELDS:
        raise CaptureBoundaryError("capture bindings are malformed")
    result: dict[str, str] = {}
    for name in _BINDING_FIELDS:
        item = value[name]
        if name == "repository_revision":
            if not isinstance(item, str) or not re.fullmatch(r"[a-f0-9]{40}", item):
                raise CaptureBoundaryError("capture source revision is invalid")
        elif name.endswith("sha256") or name == "environment_hash":
            _safe_sha(item, name)
        elif not isinstance(item, str) or not item.strip() or len(item) > 200:
            raise CaptureBoundaryError("capture provider binding is invalid")
        result[name] = item
    return result


def _normalize_capture(frame: dict[str, Any], sequence: int) -> dict[str, Any]:
    if set(frame) != {"command", "case_id_sha256", "event", "raw_payload"}:
        raise CaptureBoundaryError("capture request fields are malformed")
    event = frame["event"]
    raw = frame["raw_payload"]
    if not isinstance(event, dict):
        raise CaptureBoundaryError("capture request event is malformed")
    kind = event.get("kind")
    if kind not in _KINDS:
        raise CaptureBoundaryError("capture request kind is invalid")
    case_id_sha256 = _safe_sha(frame["case_id_sha256"], "case id")
    ordinal = event.get("ordinal")
    model = event.get("model")
    if (
        not isinstance(ordinal, int)
        or isinstance(ordinal, bool)
        or ordinal < 1
        or not isinstance(model, str)
        or not model.strip()
        or len(model) > 200
    ):
        raise CaptureBoundaryError("capture request metadata is malformed")
    if kind in {"embedding", "query"}:
        expected_fields = {
            "ordinal",
            "kind",
            "model",
            "item_count",
            "byte_count",
            "token_count",
            "payload_sha256",
        }
        if (
            set(event) != expected_fields
            or not isinstance(raw, list)
            or not raw
            or any(not isinstance(item, str) or not item for item in raw)
        ):
            raise CaptureBoundaryError("capture embedding payload is malformed")
        canonical = json.dumps(raw, ensure_ascii=False, separators=(",", ":")).encode()
        if (
            event["item_count"] != len(raw)
            or event["byte_count"] != sum(len(item.encode()) for item in raw)
            or not isinstance(event["token_count"], int)
            or isinstance(event["token_count"], bool)
            or event["token_count"] < 0
        ):
            raise CaptureBoundaryError(
                "capture embedding metadata does not match payload"
            )
        safe_counts: dict[str, Any] = {
            "item_count": event["item_count"],
            "byte_count": event["byte_count"],
            "token_count": event["token_count"],
        }
    else:
        expected_fields = {
            "ordinal",
            "kind",
            "model",
            "field_names",
            "byte_counts",
            "token_counts",
            "payload_sha256",
        }
        if (
            set(event) != expected_fields
            or not isinstance(raw, dict)
            or not raw
            or any(
                not isinstance(key, str) or not isinstance(item, str)
                for key, item in raw.items()
            )
            or event["field_names"] != sorted(raw)
            or event["byte_counts"]
            != {key: len(raw[key].encode()) for key in sorted(raw)}
            or not isinstance(event["token_counts"], dict)
            or set(event["token_counts"]) != {"prompt", "maximum_output"}
            or any(
                not isinstance(item, int) or isinstance(item, bool) or item < 0
                for item in event["token_counts"].values()
            )
        ):
            raise CaptureBoundaryError("capture generation payload is malformed")
        canonical = _canonical(raw)
        safe_counts = {
            "field_count": len(raw),
            "byte_count": sum(event["byte_counts"].values()),
            "prompt_tokens": event["token_counts"]["prompt"],
            "maximum_output_tokens": event["token_counts"]["maximum_output"],
        }
    payload_sha256 = hashlib.sha256(canonical).hexdigest()
    if event.get("payload_sha256") != payload_sha256:
        raise CaptureBoundaryError("capture payload digest mismatch")
    return {
        "sequence": sequence,
        "case_id_sha256": case_id_sha256,
        "provider_ordinal": ordinal,
        "kind": kind,
        "model": model,
        "payload_sha256": payload_sha256,
        **safe_counts,
    }


class CaptureClient:
    """Blocking client used immediately before each local model invocation."""

    def __init__(
        self,
        socket_path: Path,
        nonce: str,
        bindings: Mapping[str, str],
        *,
        timeout: float = 10.0,
    ) -> None:
        if not _NONCE.fullmatch(nonce):
            raise CaptureBoundaryError("capture nonce is invalid")
        self.socket_path = Path(socket_path)
        self.bindings = _safe_bindings(dict(bindings))
        self.timeout = timeout
        self.case_id_sha256: str | None = None
        self.request_count = 0
        self._connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._connection.settimeout(self.timeout)
        try:
            self._connection.connect(str(self.socket_path))
        except (OSError, TimeoutError) as exc:
            self._connection.close()
            raise CaptureBoundaryError("capture collector is unavailable") from exc
        client_challenge = os.urandom(32).hex()
        hello = {
            "command": "hello",
            "client_challenge": client_challenge,
            "bindings": self.bindings,
        }
        _send_frame(
            self._connection,
            {**hello, "auth": _auth_tag(bytes.fromhex(nonce), hello)},
        )
        response = _receive_frame(self._connection)
        if (
            set(response) != {"status", "session_sha256", "server_challenge", "auth"}
            or response["status"] != "ready"
        ):
            raise CaptureBoundaryError("capture collector handshake failed")
        self.session_sha256 = _safe_sha(response["session_sha256"], "capture session")
        server_challenge = _safe_sha(
            response["server_challenge"], "capture server challenge"
        )
        response_body = {name: response[name] for name in response if name != "auth"}
        expected_auth = _auth_tag(
            bytes.fromhex(nonce),
            {
                **response_body,
                "client_challenge": client_challenge,
                "bindings": self.bindings,
            },
        )
        if not isinstance(response["auth"], str) or not hmac.compare_digest(
            response["auth"], expected_auth
        ):
            self._connection.close()
            raise CaptureBoundaryError("capture collector authentication failed")
        self._session_key = _session_key(
            nonce,
            client_challenge=client_challenge,
            server_challenge=server_challenge,
            session_sha256=self.session_sha256,
            bindings=self.bindings,
        )

    def _request(self, value: Mapping[str, Any]) -> dict[str, Any]:
        sequence = self.request_count + 1
        body = {
            **dict(value),
            "session_sha256": self.session_sha256,
            "sequence": sequence,
        }
        try:
            _send_frame(
                self._connection,
                {**body, "auth": _auth_tag(self._session_key, body)},
            )
            response = _receive_frame(self._connection)
        except (OSError, TimeoutError) as exc:
            raise CaptureBoundaryError("capture collector is unavailable") from exc
        if not isinstance(response.get("auth"), str):
            raise CaptureBoundaryError("capture acknowledgement is unauthenticated")
        response_body = {name: response[name] for name in response if name != "auth"}
        if not hmac.compare_digest(
            response["auth"], _auth_tag(self._session_key, response_body)
        ):
            raise CaptureBoundaryError("capture acknowledgement authentication failed")
        if (
            response_body.get("session_sha256") != self.session_sha256
            or response_body.get("sequence") != sequence
        ):
            raise CaptureBoundaryError("capture acknowledgement session is invalid")
        return response_body

    def select(self, case_id: str) -> None:
        if not isinstance(case_id, str) or not case_id:
            raise CaptureBoundaryError("capture case identity is invalid")
        self.case_id_sha256 = hashlib.sha256(case_id.encode()).hexdigest()

    def capture(self, event: dict[str, Any], raw_payload: Any) -> str:
        if self.case_id_sha256 is None:
            raise CaptureBoundaryError("capture request has no selected case")
        response = self._request(
            {
                "command": "capture",
                "case_id_sha256": self.case_id_sha256,
                "event": event,
                "raw_payload": raw_payload,
            }
        )
        if (
            set(response) != {"status", "capture_sha256", "sequence", "session_sha256"}
            or response["status"] != "captured"
        ):
            raise CaptureBoundaryError("capture acknowledgement is malformed")
        if response["sequence"] != self.request_count + 1:
            raise CaptureBoundaryError("capture acknowledgement sequence is invalid")
        self.request_count += 1
        return _safe_sha(response["capture_sha256"], "capture acknowledgement")

    def seal_and_read_labels(
        self, expected_request_count: int
    ) -> tuple[bytes, dict[str, Any]]:
        if expected_request_count != self.request_count:
            raise CaptureBoundaryError("capture client request count mismatch")
        response = self._request(
            {
                "command": "seal",
                "expected_request_count": expected_request_count,
            }
        )
        required = {
            "status",
            "golden_payload_base64",
            "session_sha256",
            "ordered_request_digest",
            "request_count",
            "sequence",
        }
        if set(response) != required or response["status"] != "sealed":
            raise CaptureBoundaryError("capture seal acknowledgement is malformed")
        if response["session_sha256"] != self.session_sha256:
            raise CaptureBoundaryError("capture session changed")
        _safe_sha(response["ordered_request_digest"], "ordered request digest")
        if response["request_count"] != expected_request_count:
            raise CaptureBoundaryError("capture request count mismatch")
        try:
            golden = base64.b64decode(response["golden_payload_base64"], validate=True)
        except (TypeError, ValueError) as exc:
            raise CaptureBoundaryError("sealed golden payload is malformed") from exc
        self._connection.close()
        return golden, {
            "session_sha256": self.session_sha256,
            "ordered_request_digest": response["ordered_request_digest"],
            "request_count": response["request_count"],
        }


def serve(
    *,
    socket_path: Path,
    nonce: str,
    golden_path: Path,
    evidence_path: Path,
    bindings: Mapping[str, str],
) -> dict[str, Any]:
    safe_bindings = _safe_bindings(dict(bindings))
    if (
        socket_path.exists()
        or socket_path.is_symlink()
        or not socket_path.parent.is_dir()
    ):
        raise CaptureBoundaryError("capture socket path is unavailable or unsafe")
    if evidence_path.exists() or evidence_path.is_symlink():
        raise CaptureBoundaryError("capture evidence output must be new")
    session_sha256 = hashlib.sha256(os.urandom(32)).hexdigest()
    event_hashes: list[str] = []
    kind_counts: Counter[str] = Counter()
    ordinals: dict[str, int] = {}
    handshaken = False
    sealed = False
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        listener.bind(str(socket_path))
        os.chmod(socket_path, 0o600)
        listener.listen(8)
        connection, _ = listener.accept()
        with connection:
            hello_frame = _receive_frame(connection)
            if (
                set(hello_frame) != {"command", "client_challenge", "bindings", "auth"}
                or hello_frame.get("command") != "hello"
            ):
                raise CaptureBoundaryError("capture handshake is malformed")
            hello = {name: hello_frame[name] for name in hello_frame if name != "auth"}
            client_challenge = _safe_sha(
                hello["client_challenge"], "capture client challenge"
            )
            if not isinstance(hello_frame["auth"], str) or not hmac.compare_digest(
                hello_frame["auth"], _auth_tag(bytes.fromhex(nonce), hello)
            ):
                raise CaptureBoundaryError("capture authentication failed")
            if _safe_bindings(hello["bindings"]) != safe_bindings:
                raise CaptureBoundaryError("capture binding mismatch")
            server_challenge = os.urandom(32).hex()
            ready = {
                "status": "ready",
                "session_sha256": session_sha256,
                "server_challenge": server_challenge,
            }
            ready_binding = {
                **ready,
                "client_challenge": client_challenge,
                "bindings": safe_bindings,
            }
            _send_frame(
                connection,
                {**ready, "auth": _auth_tag(bytes.fromhex(nonce), ready_binding)},
            )
            session_key = _session_key(
                nonce,
                client_challenge=client_challenge,
                server_challenge=server_challenge,
                session_sha256=session_sha256,
                bindings=safe_bindings,
            )
            handshaken = True
            expected_sequence = 1
            while not sealed:
                wire_frame = _receive_frame(connection)
                if not isinstance(wire_frame.get("auth"), str):
                    raise CaptureBoundaryError("capture frame is unauthenticated")
                frame = {
                    name: wire_frame[name] for name in wire_frame if name != "auth"
                }
                if not hmac.compare_digest(
                    wire_frame["auth"], _auth_tag(session_key, frame)
                ):
                    raise CaptureBoundaryError("capture frame authentication failed")
                if (
                    frame.pop("session_sha256", None) != session_sha256
                    or frame.pop("sequence", None) != expected_sequence
                ):
                    raise CaptureBoundaryError("capture frame session is invalid")
                command = frame.get("command")
                if command == "capture":
                    if not handshaken or len(event_hashes) >= MAX_REQUESTS:
                        raise CaptureBoundaryError("capture request is not admitted")
                    safe_event = _normalize_capture(frame, len(event_hashes) + 1)
                    expected_ordinal = ordinals.get(safe_event["model"], 0) + 1
                    if safe_event["provider_ordinal"] != expected_ordinal:
                        raise CaptureBoundaryError(
                            "capture provider ordinal is not contiguous"
                        )
                    ordinals[safe_event["model"]] = expected_ordinal
                    capture_sha256 = _domain_hash(
                        "context-vault/independent-local-request-capture/v1", safe_event
                    )
                    event_hashes.append(capture_sha256)
                    kind_counts[safe_event["kind"]] += 1
                    del safe_event
                    response = {
                        "status": "captured",
                        "capture_sha256": capture_sha256,
                        "sequence": expected_sequence,
                        "session_sha256": session_sha256,
                    }
                    _send_frame(
                        connection,
                        {**response, "auth": _auth_tag(session_key, response)},
                    )
                    expected_sequence += 1
                elif command == "seal":
                    if (
                        not handshaken
                        or set(frame) != {"command", "expected_request_count"}
                        or not isinstance(frame["expected_request_count"], int)
                        or isinstance(frame["expected_request_count"], bool)
                        or frame["expected_request_count"] != len(event_hashes)
                        or not event_hashes
                    ):
                        raise CaptureBoundaryError("capture seal count is invalid")
                    requests_sealed_at = _utc_now()
                    ordered_digest = _domain_hash(
                        "context-vault/independent-local-request-ledger/v1",
                        event_hashes,
                    )
                    golden_first_open_at = _utc_now()
                    golden = _stable_read(
                        golden_path, maximum=MAX_GOLDEN_BYTES, name="golden dataset"
                    )
                    if (
                        hashlib.sha256(golden).hexdigest()
                        != safe_bindings["golden_dataset_sha256"]
                    ):
                        raise CaptureBoundaryError("golden dataset hash mismatch")
                    evidence = {
                        "schema_version": "1.0",
                        "evidence_type": "local-provider-request-boundary-capture",
                        "status": "SEALED_BEFORE_GOLDEN_OPEN",
                        "collector_sha256": hashlib.sha256(
                            Path(__file__).read_bytes()
                        ).hexdigest(),
                        **safe_bindings,
                        "request_count": len(event_hashes),
                        "request_kind_counts": dict(sorted(kind_counts.items())),
                        "ordered_request_digest": ordered_digest,
                        "session_sha256": session_sha256,
                        "requests_sealed_at_utc": requests_sealed_at,
                        "golden_first_open_at_utc": golden_first_open_at,
                        "raw_request_retained": False,
                        "raw_response_retained": False,
                        "golden_payload_retained": False,
                    }
                    _write_exclusive(evidence_path, evidence)
                    response = {
                        "status": "sealed",
                        "golden_payload_base64": base64.b64encode(golden).decode(
                            "ascii"
                        ),
                        "session_sha256": session_sha256,
                        "ordered_request_digest": ordered_digest,
                        "request_count": len(event_hashes),
                        "sequence": expected_sequence,
                    }
                    _send_frame(
                        connection,
                        {**response, "auth": _auth_tag(session_key, response)},
                    )
                    del golden
                    sealed = True
                else:
                    raise CaptureBoundaryError("capture protocol command is invalid")
        return evidence
    finally:
        listener.close()
        try:
            socket_path.unlink()
        except FileNotFoundError:
            pass


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--socket", type=Path, required=True)
    parser.add_argument("--nonce-file", type=Path, required=True)
    parser.add_argument("--golden-dataset", type=Path, required=True)
    parser.add_argument("--evidence-output", type=Path, required=True)
    for name in sorted(_BINDING_FIELDS):
        parser.add_argument("--" + name.replace("_", "-"), required=True)
    args = parser.parse_args()
    try:
        bindings = {name: getattr(args, name) for name in _BINDING_FIELDS}
        serve(
            socket_path=args.socket,
            nonce=read_nonce(args.nonce_file),
            golden_path=args.golden_dataset,
            evidence_path=args.evidence_output,
            bindings=bindings,
        )
    except CaptureBoundaryError:
        print("local provider capture failed closed", file=sys.stderr)
        return 2
    except Exception:
        print("local provider capture failed closed", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
