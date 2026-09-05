#!/usr/bin/env python3
"""Load a private benchmark pack without exposing golden labels before requests."""

from __future__ import annotations

import base64
import binascii
import copy
import hashlib
import json
import os
import re
import stat
from pathlib import Path
from typing import Any, Iterable


TOOL_VERSION = "1.0.0"
REPO = Path(__file__).resolve().parents[1]
EXECUTION_PATH = "execution/cases.jsonl"
LABEL_PATH = "labels/golden.jsonl"
PACK_PATHS = (EXECUTION_PATH, LABEL_PATH)
EXPECTED_ENTRIES = {
    "execution",
    EXECUTION_PATH,
    "labels",
    LABEL_PATH,
}

MAX_PACK_FILES = 2
MAX_FILE_BYTES = 8_000_000
MAX_TOTAL_BYTES = 16_000_000
MAX_RECORDS = 10_000
MAX_QUERY_TYPES = 100
MAX_QUERY_TYPE_BYTES = 64
MAX_RECORD_ID_BYTES = 128
MAX_QUERY_BYTES = 16_384
MAX_SCOPE_BYTES = 256
MAX_PERSONA_BYTES = 256
MAX_DOCUMENTS_PER_CASE = 128
MAX_DOCUMENT_BYTES = 8_000_000
MAX_TOTAL_DOCUMENT_BYTES = 64_000_000
MAX_STRING_BYTES = 8_192
MAX_LABEL_ITEMS = 256

EXECUTION_FIELDS = {
    "id",
    "query",
    "intent",
    "workspace_fixture",
    "project_fixture",
    "scope",
    "permission_persona",
    "query_type",
    "language",
    "documents",
}
DOCUMENT_FIELDS = {
    "document_id",
    "filename",
    "mime_type",
    "source_type",
    "content_base64",
    "classification",
}
LABEL_FIELDS = {
    "id",
    "answerable",
    "expected_facts",
    "expected_source_constraints",
    "forbidden_sources",
    "adversarial_tags",
    "notes",
    "reviewer",
    "dataset_version",
    "split",
}
SOURCE_CONSTRAINT_FIELDS = {
    "document_id",
    "active_version",
    "locator",
    "symbol",
    "must_contain",
}
LEDGER_FIELDS = {"id", "request_sha256"}

_RECORD_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]*\Z")
_STABLE_NAME = re.compile(r"[a-z0-9][a-z0-9._-]{0,127}\Z")
_QUERY_TYPE = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}\Z")
_LANGUAGE = re.compile(r"[a-z]{2,3}(?:-[A-Z]{2})?\Z")
_DATASET_VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+\Z")
_SHA256 = re.compile(r"[a-f0-9]{64}\Z")
_MIME_TYPE = re.compile(
    r"[A-Za-z0-9][A-Za-z0-9!#$&^_.+-]*/[A-Za-z0-9][A-Za-z0-9!#$&^_.+-]*\Z"
)

BUNDLE_DOMAIN = b"context-vault/local-benchmark-pack/bundle/v1\x00"
EXECUTION_DOMAIN = b"context-vault/local-benchmark-pack/execution/v1\x00"
REQUEST_DOMAIN = b"context-vault/local-benchmark-pack/request/v1\x00"
LEDGER_DOMAIN = b"context-vault/local-benchmark-pack/request-ledger/v1\x00"
REQUEST_EVENTS_DOMAIN = b"context-vault/local-benchmark-pack/request-events/v1\x00"
ALLOWED_SCOPES = {"all", "documents", "images", "code"}
ALLOWED_CLASSIFICATIONS = {"internal", "confidential", "restricted"}
ALLOWED_SOURCE_TYPES = {"document", "image", "repository", "directory", "archive"}


class LocalBenchmarkPackError(RuntimeError):
    """The private benchmark pack did not satisfy its local runtime contract."""


def _under(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _framed(digest: Any, value: bytes) -> None:
    digest.update(len(value).to_bytes(8, "big"))
    digest.update(value)


def _domain_hash(domain: bytes, value: bytes) -> str:
    digest = hashlib.sha256(domain)
    _framed(digest, value)
    return digest.hexdigest()


def _read_regular(path: Path, *, maximum: int) -> bytes:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise LocalBenchmarkPackError(
            "benchmark pack file is unreadable or a symlink"
        ) from exc
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode):
            raise LocalBenchmarkPackError(
                "benchmark pack entries must be regular files"
            )
        if before.st_size > maximum:
            raise LocalBenchmarkPackError(
                "benchmark pack exceeds per-file byte-size limit"
            )
        blocks: list[bytes] = []
        total = 0
        while True:
            block = os.read(fd, min(1024 * 1024, maximum + 1 - total))
            if not block:
                break
            blocks.append(block)
            total += len(block)
            if total > maximum:
                raise LocalBenchmarkPackError(
                    "benchmark pack exceeds per-file byte-size limit"
                )
        after = os.fstat(fd)
        identity_before = (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
        )
        identity_after = (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        )
        if identity_before != identity_after or total != before.st_size:
            raise LocalBenchmarkPackError("benchmark pack changed while being read")
        return b"".join(blocks)
    finally:
        os.close(fd)


def _pack_root(root: Path) -> Path:
    lexical = root.absolute()
    if _under(lexical, REPO.resolve()):
        raise LocalBenchmarkPackError("benchmark pack root must be outside repository")
    if lexical.is_symlink():
        raise LocalBenchmarkPackError("benchmark pack root must not be a symlink")
    try:
        resolved = lexical.resolve(strict=True)
    except OSError as exc:
        raise LocalBenchmarkPackError("benchmark pack root is unavailable") from exc
    if _under(resolved, REPO.resolve()):
        raise LocalBenchmarkPackError("benchmark pack root must be outside repository")
    if not resolved.is_dir():
        raise LocalBenchmarkPackError("benchmark pack root must be a directory")
    return resolved


def _layout(root: Path) -> Path:
    resolved = _pack_root(root)
    entries: set[str] = set()
    try:
        for path in resolved.rglob("*"):
            relative = path.relative_to(resolved).as_posix()
            if path.is_symlink():
                raise LocalBenchmarkPackError(
                    "benchmark pack must not contain symlinks"
                )
            entries.add(relative)
    except OSError as exc:
        raise LocalBenchmarkPackError("benchmark pack layout is unreadable") from exc
    if entries != EXPECTED_ENTRIES:
        raise LocalBenchmarkPackError(
            "benchmark pack requires exact two-file layout: "
            f"{EXECUTION_PATH} and {LABEL_PATH}"
        )
    if not (resolved / "execution").is_dir() or not (resolved / "labels").is_dir():
        raise LocalBenchmarkPackError("benchmark pack layout directories are invalid")
    if len(PACK_PATHS) > MAX_PACK_FILES:
        raise LocalBenchmarkPackError("benchmark pack exceeds file-count limit")
    return resolved


def bundle_descriptor(root: Path) -> dict[str, Any]:
    """Hash the exact pack layout without parsing either JSONL file."""

    resolved = _layout(Path(root))
    digest = hashlib.sha256(BUNDLE_DOMAIN)
    file_sha256: dict[str, str] = {}
    sizes: dict[str, int] = {}
    total = 0
    for relative in PACK_PATHS:
        payload = _read_regular(resolved / relative, maximum=MAX_FILE_BYTES)
        total += len(payload)
        if total > MAX_TOTAL_BYTES:
            raise LocalBenchmarkPackError(
                "benchmark pack exceeds total byte-size limit"
            )
        file_hash = hashlib.sha256(payload).hexdigest()
        file_sha256[relative] = file_hash
        sizes[relative] = len(payload)
        _framed(digest, relative.encode("utf-8"))
        _framed(digest, len(payload).to_bytes(8, "big"))
        _framed(digest, bytes.fromhex(file_hash))
    return {
        "sha256": digest.hexdigest(),
        "files": len(PACK_PATHS),
        "bytes": total,
        "file_bytes": sizes,
        "file_sha256": file_sha256,
    }


def _no_duplicate_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise LocalBenchmarkPackError("JSONL object contains a duplicate field")
        result[key] = value
    return result


def _jsonl(payload: bytes, *, name: str) -> list[dict[str, Any]]:
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise LocalBenchmarkPackError(f"{name} must be UTF-8 JSONL") from exc
    if "\x00" in text:
        raise LocalBenchmarkPackError(f"{name} contains a forbidden NUL byte")
    lines = text.splitlines()
    if not lines:
        raise LocalBenchmarkPackError(f"{name} contains no records")
    if len(lines) > MAX_RECORDS:
        raise LocalBenchmarkPackError(f"{name} exceeds record-count limit")
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            raise LocalBenchmarkPackError(f"{name} contains a blank JSONL record")
        try:
            value = json.loads(line, object_pairs_hook=_no_duplicate_object)
        except (json.JSONDecodeError, LocalBenchmarkPackError) as exc:
            raise LocalBenchmarkPackError(
                f"{name} record {line_number} is invalid JSON"
            ) from exc
        if not isinstance(value, dict):
            raise LocalBenchmarkPackError(
                f"{name} record {line_number} must be a JSON object"
            )
        records.append(value)
    return records


def _bounded_string(value: Any, *, field: str, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise LocalBenchmarkPackError(f"{field} must be a nonblank string")
    if len(value.encode("utf-8")) > maximum:
        raise LocalBenchmarkPackError(f"{field} exceeds byte-size limit")
    return value


def _record_id(value: Any) -> str:
    result = _bounded_string(value, field="id", maximum=MAX_RECORD_ID_BYTES)
    if not _RECORD_ID.fullmatch(result):
        raise LocalBenchmarkPackError("id has invalid syntax")
    return result


def _stable_name(value: Any, *, field: str) -> str:
    result = _bounded_string(value, field=field, maximum=128)
    if not _STABLE_NAME.fullmatch(result):
        raise LocalBenchmarkPackError(f"{field} has invalid syntax")
    return result


def _query_type(value: Any) -> str:
    result = _bounded_string(value, field="query_type", maximum=MAX_QUERY_TYPE_BYTES)
    if not _QUERY_TYPE.fullmatch(result):
        raise LocalBenchmarkPackError("query_type has invalid syntax")
    return result


def aggregate_request_hashes(event_hashes: Iterable[str]) -> str:
    """Aggregate one case's observed provider-request event hashes."""

    try:
        hashes = list(event_hashes)
    except TypeError as exc:
        raise LocalBenchmarkPackError("request event hashes must be iterable") from exc
    if not hashes or len(hashes) > MAX_RECORDS:
        raise LocalBenchmarkPackError(
            "request event hashes must be nonempty and bounded"
        )
    digest = hashlib.sha256(REQUEST_EVENTS_DOMAIN)
    for value in hashes:
        if not isinstance(value, str) or not _SHA256.fullmatch(value):
            raise LocalBenchmarkPackError(
                "request event hash must be lowercase SHA-256"
            )
        _framed(digest, bytes.fromhex(value))
    return digest.hexdigest()


def _string_list(value: Any, *, field: str) -> list[str]:
    if not isinstance(value, list) or len(value) > MAX_LABEL_ITEMS:
        raise LocalBenchmarkPackError(f"{field} must be a bounded string list")
    result = [
        _bounded_string(item, field=field, maximum=MAX_STRING_BYTES) for item in value
    ]
    if len(set(result)) != len(result):
        raise LocalBenchmarkPackError(f"{field} contains duplicate values")
    return result


def _document(value: Any) -> tuple[dict[str, str], int]:
    if not isinstance(value, dict) or set(value) != DOCUMENT_FIELDS:
        raise LocalBenchmarkPackError(
            "execution document fields must match the strict contract"
        )
    document_id = _bounded_string(
        value["document_id"], field="document_id", maximum=MAX_RECORD_ID_BYTES
    )
    if not _RECORD_ID.fullmatch(document_id):
        raise LocalBenchmarkPackError("document_id has invalid syntax")
    filename = _bounded_string(
        value["filename"], field="filename", maximum=MAX_STRING_BYTES
    )
    if Path(filename).name != filename or filename in {".", ".."}:
        raise LocalBenchmarkPackError(
            "filename must be a basename, not an external path"
        )
    mime_type = _bounded_string(value["mime_type"], field="mime_type", maximum=256)
    if not _MIME_TYPE.fullmatch(mime_type):
        raise LocalBenchmarkPackError("mime_type has invalid syntax")
    source_type = _bounded_string(value["source_type"], field="source_type", maximum=32)
    if source_type not in ALLOWED_SOURCE_TYPES:
        raise LocalBenchmarkPackError("source_type is not admitted")
    classification = _bounded_string(
        value["classification"], field="classification", maximum=256
    )
    if classification not in ALLOWED_CLASSIFICATIONS:
        raise LocalBenchmarkPackError("classification is not admitted")
    content = _bounded_string(
        value["content_base64"],
        field="content_base64",
        maximum=((MAX_DOCUMENT_BYTES + 2) // 3) * 4,
    )
    try:
        decoded = base64.b64decode(content, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise LocalBenchmarkPackError("content_base64 is not canonical base64") from exc
    if base64.b64encode(decoded).decode("ascii") != content:
        raise LocalBenchmarkPackError("content_base64 is not canonical base64")
    if len(decoded) > MAX_DOCUMENT_BYTES:
        raise LocalBenchmarkPackError("document exceeds decoded byte-size limit")
    return {
        "document_id": document_id,
        "filename": filename,
        "mime_type": mime_type,
        "source_type": source_type,
        "content_base64": content,
        "classification": classification,
    }, len(decoded)


def _execution_records(payload: bytes) -> list[dict[str, Any]]:
    records = _jsonl(payload, name="execution cases")
    record_ids: set[str] = set()
    query_types: set[str] = set()
    total_document_bytes = 0
    result: list[dict[str, Any]] = []
    for value in records:
        if set(value) != EXECUTION_FIELDS:
            raise LocalBenchmarkPackError(
                "execution case fields must match the strict contract"
            )
        record_id = _record_id(value["id"])
        if record_id in record_ids:
            raise LocalBenchmarkPackError("execution cases contain duplicate id")
        record_ids.add(record_id)
        query_type = _query_type(value["query_type"])
        query_types.add(query_type)
        if len(query_types) > MAX_QUERY_TYPES:
            raise LocalBenchmarkPackError("execution cases exceed query-type limit")
        documents_value = value["documents"]
        if (
            not isinstance(documents_value, list)
            or not documents_value
            or len(documents_value) > MAX_DOCUMENTS_PER_CASE
        ):
            raise LocalBenchmarkPackError("documents must be a nonempty bounded list")
        documents: list[dict[str, str]] = []
        document_ids: set[str] = set()
        filenames: set[str] = set()
        for document_value in documents_value:
            document, decoded_size = _document(document_value)
            if document["document_id"] in document_ids:
                raise LocalBenchmarkPackError("documents contain duplicate document_id")
            if document["filename"] in filenames:
                raise LocalBenchmarkPackError("documents contain duplicate filename")
            document_ids.add(document["document_id"])
            filenames.add(document["filename"])
            total_document_bytes += decoded_size
            if total_document_bytes > MAX_TOTAL_DOCUMENT_BYTES:
                raise LocalBenchmarkPackError(
                    "execution cases exceed total decoded document byte-size limit"
                )
            documents.append(document)
        scope = _bounded_string(value["scope"], field="scope", maximum=MAX_SCOPE_BYTES)
        if scope not in ALLOWED_SCOPES:
            raise LocalBenchmarkPackError("scope is not admitted")
        permission_persona = _bounded_string(
            value["permission_persona"],
            field="permission_persona",
            maximum=MAX_PERSONA_BYTES,
        )
        if not _STABLE_NAME.fullmatch(permission_persona):
            raise LocalBenchmarkPackError("permission_persona has invalid syntax")
        language = _bounded_string(value["language"], field="language", maximum=16)
        if not _LANGUAGE.fullmatch(language):
            raise LocalBenchmarkPackError("language has invalid syntax")
        result.append(
            {
                "id": record_id,
                "query": _bounded_string(
                    value["query"], field="query", maximum=MAX_QUERY_BYTES
                ),
                "intent": _stable_name(value["intent"], field="intent"),
                "workspace_fixture": _stable_name(
                    value["workspace_fixture"], field="workspace_fixture"
                ),
                "project_fixture": _stable_name(
                    value["project_fixture"], field="project_fixture"
                ),
                "scope": scope,
                "permission_persona": permission_persona,
                "query_type": query_type,
                "language": language,
                "documents": documents,
            }
        )
    return result


def _source_constraints(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) > MAX_LABEL_ITEMS:
        raise LocalBenchmarkPackError(
            "expected_source_constraints must be a bounded object list"
        )
    result: list[dict[str, Any]] = []
    seen: set[bytes] = set()
    for constraint in value:
        if (
            not isinstance(constraint, dict)
            or "document_id" not in constraint
            or not set(constraint).issubset(SOURCE_CONSTRAINT_FIELDS)
        ):
            raise LocalBenchmarkPackError(
                "source constraint fields must match the strict contract"
            )
        normalized: dict[str, Any] = {
            "document_id": _bounded_string(
                constraint["document_id"],
                field="source constraint document_id",
                maximum=MAX_RECORD_ID_BYTES,
            )
        }
        if not _RECORD_ID.fullmatch(normalized["document_id"]):
            raise LocalBenchmarkPackError(
                "source constraint document_id has invalid syntax"
            )
        if "active_version" in constraint:
            if not isinstance(constraint["active_version"], bool):
                raise LocalBenchmarkPackError(
                    "source constraint active_version must be a boolean"
                )
            normalized["active_version"] = constraint["active_version"]
        for field in ("locator", "symbol"):
            if field in constraint:
                normalized[field] = _bounded_string(
                    constraint[field], field=field, maximum=MAX_STRING_BYTES
                )
        if "must_contain" in constraint:
            normalized["must_contain"] = _string_list(
                constraint["must_contain"], field="must_contain"
            )
            if not normalized["must_contain"]:
                raise LocalBenchmarkPackError("must_contain must not be empty")
        canonical = _canonical(normalized)
        if canonical in seen:
            raise LocalBenchmarkPackError(
                "expected_source_constraints contains duplicates"
            )
        seen.add(canonical)
        result.append(normalized)
    return result


def _label_records(payload: bytes) -> list[dict[str, Any]]:
    records = _jsonl(payload, name="golden labels")
    record_ids: set[str] = set()
    result: list[dict[str, Any]] = []
    for value in records:
        if set(value) != LABEL_FIELDS:
            raise LocalBenchmarkPackError(
                "golden label fields must match the strict contract"
            )
        record_id = _record_id(value["id"])
        if record_id in record_ids:
            raise LocalBenchmarkPackError("golden labels contain duplicate id")
        record_ids.add(record_id)
        if not isinstance(value["answerable"], bool):
            raise LocalBenchmarkPackError("answerable must be a boolean")
        expected_facts = _string_list(value["expected_facts"], field="expected_facts")
        constraints = _source_constraints(value["expected_source_constraints"])
        if value["answerable"] and (not expected_facts or not constraints):
            raise LocalBenchmarkPackError(
                "answerable labels require expected facts and source constraints"
            )
        if not value["answerable"] and (expected_facts or constraints):
            raise LocalBenchmarkPackError(
                "unanswerable labels must not declare expected facts or sources"
            )
        dataset_version = _bounded_string(
            value["dataset_version"], field="dataset_version", maximum=64
        )
        if not _DATASET_VERSION.fullmatch(dataset_version):
            raise LocalBenchmarkPackError("dataset_version must be semantic version")
        split = _bounded_string(value["split"], field="split", maximum=16)
        if split not in {"train", "holdout"}:
            raise LocalBenchmarkPackError("split must be train or holdout")
        result.append(
            {
                "id": record_id,
                "answerable": value["answerable"],
                "expected_facts": expected_facts,
                "expected_source_constraints": constraints,
                "forbidden_sources": _string_list(
                    value["forbidden_sources"], field="forbidden_sources"
                ),
                "adversarial_tags": _string_list(
                    value["adversarial_tags"], field="adversarial_tags"
                ),
                "notes": _bounded_string(
                    value["notes"], field="notes", maximum=MAX_STRING_BYTES
                ),
                "reviewer": _bounded_string(
                    value["reviewer"], field="reviewer", maximum=MAX_STRING_BYTES
                ),
                "dataset_version": dataset_version,
                "split": split,
            }
        )
    return result


class LocalBenchmarkPack:
    """A one-way execution -> frozen request ledger -> label loader."""

    def __init__(
        self,
        root: Path,
        descriptor: dict[str, Any],
        *,
        expected_records: int | None,
        expected_query_types: frozenset[str] | None,
    ) -> None:
        self.root = root
        self._descriptor = copy.deepcopy(descriptor)
        self.bundle_sha256 = descriptor["sha256"]
        self.execution_sha256: str | None = None
        self.request_ledger_sha256: str | None = None
        self._executions: list[dict[str, Any]] | None = None
        self._request_hashes: tuple[str, ...] | None = None
        self._ledger_frozen = False
        self._expected_records = expected_records
        self._expected_query_types = expected_query_types

    @classmethod
    def open(
        cls,
        root: Path,
        *,
        expected_bundle_sha256: str,
        expected_records: int | None = None,
        expected_query_types: Iterable[str] | None = None,
    ) -> LocalBenchmarkPack:
        """Admit exact raw bytes; golden JSON remains unparsed."""

        if not isinstance(expected_bundle_sha256, str) or not _SHA256.fullmatch(
            expected_bundle_sha256
        ):
            raise LocalBenchmarkPackError(
                "expected bundle hash must be lowercase SHA-256"
            )
        if expected_records is not None and (
            not isinstance(expected_records, int)
            or isinstance(expected_records, bool)
            or not 1 <= expected_records <= MAX_RECORDS
        ):
            raise LocalBenchmarkPackError("expected records must be a bounded integer")
        normalized_query_types: frozenset[str] | None = None
        if expected_query_types is not None:
            if isinstance(expected_query_types, (str, bytes)):
                raise LocalBenchmarkPackError(
                    "expected query types must be a bounded sequence"
                )
            try:
                query_types = list(expected_query_types)
            except TypeError as exc:
                raise LocalBenchmarkPackError(
                    "expected query types must be iterable"
                ) from exc
            if not query_types or len(query_types) > MAX_QUERY_TYPES:
                raise LocalBenchmarkPackError(
                    "expected query types must be nonempty and bounded"
                )
            normalized_query_types = frozenset(
                _query_type(item) for item in query_types
            )
            if len(normalized_query_types) != len(query_types):
                raise LocalBenchmarkPackError(
                    "expected query types must not contain duplicates"
                )
        descriptor = bundle_descriptor(root)
        if descriptor["sha256"] != expected_bundle_sha256:
            raise LocalBenchmarkPackError("benchmark pack bundle hash mismatch")
        if bundle_descriptor(root) != descriptor:
            raise LocalBenchmarkPackError("benchmark pack changed during admission")
        return cls(
            _pack_root(Path(root)),
            descriptor,
            expected_records=expected_records,
            expected_query_types=normalized_query_types,
        )

    def _verify_unchanged(self) -> None:
        if bundle_descriptor(self.root) != self._descriptor:
            raise LocalBenchmarkPackError("benchmark pack changed after admission")

    def execution_projection(self) -> list[dict[str, Any]]:
        """Return execution-only cases, never golden label fields."""

        if self._executions is None:
            self._verify_unchanged()
            payload = _read_regular(self.root / EXECUTION_PATH, maximum=MAX_FILE_BYTES)
            executions = _execution_records(payload)
            self._verify_unchanged()
            self._validate_manifest_values(
                executions,
                records=self._expected_records,
                query_types=self._expected_query_types,
            )
            self.execution_sha256 = _domain_hash(
                EXECUTION_DOMAIN, _canonical(executions)
            )
            self._request_hashes = tuple(
                _domain_hash(REQUEST_DOMAIN, _canonical(case)) for case in executions
            )
            self._executions = executions
        return copy.deepcopy(self._executions)

    def request_hashes(self) -> tuple[str, ...]:
        """Return execution identities, not observed-provider-request evidence."""

        self.execution_projection()
        assert self._request_hashes is not None
        return self._request_hashes

    @staticmethod
    def _validate_manifest_values(
        executions: list[dict[str, Any]],
        *,
        records: int | None,
        query_types: frozenset[str] | None,
    ) -> None:
        if records is not None and len(executions) != records:
            raise LocalBenchmarkPackError(
                "execution record count does not match private pack manifest"
            )
        actual_query_types = frozenset(case["query_type"] for case in executions)
        if query_types is not None and actual_query_types != query_types:
            raise LocalBenchmarkPackError(
                "execution query types do not match private pack manifest"
            )

    def validate_execution_manifest(
        self, *, records: int, query_types: Iterable[str]
    ) -> dict[str, Any]:
        """Bind manifest count/type claims to the execution projection."""

        if (
            not isinstance(records, int)
            or isinstance(records, bool)
            or not 1 <= records <= MAX_RECORDS
        ):
            raise LocalBenchmarkPackError("manifest records must be a bounded integer")
        if isinstance(query_types, (str, bytes)):
            raise LocalBenchmarkPackError("manifest query types must be a sequence")
        try:
            values = list(query_types)
        except TypeError as exc:
            raise LocalBenchmarkPackError(
                "manifest query types must be iterable"
            ) from exc
        if not values or len(values) > MAX_QUERY_TYPES:
            raise LocalBenchmarkPackError(
                "manifest query types must be nonempty and bounded"
            )
        normalized = frozenset(_query_type(item) for item in values)
        if len(normalized) != len(values):
            raise LocalBenchmarkPackError(
                "manifest query types must not contain duplicates"
            )
        executions = self.execution_projection()
        self._validate_manifest_values(
            executions, records=records, query_types=normalized
        )
        return {
            "records": len(executions),
            "query_types": sorted(normalized),
            "execution_sha256": self.execution_sha256,
        }

    def freeze_request_ledger(self, records: Iterable[dict[str, str]]) -> str:
        """Freeze per-case aggregates of actual observed request-event hashes."""

        if self._ledger_frozen:
            raise LocalBenchmarkPackError("request ledger is already frozen")
        executions = self.execution_projection()
        self._verify_unchanged()
        try:
            ledger = list(records)
        except TypeError as exc:
            raise LocalBenchmarkPackError("request ledger must be iterable") from exc
        if len(ledger) > MAX_RECORDS:
            raise LocalBenchmarkPackError("request ledger exceeds record-count limit")
        normalized: list[dict[str, str]] = []
        seen: set[str] = set()
        for value in ledger:
            if not isinstance(value, dict) or set(value) != LEDGER_FIELDS:
                raise LocalBenchmarkPackError(
                    "request ledger fields must be id and request_sha256 only"
                )
            record_id = _record_id(value["id"])
            request_sha256 = value["request_sha256"]
            if not isinstance(request_sha256, str) or not _SHA256.fullmatch(
                request_sha256
            ):
                raise LocalBenchmarkPackError(
                    "request ledger hash must be lowercase SHA-256"
                )
            if record_id in seen:
                raise LocalBenchmarkPackError("request ledger contains duplicate id")
            seen.add(record_id)
            normalized.append({"id": record_id, "request_sha256": request_sha256})
        expected_ids = {case["id"] for case in executions}
        if seen != expected_ids:
            raise LocalBenchmarkPackError(
                "request ledger record IDs must exactly match execution record IDs"
            )
        normalized.sort(key=lambda item: item["id"])
        self.request_ledger_sha256 = _domain_hash(LEDGER_DOMAIN, _canonical(normalized))
        self._ledger_frozen = True
        return self.request_ledger_sha256

    def load_labels(self) -> list[dict[str, Any]]:
        """Parse golden labels only after the caller request ledger is frozen."""

        if not self._ledger_frozen:
            raise LocalBenchmarkPackError(
                "request ledger must be frozen before golden labels are loaded"
            )
        executions = self.execution_projection()
        self._verify_unchanged()
        payload = _read_regular(self.root / LABEL_PATH, maximum=MAX_FILE_BYTES)
        labels = _label_records(payload)
        self._verify_unchanged()
        execution_ids = {case["id"] for case in executions}
        label_ids = {label["id"] for label in labels}
        if label_ids != execution_ids:
            raise LocalBenchmarkPackError(
                "golden label record IDs must exactly match execution record IDs"
            )
        documents_by_case = {
            case["id"]: {document["document_id"] for document in case["documents"]}
            for case in executions
        }
        for label in labels:
            source_ids = {
                constraint["document_id"]
                for constraint in label["expected_source_constraints"]
            }
            forbidden_ids = set(label["forbidden_sources"])
            known_ids = documents_by_case[label["id"]]
            if not source_ids.issubset(known_ids):
                raise LocalBenchmarkPackError(
                    "golden expected_source_constraints must reference execution "
                    "document_id values"
                )
            if not forbidden_ids.issubset(known_ids):
                raise LocalBenchmarkPackError(
                    "golden forbidden_sources must reference execution document_id values"
                )
            if source_ids & forbidden_ids:
                raise LocalBenchmarkPackError(
                    "expected and forbidden source sets must be disjoint"
                )
        return copy.deepcopy(labels)
