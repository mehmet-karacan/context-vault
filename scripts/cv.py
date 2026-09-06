#!/usr/bin/env python3
"""Repository-local entry point for the Context Vault command protocol."""

from __future__ import annotations

import os
import sys
from pathlib import Path


BACKEND = (
    Path(__file__).resolve().parents[1]
    / "document-rag-platform"
    / "services"
    / "backend"
)
if os.environ.get("CV_CLI_UV_REEXEC") != "1":
    os.environ["CV_CLI_UV_REEXEC"] = "1"
    os.execvp(
        "uv",
        ["uv", "run", "--project", str(BACKEND), "python", __file__, *sys.argv[1:]],
    )
sys.path.insert(0, str(BACKEND))

from src.context_vault.cli import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
