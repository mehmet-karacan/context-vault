"""Contract generation is repeatable and its check rejects a stale schema."""

import subprocess
import sys
from pathlib import Path


def test_openapi_export_and_drift_gate(tmp_path):
    root = Path(__file__).resolve().parents[4]
    script = root / "scripts/generate_openapi.py"
    target = tmp_path / "openapi.json"
    command = [sys.executable, str(script), "--output", str(target)]
    first = subprocess.run(command, cwd=tmp_path, capture_output=True, text=True)
    assert first.returncode == 0, first.stderr
    content = target.read_text()
    second = subprocess.run(
        command + ["--check"], cwd=root, capture_output=True, text=True
    )
    assert second.returncode == 0, second.stderr
    assert content == target.read_text()
    target.write_text("{}\n")
    drift = subprocess.run(
        command + ["--check"], cwd=tmp_path, capture_output=True, text=True
    )
    assert drift.returncode == 1
    assert "OpenAPI drift" in drift.stdout
