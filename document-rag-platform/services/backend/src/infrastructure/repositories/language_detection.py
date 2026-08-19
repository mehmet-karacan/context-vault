"""Deterministic, DB-free language and binary detection (Aşama 7.4).

Provides:
- ``detect_language`` — map a file's extension (or explicit filename) to a
  programming language slug, e.g. ``py`` -> ``python``, ``ts`` -> ``typescript``.
- ``is_binary`` — heuristic content sniffing via magic header signatures and a
  NUL/control-byte scan. Deterministic and fully testable — no external
  libraries, no filesystem, no database.
"""

from __future__ import annotations

from typing import Optional

# Extension -> language slug. Order matters only for reading the dict; every
# key is unique. Lowercased, dot stripped before lookup.
EXTENSION_TO_LANGUAGE: dict[str, str] = {
    "py": "python",
    "pyi": "python",
    "pyw": "python",
    "java": "java",
    "kt": "kotlin",
    "kts": "kotlin",
    "scala": "scala",
    "sc": "scala",
    "go": "go",
    "rs": "rust",
    "c": "c",
    "h": "c",
    "cc": "cpp",
    "cpp": "cpp",
    "cxx": "cpp",
    "c++": "cpp",
    "hh": "cpp",
    "hpp": "cpp",
    "hxx": "cpp",
    "cs": "csharp",
    "js": "javascript",
    "mjs": "javascript",
    "cjs": "javascript",
    "jsx": "javascript",
    "ts": "typescript",
    "mts": "typescript",
    "cts": "typescript",
    "tsx": "typescript",
    "rb": "ruby",
    "rake": "ruby",
    "php": "php",
    "swift": "swift",
    "m": "objective-c",
    "mm": "objective-c",
    "sh": "shell",
    "bash": "shell",
    "zsh": "shell",
    "fish": "shell",
    "ps1": "powershell",
    "bat": "batch",
    "cmd": "batch",
    "sql": "sql",
    "plsql": "plsql",
    "pkb": "plsql",
    "pks": "plsql",
    "prc": "plsql",
    "fnc": "plsql",
    "trg": "plsql",
    "vw": "plsql",
    "json": "json",
    "yaml": "yaml",
    "yml": "yaml",
    "toml": "toml",
    "xml": "xml",
    "html": "html",
    "htm": "html",
    "css": "css",
    "scss": "scss",
    "sass": "scss",
    "less": "less",
    "md": "markdown",
    "markdown": "markdown",
    "mdx": "markdown",
    "rst": "markdown",
    "txt": "plaintext",
    "text": "plaintext",
    "ipynb": "jupyter",
    "dockerfile": "dockerfile",
    "makefile": "makefile",
    "tf": "terraform",
    "hcl": "hcl",
    "proto": "protobuf",
    "graphql": "graphql",
    "gql": "graphql",
    "r": "r",
    "lua": "lua",
    "ex": "elixir",
    "exs": "elixir",
    "erl": "erlang",
    "hrl": "erlang",
    "hs": "haskell",
    "clj": "clojure",
    "cljs": "clojure",
    "dart": "dart",
    "vue": "vue",
    "svelte": "svelte",
}

# Exact-filename (no extension) -> language slug, for special-case files such as
# Dockerfile / Makefile / .gitignore style files without an extension.
FILENAME_TO_LANGUAGE: dict[str, str] = {
    "dockerfile": "dockerfile",
    "makefile": "makefile",
    "cmakelists.txt": "cmake",
    "gemfile": "ruby",
    "rakefile": "ruby",
    "vagrantfile": "ruby",
    "procfile": "shell",
    "jenkinsfile": "groovy",
}

# Known binary magic signatures: (offset, bytes). Deterministic sniffing used by
# ``is_binary`` for files that do not happen to contain a NUL byte in the probe
# window (e.g. PNG, JDK class files, gzip, ZIP, PDF, ELF, PE).
_BINARY_MAGICS: tuple[tuple[bytes, ...], ...] = (
    (b"\x89PNG\r\n\x1a\n",),
    (b"\xff\xd8\xff",),  # JPEG
    (b"GIF87a", b"GIF89a"),
    (b"BM",),  # Windows BMP
    (b"\xca\xfe\xba\xbe", b"\xca\xfe\xba\xbb"),  # Java class / Mach-O fat
    (b"\xfe\xed\xfa\xce", b"\xce\xfa\xed\xfe", b"\xfe\xed\xfa\xcf", b"\xcf\xfa\xed\xfe"),  # Mach-O
    (b"\x7fELF",),  # ELF
    (b"\x4d\x5a",),  # PE (MZ)
    (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"),  # ZIP / JAR / ODT etc.
    (b"\x1f\x8b",),  # gzip / tar.gz
    (b"\x1f\x9d", b"\x1f\xa0"),  # compress
    (b"\x42\x5a\x68",),  # bzip2
    (b"\xfd7zXZ\x00",),  # xz
    (b"\x28\xb5\x2f\xfd",),  # zstd
    (b"%PDF-",),  # PDF (routed to doc parser by caller)
    (b"\x00\x00\x01\x00",),  # Windows shortcut / some binary formats
    (b"\x00asm",),  # WASM
)

# Extension-based binary shortcut: these extensions almost always denote
# compiled/binary artifacts and are checked before content sniffing.
_BINARY_EXTENSIONS: frozenset[str] = frozenset({
    "png", "jpg", "jpeg", "gif", "bmp", "webp", "ico", "tif", "tiff",
    "pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx",
    "zip", "jar", "gz", "tar", "tgz", "bz2", "xz", "7z", "rar", "zst",
    "exe", "dll", "so", "dylib", "class", "obj", "o", "a", "lib",
    "wasm", "pyc", "pyo", "pyd", "woff", "woff2", "ttf", "otf", "eot",
    "db", "sqlite", "sqlite3", "mdb", "bin", "iso", "img",
})


def _extract_extension(relative_path: str) -> str:
    filename = relative_path.split("/")[-1].split("\\")[-1]
    if "." not in filename:
        return ""
    return filename.rsplit(".", 1)[-1].lower()


def detect_language(relative_path: str, filename: Optional[str] = None) -> Optional[str]:
    """Return a language slug for the given path/filename, or ``None``.

    ``relative_path`` is used to derive the extension (and the basename for
    special-case files). When ``filename`` is provided it takes precedence over
    the basename of ``relative_path`` for exact-filename matching, but the
    extension is still taken from ``filename`` if given.
    """
    name = filename if filename else relative_path
    base = name.split("/")[-1].split("\\")[-1]

    # Exact-filename match first (Dockerfile, Makefile, ...).
    lower_base = base.lower()
    if lower_base in FILENAME_TO_LANGUAGE:
        return FILENAME_TO_LANGUAGE[lower_base]

    ext = _extract_extension(name if filename else relative_path)
    if not ext:
        return None
    return EXTENSION_TO_LANGUAGE.get(ext)


def _has_binary_magic(probe: bytes) -> bool:
    for signatures in _BINARY_MAGICS:
        for magic in signatures:
            if probe.startswith(magic):
                return True
    return False


def _has_nul_byte(probe: bytes) -> bool:
    return b"\x00" in probe


def is_binary(
    relative_path: str,
    first_bytes: bytes,
    chunk: bytes = b"",
) -> bool:
    """Heuristically decide whether a file is binary.

    Order of checks:
    1. Extension shortcut — known compiled/media extensions are binary.
    2. Magic signature header match.
    3. NUL byte present in the probe window (``first_bytes`` + ``chunk``).

    ``chunk`` is an optional additional slice of bytes (e.g. a second read)
    used to widen the probe window when the first chunk was short.
    """
    if not first_bytes:
        return False

    ext = _extract_extension(relative_path)
    if ext in _BINARY_EXTENSIONS:
        return True

    probe = first_bytes[:8192] + chunk[:8192]
    if _has_binary_magic(probe):
        return True
    if _has_nul_byte(probe):
        return True
    return False
