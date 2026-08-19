"""Directory ``SourceScanner`` adapter (Aşama 7.1 / §12.3).

``DirectorySourceScanner`` implements ``domain.ports.SourceScanner`` for local
directory scans. It enforces the security contract before any discovery runs:

- Only an allowed-root alias + a **relative** path from the client is accepted;
  absolute client paths are refused (AKTIF §12.3: "Mutlak path istemciden kabul
  edilmez").
- The resolved absolute target must canonicalize under ``CODE_ALLOWED_ROOTS``
  (AKTIF §7.2) via ``is_allowed_scan_path``.

Git and archive scanners belong to the concurrent worker; a no-op placeholder is
provided so this port still resolves.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Sequence

from ...config import Settings, settings as default_settings
from .discovery import DiscoveredFile, ScanConfig, discover_directory
from .ignore_rules import build_ignore_rules
from .path_security import canonical, is_allowed_scan_path


def parse_allowed_roots(allowed_roots: Optional[Sequence[str]] = None) -> List[str]:
    """Resolve ``CODE_ALLOWED_ROOTS`` (comma string or sequence) to a list."""
    if allowed_roots is not None:
        if isinstance(allowed_roots, str):
            return [r.strip() for r in allowed_roots.split(",") if r.strip()]
        return [str(r) for r in allowed_roots if r]
    raw = getattr(default_settings, "CODE_ALLOWED_ROOTS", "/imports,/workspace")
    return [r.strip() for r in str(raw).split(",") if r.strip()]


class DirectorySourceScanner:
    """Scans a validated local directory under an allowed root.

    Conforms to ``domain.ports.SourceScanner``: ``scan(source_ref, **options)``
    returns a list of discovered-file descriptors.
    """

    def __init__(
        self,
        settings: Optional[Settings] = None,
        allowed_roots: Optional[Sequence[str]] = None,
        config: Optional[ScanConfig] = None,
        root_map: Optional[Dict[str, str]] = None,
    ):
        self.settings = settings or default_settings
        self.allowed_roots = parse_allowed_roots(allowed_roots)
        self.config = config or ScanConfig.from_settings(self.settings)
        self.root_map = dict(root_map or {})

    def _resolve_root(self, alias: str) -> Optional[str]:
        if alias in self.root_map:
            return self.root_map[alias]
        if os.path.isabs(alias):
            cand = canonical(alias)
            for root in self.allowed_roots:
                if canonical(root) == cand:
                    return root
            return None
        for root in self.allowed_roots:
            if os.path.basename(root.rstrip(os.sep)) == alias:
                return root
        return None

    def scan(self, source_ref: str, **options: Any) -> List[DiscoveredFile]:
        """Discover files for a directory identified by an allowed-root alias.

        ``source_ref`` is the allowed-root alias for the scan root; ``options``
        may carry:
          - ``relative_path``: relative path under that root (default "").
          - ``include_patterns`` / ``exclude_patterns``: user pattern overrides.
          - ``config``: an explicit ``ScanConfig`` override.
        """
        root_alias = options.get("root_alias") or source_ref
        relative_path = str(options.get("relative_path") or "")
        include = list(options.get("include_patterns") or [])
        exclude = list(options.get("exclude_patterns") or [])
        config = options.get("config") or self.config

        if os.path.isabs(relative_path):
            raise ValueError(
                "absolute paths are not accepted from the client (AKTIF 12.3)"
            )

        root_abs = self._resolve_root(root_alias)
        if root_abs is None:
            raise ValueError(
                f"unknown allowed root alias: {root_alias!r}; "
                f"allowed roots: {self.allowed_roots}"
            )

        scan_target = os.path.join(root_abs, relative_path)
        if not is_allowed_scan_path(scan_target, self.allowed_roots):
            raise PermissionError(
                f"scan path resolves outside the allowed roots: {scan_target}"
            )
        if not os.path.isdir(scan_target):
            raise FileNotFoundError(scan_target)

        ignore = build_ignore_rules(
            root_path=scan_target,
            include_patterns=include or None,
            exclude_patterns=exclude or None,
        )
        result = discover_directory(scan_target, ignore=ignore, config=config)
        return result.files


class UnsupportedSourceScanner:
    """Placeholder for Git/archive scanners (handled by the concurrent worker).

    Raises ``NotImplementedError`` so callers get an explicit signal that a
    repository URL / archive upload must be routed to the dedicated scanners.
    """

    def scan(self, source_ref: str, **options: Any) -> List[Any]:
        raise NotImplementedError(
            "Git/archive source scanners are implemented by the concurrent "
            "repository scanner worker (AKTIF_GOREV.md 7.1)."
        )
