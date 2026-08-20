"""Secret / credential skip & redaction policy (Aşama 9.5 / §7.2).

- ``contains_secret(text)`` — True if the text carries a credential pattern.
- ``redact_secrets(text)`` — replaces secret *values* with ``[REDACTED]`` so
  secrets never reach the embedding gateway (AKTIF §15: ".env, private key veya
  credential içeriğini embedding gateway'e gönderme").
- ``redact_log_field(value)`` — a safe, size-capped, secret-aware renderer for
  structured logs.
- ``is_sensitive_filename(path)`` — reuses the Aşama 7
  ``ignore_rules.is_sensitive_path`` matcher unchanged (do NOT rewrite).

Patterns are intentionally anchored to a secret *value* (assigned key=value,
``sk-...`` tokens, PEM blocks, etc.) so a bare word like "password" in prose or
benign Turkish text is never flagged.
"""

from __future__ import annotations

import re
from typing import Any, Optional, Tuple

from ...config import settings
from ..repositories.ignore_rules import is_sensitive_path as _is_sensitive_path

REDACTED = "[REDACTED]"


# A private-key / certificate PEM block spanning many lines.
_PEM_RE = re.compile(
    r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP |ENCRYPTED )?PRIVATE KEY-----"
    r".*?-----END (?:RSA |EC |DSA |OPENSSH |PGP |ENCRYPTED )?PRIVATE KEY-----",
    re.DOTALL,
)

# Well-known token shapes.
_TOKEN_RE = re.compile(
    r"(?i)\b("
    r"sk-[A-Za-z0-9_\-]{8,}|"       # OpenAI-style
    r"ghp_[A-Za-z0-9]{20,}|"        # GitHub PAT
    r"AKIA[0-9A-Z]{16}|"            # AWS access key id
    r"xox[baprs]-[A-Za-z0-9\-]{10,}|"  # Slack
    r"eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}"  # JWT-ish
    r")\b"
)

# A secret-ish named assignment:  key = "value"  -> redact the value.
_KEY_VALUE_RE = re.compile(
    r"(?i)(\b(?:api[_-]?key|apikey|secret|secret[_-]?key|password|passwd|pwd"
    r"|token|access[_-]?token|access[_-]?key|auth|credential|private[_-]?key"
    r"|client[_-]?secret|db_password|connection[_-]?string)\b"
    r"\s*[:=]\s*)"
    r"([\"']?[^\s\"',;]+[\"']?)"
)

# --- Default redaction patterns ---------------------------------------------

_BUILTIN_REDACTORS: Tuple[Tuple[str, re.Pattern], ...] = (
    ("pem", _PEM_RE),
    ("token", _TOKEN_RE),
    ("key_value", _KEY_VALUE_RE),
)

# Patterns used by ``contains_secret``: a subset that indicates a *present*
# credential value (avoids flagging the mere keyword in prose).
_BUILTIN_DETECTORS: Tuple[Tuple[str, re.Pattern], ...] = (
    ("pem", _PEM_RE),
    ("token", _TOKEN_RE),
    ("key_value", _KEY_VALUE_RE),
)


def _extra_patterns(config: Any = None) -> Tuple[Tuple[str, re.Pattern], ...]:
    """Parse ``config.SECRET_PATTERNS`` (comma-separated regexes) if set."""
    cfg = config if config is not None else settings
    raw = getattr(cfg, "SECRET_PATTERNS", "") or ""
    out = []
    for i, pat in enumerate(r.strip() for r in raw.split(",") if r.strip()):
        try:
            out.append((f"custom_{i}", re.compile(pat, re.IGNORECASE)))
        except re.error:
            continue
    return tuple(out)


def _apply_redactor(text: str, pattern: re.Pattern) -> str:
    """Replace only the *secret value* part of a match.

    For the key=value pattern the value is group 2; for token/PEM patterns the
    whole match is the secret.
    """
    if pattern is _KEY_VALUE_RE:
        return _KEY_VALUE_RE.sub(
            lambda m: m.group(1) + REDACTED, text
        )
    return pattern.sub(REDACTED, text)


def redact_secrets(text: Any, config: Any = None) -> str:
    """Return ``text`` with credential values replaced by ``[REDACTED]``."""
    if text is None:
        return ""
    s = str(text)
    for _, pat in (*_BUILTIN_REDACTORS, *_extra_patterns(config)):
        try:
            s = _apply_redactor(s, pat)
        except Exception:
            continue
    return s


def contains_secret(text: Any, config: Any = None) -> bool:
    """True if ``text`` carries a credential pattern (API key, password value,
    private-key block, token...)."""
    if text is None:
        return False
    s = str(text)
    for _, pat in (*_BUILTIN_DETECTORS, *_extra_patterns(config)):
        try:
            if pat.search(s):
                return True
        except Exception:
            continue
    return False


def redact_log_field(value: Any, config: Any = None, max_len: int = 1024) -> Any:
    """Safe renderer for a structured-log field.

    - Secret-looking values are replaced wholesale with ``[REDACTED]``.
    - Non-secret values are stringified and length-capped so a runaway value
      never bloats a log line.
    """
    if value is None:
        return None
    if contains_secret(value, config=config):
        return REDACTED
    s = str(value)
    if len(s) > max_len:
        return s[:max_len] + "..."
    return s


def is_sensitive_filename(path: Any) -> bool:
    """True if ``path`` is a secret/credential file (reuses Aşama 7 rules)."""
    if not path:
        return False
    return _is_sensitive_path(str(path))
