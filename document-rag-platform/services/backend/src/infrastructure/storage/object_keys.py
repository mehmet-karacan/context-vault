"""Object key layout for MinIO/S3 storage (Aşama 2.2).

Centralizes the immutable object key standard defined in AKTIF_GOREV.md
("Object key standardı") so every caller (upload endpoint, ingestion
workers, artifact readers) builds keys the same way:

    projects/{project_id}/documents/{document_id}/versions/{version_id}/original/{safe_filename}
    projects/{project_id}/documents/{document_id}/versions/{version_id}/normalized/document.json
    projects/{project_id}/documents/{document_id}/versions/{version_id}/normalized/document.md
    projects/{project_id}/documents/{document_id}/versions/{version_id}/artifacts/...

Nothing here talks to MinIO directly — this module only builds strings.
"""

from __future__ import annotations

import re
import unicodedata

# Characters allowed in a sanitized filename segment. Everything else is
# replaced with "_". Deliberately conservative (ASCII alnum + a handful of
# punctuation) rather than trying to enumerate every unsafe character.
_SAFE_CHARS_RE = re.compile(r"[^A-Za-z0-9._-]+")
_DEFAULT_FILENAME = "file"
_MAX_FILENAME_LENGTH = 200


def safe_filename(filename: str) -> str:
    """Sanitizes a user-supplied filename for use as an object key segment.

    Guards against:
    - Path traversal (``../``, absolute paths, embedded separators).
    - Null bytes / control characters.
    - Empty or dot-only names (``.``, ``..``, ``""``).
    - Overly long names.

    Does NOT try to preserve the original filename byte-for-byte — it only
    guarantees the result is a single safe path segment. The original
    filename should still be kept as metadata (e.g. in ``document_artifacts``)
    for display purposes.
    """
    if not filename:
        return _DEFAULT_FILENAME

    # Normalize unicode, then drop any path components an attacker (or a
    # browser sending a full path in `filename`) might have smuggled in.
    normalized = unicodedata.normalize("NFKC", filename)
    normalized = normalized.replace("\\", "/")
    candidate = normalized.split("/")[-1]

    # Strip control characters (including NUL) outright.
    candidate = "".join(ch for ch in candidate if unicodedata.category(ch)[0] != "C")

    candidate = _SAFE_CHARS_RE.sub("_", candidate).strip("._")

    if candidate in ("", ".", ".."):
        candidate = _DEFAULT_FILENAME

    if len(candidate) > _MAX_FILENAME_LENGTH:
        # Preserve the extension when truncating so downstream MIME/ext
        # sniffing still works.
        dot = candidate.rfind(".")
        if 0 < dot < len(candidate) - 1 and len(candidate) - dot <= 20:
            ext = candidate[dot:]
            candidate = candidate[: _MAX_FILENAME_LENGTH - len(ext)] + ext
        else:
            candidate = candidate[:_MAX_FILENAME_LENGTH]

    return candidate


def _segment(value: str, label: str) -> str:
    """Validates a path segment (project/document/version id) is safe.

    IDs are expected to be UUIDs or similarly simple identifiers generated
    by our own code, never taken verbatim from request path traversal — but
    we still refuse anything containing a path separator or empty string so
    a caller bug can't silently produce a key that escapes its prefix.
    """
    if not value or "/" in value or "\\" in value or value in (".", ".."):
        raise ValueError(f"invalid {label}: {value!r}")
    return value


def original_key(project_id: str, document_id: str, version_id: str, filename: str) -> str:
    """Key for the immutable original uploaded file."""
    p = _segment(project_id, "project_id")
    d = _segment(document_id, "document_id")
    v = _segment(version_id, "version_id")
    return f"projects/{p}/documents/{d}/versions/{v}/original/{safe_filename(filename)}"


def normalized_json_key(project_id: str, document_id: str, version_id: str) -> str:
    """Key for the normalized content model, stored as JSON."""
    p = _segment(project_id, "project_id")
    d = _segment(document_id, "document_id")
    v = _segment(version_id, "version_id")
    return f"projects/{p}/documents/{d}/versions/{v}/normalized/document.json"


def normalized_markdown_key(project_id: str, document_id: str, version_id: str) -> str:
    """Key for the human-readable Markdown representation."""
    p = _segment(project_id, "project_id")
    d = _segment(document_id, "document_id")
    v = _segment(version_id, "version_id")
    return f"projects/{p}/documents/{d}/versions/{v}/normalized/document.md"


def artifact_key(
    project_id: str, document_id: str, version_id: str, artifact_relative_path: str
) -> str:
    """Key for a miscellaneous artifact (page image, thumbnail, ocr json, ...).

    ``artifact_relative_path`` may itself contain sub-segments (e.g.
    ``pages/0007/thumbnail.png``); each segment is sanitized independently
    so no segment can traverse above ``artifacts/``.
    """
    p = _segment(project_id, "project_id")
    d = _segment(document_id, "document_id")
    v = _segment(version_id, "version_id")
    if not artifact_relative_path:
        raise ValueError("artifact_relative_path must not be empty")

    parts = [seg for seg in artifact_relative_path.replace("\\", "/").split("/") if seg]
    if not parts:
        raise ValueError("artifact_relative_path must not be empty")

    sanitized_parts = [safe_filename(seg) for seg in parts]
    return f"projects/{p}/documents/{d}/versions/{v}/artifacts/" + "/".join(sanitized_parts)
