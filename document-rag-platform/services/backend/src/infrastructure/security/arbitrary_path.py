"""Arbitrary absolute local path block (Aşama 9.5 / §7.2).

Re-exports the Aşama 7 canonical guard (``src/infrastructure/repositories/
path_security``) and adds a thin raising convenience so the ingestion/API layer
can *fail closed* before touching any path supplied by a web request.

AKTIF §7.2 rules enforced (unchanged, not re-implemented):
- Never scan an arbitrary absolute path straight from the client.
- Only paths canonicalizing under an ``allowed_roots`` entry are permitted.
- Symlink/``..`` escapes that land outside every root are refused.
"""

from __future__ import annotations

from typing import Any, Iterable, Union

from ..repositories.path_security import (
    canonical,
    canonical_under_root,
    is_allowed_scan_path,
)

__all__ = [
    "PathBlockError",
    "canonical",
    "canonical_under_root",
    "is_allowed_scan_path",
    "is_path_blocked",
    "ensure_allowed_scan_path",
]


class PathBlockError(ValueError):
    """Raised when a requested local path is not under an allowed root."""


def is_path_blocked(
    requested_path: str,
    allowed_roots: Union[str, Iterable[str]],
) -> bool:
    """True if ``requested_path`` must be blocked (reuses path_security)."""
    return not is_allowed_scan_path(requested_path, allowed_roots)


def ensure_allowed_scan_path(
    requested_path: str,
    allowed_roots: Union[str, Iterable[str]],
    *,
    config: Any = None,
) -> str:
    """Return the canonical ``requested_path``, or raise ``PathBlockError``.

    ``allowed_roots`` (from ``config.CODE_ALLOWED_ROOTS`` when not given) must
    contain the canonicalized path. Failing closed here guarantees a web-supplied
    absolute path is never scanned unless it resolves under an allowed root.
    """
    if config is not None:
        roots = getattr(config, "CODE_ALLOWED_ROOTS", None)
        if roots and allowed_roots in (None, ""):
            allowed_roots = roots
        elif roots and allowed_roots:
            # Merge config roots with any explicitly-supplied ones.
            merged = list((allowed_roots if not isinstance(allowed_roots, str)
                           else [r for r in allowed_roots.split(",") if r]))
            merged.extend(r for r in str(roots).split(",") if r.strip())
            allowed_roots = merged
    if is_path_blocked(requested_path, allowed_roots):
        raise PathBlockError(
            f"path {requested_path!r} does not resolve under an allowed root"
        )
    return canonical(requested_path)
