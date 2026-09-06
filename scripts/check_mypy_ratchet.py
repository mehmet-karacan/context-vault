#!/usr/bin/env python3
"""Run MyPy strict mode over the monotonic critical-package ratchet."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
RATCHET_PATH = REPO_ROOT / "mypy-ratchet.json"


def main() -> int:
    config = json.loads(RATCHET_PATH.read_text(encoding="utf-8"))
    paths = config.get("strict_paths")
    minimum_paths = config.get("minimum_paths")
    if not isinstance(paths, list) or not all(isinstance(path, str) for path in paths):
        print("mypy ratchet: strict_paths must be a string list", file=sys.stderr)
        return 2
    if not isinstance(minimum_paths, int) or len(paths) < minimum_paths:
        print(
            "mypy ratchet: strict scope shrank below its recorded floor",
            file=sys.stderr,
        )
        return 2
    if paths != sorted(set(paths)):
        print("mypy ratchet: strict_paths must be sorted and unique", file=sys.stderr)
        return 2

    missing = [path for path in paths if not (REPO_ROOT / path).exists()]
    if missing:
        print(f"mypy ratchet: missing paths: {', '.join(missing)}", file=sys.stderr)
        return 2

    command = [sys.executable, "-m", "mypy", "--strict", *paths]
    return subprocess.run(command, cwd=REPO_ROOT, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
