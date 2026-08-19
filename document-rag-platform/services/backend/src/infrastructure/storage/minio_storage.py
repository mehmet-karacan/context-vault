"""MinIO ``ObjectStorage`` adaptör (Aşama 2.2).

Implements ``domain.ports.ObjectStorage`` against a MinIO/S3-compatible
endpoint using the official ``minio`` SDK. This module is intentionally
standalone: nothing in the existing upload flow (``api/v1/documents.py``)
imports it yet — it will be wired in when the upload endpoint moves to the
job-based ingestion pipeline (see AKTIF_GOREV.md Aşama 2).

Connection settings come from ``src.config.Settings``
(``MINIO_ENDPOINT`` / ``MINIO_ACCESS_KEY`` / ``MINIO_SECRET_KEY`` /
``MINIO_BUCKET``), which are already injected into the ``backend`` and
``worker`` containers by ``docker-compose.yml``.
"""

from __future__ import annotations

import io
from typing import Optional
from urllib.parse import urlsplit

from minio import Minio
from minio.error import S3Error


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
        stream = io.BytesIO(data)
        self._client.put_object(
            self._bucket,
            key,
            stream,
            length=len(data),
            content_type=content_type or "application/octet-stream",
        )
        return key

    def get(self, key: str) -> bytes:
        """Reads an object's full content into memory."""
        self._ensure_bucket()
        response = self._client.get_object(self._bucket, key)
        try:
            return response.read()
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
