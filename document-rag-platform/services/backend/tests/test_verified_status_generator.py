"""Unit coverage for fail-closed historical-projection classification."""

from __future__ import annotations

import importlib.util
from pathlib import Path


REPO = Path(__file__).resolve().parents[4]
SCRIPT = REPO / "scripts/generate_verified_status.py"


def _module():
    spec = importlib.util.spec_from_file_location("cv_verified_status", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _projection(sha: str, *, warning: bool = True) -> str:
    suffix = "\n> **STALE UYARISI:** current checkout değildir.\n" if warning else "\n"
    return (
        "> **Sınıflandırma:** historical/non-canonical human projection\n"
        f"> **last_verified_sha:** `{sha}`\n"
        "> **last_verified_at:** `2026-09-05T00:00:00Z`\n"
        "> **evidence_manifest:** `evidence.json`\n" + suffix
    )


def test_current_projection_does_not_require_stale_warning() -> None:
    module = _module()
    head = "a" * 40
    status = module.inspect_historical_projection(
        _projection(head, warning=False), head=head
    )
    assert status["safe"] is True
    assert status["is_stale"] is False


def test_stale_projection_requires_explicit_warning() -> None:
    module = _module()
    status = module.inspect_historical_projection(
        _projection("a" * 40, warning=False), head="b" * 40
    )
    assert status["safe"] is False
    assert status["is_stale"] is True


def test_stale_projection_with_warning_is_safe_but_still_reported_stale() -> None:
    module = _module()
    status = module.inspect_historical_projection(_projection("a" * 40), head="b" * 40)
    assert status["safe"] is True
    assert status["is_stale"] is True
    assert status["stale_warning_present"] is True
