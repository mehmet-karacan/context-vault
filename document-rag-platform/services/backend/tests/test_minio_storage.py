"""Aşama 2.2: integration tests for the MinIO ``ObjectStorage`` adaptör.

Runs against the real MinIO container from docker-compose.yml (no mocking)
using ``src.config.settings`` for connection info — the same settings the
app itself would use. All objects are written under a dedicated
``projects/__test__/...`` prefix and deleted at the end of each test so this
suite never touches real project/document data.

Skips cleanly (rather than failing) if MinIO is unreachable, so the rest of
the suite still runs in environments without the compose stack up.
"""

from __future__ import annotations

import uuid

import pytest

from src.config import settings
from src.domain import ports
from src.infrastructure.storage import object_keys
from src.infrastructure.storage.minio_storage import MinioObjectStorage

TEST_PROJECT_ID = "__test__"


def _make_storage() -> MinioObjectStorage:
    return MinioObjectStorage(
        endpoint=settings.MINIO_ENDPOINT,
        access_key=settings.MINIO_ACCESS_KEY,
        secret_key=settings.MINIO_SECRET_KEY,
        bucket=settings.MINIO_BUCKET,
    )


@pytest.fixture()
def storage():
    store = _make_storage()
    try:
        store._ensure_bucket()
    except Exception as exc:  # pragma: no cover - environment guard
        pytest.skip(f"MinIO not reachable at {settings.MINIO_ENDPOINT}: {exc}")
    yield store


@pytest.fixture()
def test_key():
    """A key under the __test__ prefix, cleaned up after the test runs."""
    document_id = str(uuid.uuid4())
    version_id = str(uuid.uuid4())
    key = object_keys.original_key(
        TEST_PROJECT_ID, document_id, version_id, "resim adı öğe#1 (final).txt"
    )
    yield key
    # Best-effort cleanup: ignore failures so a prior assertion failure in
    # the test itself is what gets reported, not a cleanup error.
    try:
        _make_storage().delete(key)
    except Exception:
        pass


def test_minio_storage_conforms_to_object_storage_port():
    assert isinstance(_make_storage(), ports.ObjectStorage)


def test_put_then_exists_then_get_then_delete_roundtrip(storage, test_key):
    payload = b"context-vault minio adaptor smoke test \xf0\x9f\x93\xa6"

    assert storage.exists(test_key) is False

    returned_key = storage.put(test_key, payload, content_type="text/plain")
    assert returned_key == test_key

    assert storage.exists(test_key) is True

    fetched = storage.get(test_key)
    assert fetched == payload

    storage.delete(test_key)
    assert storage.exists(test_key) is False


def test_delete_is_idempotent_for_a_missing_object(storage, test_key):
    # Deleting something that was never written must not raise (matches
    # S3/MinIO idempotent-delete semantics), so cleanup code never needs an
    # existence check first.
    storage.delete(test_key)
    storage.delete(test_key)


def test_get_missing_object_raises(storage, test_key):
    with pytest.raises(Exception):
        storage.get(test_key)


def test_bucket_is_created_idempotently():
    # Constructing + using the adaptor twice (fresh instances, as two
    # different app processes would) must not fail even though the bucket
    # already exists from a previous run.
    first = _make_storage()
    second = _make_storage()
    try:
        first._ensure_bucket()
        second._ensure_bucket()
    except Exception as exc:  # pragma: no cover - environment guard
        pytest.skip(f"MinIO not reachable at {settings.MINIO_ENDPOINT}: {exc}")


# --- object_keys ------------------------------------------------------------


def test_object_key_layout_matches_aktif_gorev_standard():
    p, d, v = "proj-1", "doc-1", "ver-1"

    assert (
        object_keys.original_key(p, d, v, "rapor.docx")
        == "projects/proj-1/documents/doc-1/versions/ver-1/original/rapor.docx"
    )
    assert (
        object_keys.normalized_json_key(p, d, v)
        == "projects/proj-1/documents/doc-1/versions/ver-1/normalized/document.json"
    )
    assert (
        object_keys.normalized_markdown_key(p, d, v)
        == "projects/proj-1/documents/doc-1/versions/ver-1/normalized/document.md"
    )
    assert (
        object_keys.artifact_key(p, d, v, "pages/0007/thumbnail.png")
        == "projects/proj-1/documents/doc-1/versions/ver-1/artifacts/pages/0007/thumbnail.png"
    )


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("rapor.docx", "rapor.docx"),
        ("../../etc/passwd", "passwd"),
        ("..\\..\\windows\\system32\\config", "config"),
        ("/etc/passwd", "passwd"),
        ("", "file"),
        (".", "file"),
        ("..", "file"),
        ("a/b/../../c.txt", "c.txt"),
    ],
)
def test_safe_filename_blocks_path_traversal(raw, expected):
    assert object_keys.safe_filename(raw) == expected


def test_safe_filename_strips_unsafe_characters():
    sanitized = object_keys.safe_filename('weird<>:"|?*name.txt')
    assert sanitized.endswith(".txt")
    for bad_char in '<>:"|?*':
        assert bad_char not in sanitized


def test_safe_filename_strips_control_and_null_bytes():
    sanitized = object_keys.safe_filename("evil\x00name\x01.txt")
    assert "\x00" not in sanitized
    assert "\x01" not in sanitized


def test_segment_helpers_reject_path_separators_in_ids():
    with pytest.raises(ValueError):
        object_keys.original_key("proj/../escape", "doc-1", "ver-1", "f.txt")
    with pytest.raises(ValueError):
        object_keys.original_key("proj-1", "doc-1", "", "f.txt")
