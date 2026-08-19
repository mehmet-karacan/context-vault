"""Canonical-path guard for repository/directory scans (Aşama 7.2).

This is the documented security gate the API/application layer MUST call before
scanning a path taken from a web request (AKTIF_GOREV.md §7.2):

- "Web isteğinden gelen herhangi bir mutlak path'i doğrudan tarama." — never
  scan an arbitrary absolute path straight from the client.
- "Yalnız ``CODE_ALLOWED_ROOTS`` altında kalan canonical path'lere izin ver." —
  the requested path must canonicalize to a location under one of the allowed
  roots.
- "Symlink ile izinli kökün dışına çıkışı engelle." — the canonicalization
  resolves symlinks, so a symlink pointing outside the root is rejected.
"""

from __future__ import annotations

import os
from typing import Iterable, List, Union


def canonical(path: str) -> str:
    """Return the fully-resolved, absolute, symlink-expanded path."""
    expanded = os.path.expanduser(os.path.abspath(path))
    return os.path.realpath(expanded)


def _norm_root(root: str) -> str:
    return canonical(root).rstrip(os.sep) or os.sep


def canonical_under_root(candidate_path: str, root: str) -> bool:
    """True if the canonical ``candidate_path`` equals ``root`` or lies under it.

    Canonicalization expands symlinks on both sides, so a symlink inside the
    tree that resolves above the root yields a candidate that no longer starts
    with the canonical root and is therefore rejected (AKTIF §7.2 symlink
    escape guard). Traversal segments (``..``) are also resolved away.
    """
    cand = canonical(candidate_path)
    root_norm = _norm_root(root)
    if cand == root_norm:
        return True
    return cand.startswith(root_norm + os.sep)


def _split_roots(allowed_roots: Union[str, Iterable[str]]) -> List[str]:
    if isinstance(allowed_roots, str):
        return [r.strip() for r in allowed_roots.split(",") if r.strip()]
    return [str(r) for r in allowed_roots if r]


def is_allowed_scan_path(
    requested_path: str,
    allowed_roots: Union[str, Iterable[str]],
) -> bool:
    """True only if ``requested_path`` canonicalizes under an allowed root.

    Rules:
    - A relative path is refused (clients may only supply an alias + relative
      path, never an absolute path — AKTIF §12.3).
    - The requested path must be absolute and resolve, after symlink expansion,
      to a directory-tree root under one of the ``allowed_roots``.
    - Traversal / symlink escapes that land outside every root are refused.
    """
    if not requested_path:
        return False
    if not os.path.isabs(requested_path):
        return False

    allowed = _split_roots(allowed_roots)
    if not allowed:
        return False

    for root in allowed:
        if canonical_under_root(requested_path, root):
            return True
    return False
