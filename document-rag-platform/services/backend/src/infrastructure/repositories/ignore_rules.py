"""Ignore-rule engine for repository/directory scans (Aşama 7.3).

Implements the documented precedence (AKTIF_GOREV.md §7.3):

1. System security ignore list (fixed, never overridable).
2. ``.contextvaultignore``.
3. Repository ``.gitignore`` rules.
4. User-provided include/exclude patterns.

Contains a small, self-contained gitignore-style glob matcher that reads and
honors ``#`` comments, ``!`` negation, extension and directory patterns — it
deliberately does **not** shell out to git.

Also provides ``is_sensitive_path`` for secret/credential detection so the
discovery layer can skip secret material by default (AKTIF §7.2 / §7.3).
"""

from __future__ import annotations

import os
import re
from typing import Iterable, List, Optional, Sequence

# --- Default system ignore list (AKTIF_GOREV.md §7.3) -----------------------
DEFAULT_IGNORE_PATTERNS: tuple[str, ...] = (
    ".git/",
    "node_modules/",
    ".venv/",
    "venv/",
    "dist/",
    "build/",
    "target/",
    "coverage/",
    ".next/",
    ".cache/",
    "vendor/",
    "*.min.js",
    "*.map",
    "*.lock",
    "*.png",
    "*.jpg",
    "*.pdf",   # repo-code scans route PDFs to the document parser instead
    "*.exe",
    "*.dll",
    "*.so",
    "*.class",
    "*.jar",
    "*.zip",
    "*.tar",
    "*.gz",
    ".env",
    ".env.*",
    "*.pem",
    "*.key",
    "*.p12",
    "*.jks",
    "id_rsa*",
)

# Patterns treated as secret/sensitive (AKTIF §7.2 "hassas dosyaları
# varsayılan olarak skip et"). Used by ``is_sensitive_path``.
DEFAULT_SENSITIVE_PATTERNS: tuple[str, ...] = (
    ".env",
    ".env.*",
    "*.pem",
    "*.key",
    "*.p12",
    "*.pks",
    "*.pfx",
    "*.jks",
    "*.pkcs8",
    "*.ppk",
    "id_rsa*",
    "id_dsa*",
    "id_ecdsa*",
    "id_ed25519*",
    "*.keystore",
    "*.jceks",
    "*credential*",
    "*credentials*",
    "*secret*",
    "*secrets*",
    "*password*",
    ".npmrc",   # often carries auth tokens
    ".pypirc",
    "*.pem.my.cnf",
)


def _normalize_rule(raw: str) -> Optional[dict]:
    """Parse a single gitignore-style line into a rule dict, or ``None``."""
    line = raw.strip()
    if not line:
        return None
    if line.startswith("#"):
        return None
    if line.startswith("\\#") or line.startswith("\\!"):
        line = line[1:]

    negate = False
    if line.startswith("!"):
        negate = True
        line = line[1:].strip()

    if not line:
        return None

    dir_only = False
    if line.endswith("/"):
        dir_only = True
        line = line[:-1]

    if not line:
        return None

    anchored = line.startswith("/")
    if anchored:
        line = line[1:]
    elif "/" in line:
        walks_all = line.startswith("**/")
        # A pattern with an interior slash is anchored to the owning directory
        # unless it starts with a globstar (`**/`) which matches at any level.
        anchored = not walks_all
        if walks_all:
            line = line[3:] or "**"

    regex = _compile_glob(line)
    return {
        "negate": negate,
        "dir_only": dir_only,
        "anchored": anchored,
        "regex": regex,
    }


def _compile_glob(pattern: str) -> re.Pattern:
    """Translate a gitignore glob into an anchored regex over a single path."""
    out: List[str] = ["^"]
    i, n = 0, len(pattern)
    while i < n:
        c = pattern[i]
        if c == "*":
            if i + 1 < n and pattern[i + 1] == "*":
                out.append(".*")
                i += 2
                if i < n and pattern[i] == "/":
                    i += 1
            else:
                out.append("[^/]*")
                i += 1
        elif c == "?":
            out.append("[^/]")
            i += 1
        elif c == "[":
            j = i + 1
            while j < n and pattern[j] != "]":
                j += 1
            if j < n:
                cls = pattern[i:j + 1]
                if cls.startswith("[!"):
                    cls = "[^" + cls[2:]
                out.append(cls)
                i = j + 1
            else:
                out.append(re.escape(c))
                i += 1
        else:
            out.append(re.escape(c))
            i += 1
    out.append("$")
    return re.compile("".join(out))


def _posix(path: str) -> str:
    return path.replace("\\", "/")


class GitignoreMatcher:
    """A single, ordered set of gitignore rules parsed from one level.

    Rules may carry an optional ``base`` (POSIX, relative to the scan root) so
    nested ``.gitignore``/``.contextvaultignore`` files apply only underneath
    their own directory.
    """

    def __init__(
        self,
        patterns: Sequence[str],
        base: str = "",
        allow_negation: bool = True,
    ):
        self.base = _posix(base).strip("/")
        self.rules: List[dict] = []
        for raw in patterns:
            rule = _normalize_rule(raw)
            if rule is None:
                continue
            if not allow_negation and rule["negate"]:
                rule["negate"] = False
            self.rules.append(rule)

    @classmethod
    def from_file(cls, filename: str, base: str = "", allow_negation: bool = True):
        patterns: List[str] = []
        if filename and os.path.isfile(filename):
            try:
                with open(filename, "r", encoding="utf-8", errors="replace") as fh:
                    patterns = fh.read().splitlines()
            except OSError:
                patterns = []
        return cls(patterns, base=base, allow_negation=allow_negation)

    def _strip_base(self, rel: str) -> str:
        if not self.base:
            return rel
        if rel == self.base:
            return ""
        prefix = self.base + "/"
        if rel.startswith(prefix):
            return rel[len(prefix):]
        # Rule not in scope for this path.
        return None

    def _tail_match(self, tail: str, rule: dict, is_dir: bool) -> bool:
        if rule["dir_only"] and not is_dir:
            return False
        if rule["anchored"]:
            return rule["regex"].match(tail) is not None
        # Non-anchored pattern matches any basename below the base.
        basename = tail.split("/")[-1]
        return rule["regex"].match(basename) is not None

    def ignored(self, relative_path: str, is_dir: bool = False) -> Optional[bool]:
        """Return True/False if a rule decides this path, else ``None``.

        Late rules win: within a single matcher an earlier ignore can be
        overridden by a later ``!`` negation (and vice versa).
        """
        rel = _posix(relative_path).strip("/")
        if not rel:
            return None
        tail = self._strip_base(rel)
        if tail is None:
            return None
        if tail == "":
            return None
        result: Optional[bool] = None
        for rule in self.rules:
            if self._tail_match(tail, rule, is_dir):
                result = not rule["negate"]
        return result


class IgnoreRules:
    """Combines system, ``.contextvaultignore``, ``.gitignore`` and user rules.

    Precedence order is fixed: system > contextvault > gitignore > user. The
    first layer that has any matching rule decides the outcome; lower layers
    are never consulted for that path. Within a single layer, the last matching
    rule (including negation) wins.
    """

    def __init__(
        self,
        system_patterns: Sequence[str] = DEFAULT_IGNORE_PATTERNS,
        contextvault_patterns: Sequence[str] = (),
        gitignore_patterns: Sequence[str] = (),
        include_patterns: Sequence[str] = (),
        exclude_patterns: Sequence[str] = (),
        contextvault_file: Optional[str] = None,
        gitignore_file: Optional[str] = None,
    ):
        self.system = GitignoreMatcher(system_patterns, allow_negation=False)
        if contextvault_file:
            self.contextvault = GitignoreMatcher.from_file(
                contextvault_file, allow_negation=True
            )
        else:
            self.contextvault = GitignoreMatcher(contextvault_patterns)
        if gitignore_file:
            self.gitignore = GitignoreMatcher.from_file(gitignore_file)
        else:
            self.gitignore = GitignoreMatcher(gitignore_patterns)
        # User include patterns re-include (-negation); excludes ignore.
        user_rules: List[str] = []
        user_rules.extend("!" + p for p in include_patterns)
        user_rules.extend(exclude_patterns)
        self.user = GitignoreMatcher(user_rules)

        self.layers = [
            self.system,
            self.contextvault,
            self.gitignore,
            self.user,
        ]

    def is_ignored(self, relative_path: str, is_dir: bool = False) -> bool:
        rel = _posix(relative_path).strip("/")
        if not rel:
            return False
        for layer in self.layers:
            decision = layer.ignored(rel, is_dir)
            if decision is not None:
                return decision
        return False


def build_ignore_rules(
    system_ignore: Optional[Sequence[str]] = None,
    contextvault_ignore: Optional[Sequence[str]] = None,
    contextvault_file: Optional[str] = None,
    gitignore: Optional[Sequence[str]] = None,
    gitignore_file: Optional[str] = None,
    include_patterns: Optional[Sequence[str]] = None,
    exclude_patterns: Optional[Sequence[str]] = None,
    root_path: Optional[str] = None,
) -> IgnoreRules:
    """Build ``IgnoreRules`` with the documented precedence.

    ``root_path`` (if given) is used to auto-discover ``.contextvaultignore``
    and ``.gitignore`` files at the scan root when they are not passed
    explicitly.
    """
    if root_path:
        if contextvault_file is None:
            contextvault_file = os.path.join(root_path, ".contextvaultignore")
        if gitignore_file is None:
            gitignore_file = os.path.join(root_path, ".gitignore")

    return IgnoreRules(
        system_patterns=(
            list(system_ignore) if system_ignore is not None else DEFAULT_IGNORE_PATTERNS
        ),
        contextvault_patterns=contextvault_ignore or (),
        gitignore_patterns=gitignore or (),
        include_patterns=include_patterns or (),
        exclude_patterns=exclude_patterns or (),
        contextvault_file=contextvault_file,
        gitignore_file=gitignore_file,
    )


# --- Sensitive-path detection -----------------------------------------------
class SensitivePathMatcher:
    def __init__(self, patterns: Sequence[str] = DEFAULT_SENSITIVE_PATTERNS):
        # Sensitive patterns are not anchored: match against any basename so a
        # nested `.env` or `config/id_rsa` anywhere in the tree is caught.
        self._matcher = GitignoreMatcher(list(patterns), allow_negation=False)

    def is_sensitive(self, relative_path: str) -> bool:
        rel = _posix(relative_path).strip("/")
        if not rel:
            return False
        decision = self._matcher.ignored(rel)
        return bool(decision)


_default_sensitive = SensitivePathMatcher()


def is_sensitive_path(relative_path: str) -> bool:
    """Return True if ``relative_path`` looks like a secret/credential file."""
    return _default_sensitive.is_sensitive(relative_path)


def is_generated_file(relative_path: str) -> bool:
    """Heuristic: is this file a lockfile / generated artifact one would skip
    re-ingesting as source (AKTIF §7.4 ``is_generated``)?"""
    rel = _posix(relative_path)
    base = rel.split("/")[-1].lower()
    if base.endswith(".lock") or base.endswith(".lockb"):
        return True
    named_locks = {
        "package-lock.json",
        "npm-shrinkwrap.json",
        "yarn.lock",
        "pnpm-lock.yaml",
        "poetry.lock",
        "requirements.txt.lock",
        "Pipfile.lock",
        "composer.lock",
        "Gemfile.lock",
        "go.sum",
        "Cargo.lock",
        "Pip.lock",
        "flake.lock",
    }
    if base in named_locks:
        return True
    if base.endswith(".min.js") or base.endswith(".min.css"):
        return True
    generated_dirs = {
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        "target",
        "dist",
        "build",
    }
    parts = rel.split("/")
    return bool(set(parts) & generated_dirs)
