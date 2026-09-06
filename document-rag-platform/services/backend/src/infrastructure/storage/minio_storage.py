"""MinIO ``ObjectStorage`` adaptör (Aşama 2.2).

Implements ``domain.ports.ObjectStorage`` against a MinIO/S3-compatible
endpoint using the official ``minio`` SDK. Every new object is encrypted with
an authenticated AES-256-GCM envelope before it leaves the application.

Connection settings come from ``src.config.Settings``
(``MINIO_ENDPOINT`` / ``MINIO_ACCESS_KEY`` / ``MINIO_SECRET_KEY`` /
``MINIO_BUCKET``), which are already injected into the ``backend`` and
``worker`` containers by ``docker-compose.yml``.
"""

from __future__ import annotations

import base64
import io
import os
from typing import Optional
from urllib.parse import urlsplit

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from minio import Minio
from minio.error import S3Error


_ENCRYPTED_MAGIC = b"CVENC1\x00"
_NONCE_BYTES = 12


def decode_encryption_key(value: str | bytes | None) -> bytes:
    """Decode and validate a base64-encoded 256-bit storage key."""
    if isinstance(value, bytes):
        key = value
    elif value:
        try:
            key = base64.b64decode(value, validate=True)
        except (ValueError, TypeError) as exc:
            raise ValueError(
                "object-storage encryption key is not valid base64"
            ) from exc
    else:
        raise ValueError("object-storage encryption key is required")
    if len(key) != 32:
        raise ValueError("object-storage encryption key must decode to 32 bytes")
    return key


def _split_endpoint(endpoint: str) -> tuple[str, bool]:
    """Splits a ``http://host:port`` / ``https://host:port`` endpoint into
    the bare ``host:port`` the ``minio`` SDK expects plus a ``secure`` flag.

    Also accepts a bare ``host:port`` (no scheme) for convenience, defaulting
    to non-TLS in that case.
    """
    if "://" not in endpoint:
        return endpoint, False
    parts = urlsplit(endpoint)
    secure = parts.scheme == "https"
    netloc = parts.netloc or parts.path
    return netloc, secure


class MinioObjectStorage:
    """Stores and retrieves immutable binary artifacts in MinIO.

    Conforms to ``domain.ports.ObjectStorage``: ``put``, ``get``, ``delete``,
    ``exists``. The target bucket is created lazily (idempotent) on first
    use rather than at import time, so constructing this class never makes a
    network call.
    """

    def __init__(
        self,
        endpoint: str,
        access_key: str,
        secret_key: str,
        bucket: str,
        secure: Optional[bool] = None,
        encryption_key: str | bytes | None = None,
        allow_legacy_plaintext_reads: bool = False,
    ):
        host_port, inferred_secure = _split_endpoint(endpoint)
        self._bucket = bucket
        self._client = Minio(
            host_port,
            access_key=access_key,
            secret_key=secret_key,
            secure=inferred_secure if secure is None else secure,
        )
        self._bucket_ready = False
        self._encryption_key = decode_encryption_key(encryption_key)
        self._allow_legacy_plaintext_reads = allow_legacy_plaintext_reads

    def _encrypt(self, key: str, data: bytes) -> bytes:
        nonce = os.urandom(_NONCE_BYTES)
        ciphertext = AESGCM(self._encryption_key).encrypt(
            nonce, data, key.encode("utf-8")
        )
        return _ENCRYPTED_MAGIC + nonce + ciphertext

    def _decrypt(self, key: str, payload: bytes) -> bytes:
        if not payload.startswith(_ENCRYPTED_MAGIC):
            if self._allow_legacy_plaintext_reads:
                return payload
            raise ValueError("unencrypted legacy object is blocked by storage policy")
        offset = len(_ENCRYPTED_MAGIC)
        nonce = payload[offset : offset + _NONCE_BYTES]
        ciphertext = payload[offset + _NONCE_BYTES :]
        if len(nonce) != _NONCE_BYTES or not ciphertext:
            raise ValueError("encrypted object envelope is malformed")
        try:
            return AESGCM(self._encryption_key).decrypt(
                nonce, ciphertext, key.encode("utf-8")
            )
        except InvalidTag as exc:
            raise ValueError("encrypted object authentication failed") from exc

    def _ensure_bucket(self) -> None:
        """Creates the configured bucket if it doesn't already exist.

        Idempotent and safe to call on every operation: ``bucket_exists`` is
        a cheap HEAD-style call, and ``make_bucket`` is only invoked when the
        bucket is genuinely missing. Cached after the first successful check
        so steady-state calls don't pay the extra round-trip.
        """
        if self._bucket_ready:
            return
        if not self._client.bucket_exists(self._bucket):
            self._client.make_bucket(self._bucket)
        self._bucket_ready = True

    def put(self, key: str, data: bytes, content_type: Optional[str] = None) -> str:
        """Writes an object and returns its storage key.

        ``data`` is buffered in memory as a ``BytesIO`` stream — acceptable
        for document-sized artifacts (bounded by ``MAX_DOCUMENT_BYTES`` at
        the ingestion layer); a true streaming upload can be added later if
        needed without changing this signature.
        """
        self._ensure_bucket()
        encrypted = self._encrypt(key, data)
        stream = io.BytesIO(encrypted)
        self._client.put_object(
            self._bucket,
            key,
            stream,
            length=len(encrypted),
            content_type=content_type or "application/octet-stream",
            metadata={"context-vault-encryption": "aes-256-gcm-v1"},
        )
        return key

    def get(self, key: str) -> bytes:
        """Reads an object's full content into memory."""
        self._ensure_bucket()
        response = self._client.get_object(self._bucket, key)
        try:
            return self._decrypt(key, response.read())
        finally:
            response.close()
            response.release_conn()

    def is_encrypted(self, key: str) -> bool:
        """Read-only audit probe for the Context Vault envelope marker."""
        self._ensure_bucket()
        response = self._client.get_object(self._bucket, key)
        try:
            return response.read(len(_ENCRYPTED_MAGIC)) == _ENCRYPTED_MAGIC
        finally:
            response.close()
            response.release_conn()

    def delete(self, key: str) -> None:
        """Deletes a single object. No-op (does not raise) if it is already
        absent, matching S3/MinIO's own idempotent-delete semantics."""
        self._ensure_bucket()
        self._client.remove_object(self._bucket, key)

    def exists(self, key: str) -> bool:
        """Returns whether an object exists at ``key``."""
        self._ensure_bucket()
        try:
            self._client.stat_object(self._bucket, key)
            return True
        except S3Error as exc:
            if exc.code in ("NoSuchKey", "NoSuchObject"):
                return False
            raise

    def list_keys(self, prefix: str = "") -> list[str]:
        """Return a deterministic key inventory for reconciliation tooling."""
        return sorted(self.iter_keys(prefix=prefix))

    def iter_keys(self, prefix: str = ""):
        """Stream object keys for bounded observers without materializing a bucket."""
        self._ensure_bucket()
        return (
            obj.object_name
            for obj in self._client.list_objects(
                self._bucket, prefix=prefix, recursive=True
            )
        )

    def list_entries(self, prefix: str = "") -> list[tuple[str, object]]:
        """Return `(key, last_modified)` pairs for grace-period sweepers."""
        self._ensure_bucket()
        return sorted(
            (
                (obj.object_name, obj.last_modified)
                for obj in self._client.list_objects(
                    self._bucket, prefix=prefix, recursive=True
                )
            ),
            key=lambda item: item[0],
        )
