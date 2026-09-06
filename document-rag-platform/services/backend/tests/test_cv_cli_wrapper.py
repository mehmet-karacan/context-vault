from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[4]


def test_repository_wrapper_reexecutes_in_locked_backend_environment() -> None:
    environment = {
        "HOME": os.environ["HOME"],
        "PATH": os.environ["PATH"],
    }
    completed = subprocess.run(
        [
            str(REPO_ROOT / "scripts" / "cv.py"),
            "--json",
            "doctor",
            "--start",
            str(REPO_ROOT),
        ],
        cwd=REPO_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["command"] == "doctor"
    assert payload["ok"] is True
    assert payload["result"]["project_root"] == str(REPO_ROOT)
    assert len(payload["result"]["manifest_hash"]) == 64
