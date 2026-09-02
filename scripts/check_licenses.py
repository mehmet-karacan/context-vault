#!/usr/bin/env python3
"""Apply the repository's documented strong-copyleft dependency policy."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


DENIED_MARKERS = ("AGPL", "GPL-3", "GPLV3", "SSPL", "BUSL")


def node_components(sbom: dict[str, Any]) -> list[tuple[str, str]]:
    result = []
    for component in sbom.get("components", []):
        licenses = component.get("licenses") or []
        labels = []
        for item in licenses:
            license_data = item.get("license") or {}
            expression = item.get("expression")
            labels.append(
                expression
                or license_data.get("id")
                or license_data.get("name")
                or "UNKNOWN"
            )
        result.append(
            (component.get("name", "UNKNOWN"), " OR ".join(labels) or "UNKNOWN")
        )
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--python-report", type=Path, required=True)
    parser.add_argument("--node-sbom", type=Path, required=True)
    parser.add_argument("--json-output", type=Path, required=True)
    args = parser.parse_args()

    python_data = json.loads(args.python_report.read_text(encoding="utf-8"))
    node_data = json.loads(args.node_sbom.read_text(encoding="utf-8"))
    packages = [
        ("python", item.get("Name", "UNKNOWN"), item.get("License", "UNKNOWN"))
        for item in python_data
    ]
    packages.extend(
        ("node", name, license_name)
        for name, license_name in node_components(node_data)
    )

    denied = []
    for ecosystem, name, license_name in packages:
        normalized = license_name.upper().replace(" ", "")
        if "LGPL" in normalized:
            continue
        if any(marker in normalized for marker in DENIED_MARKERS):
            denied.append(
                {"ecosystem": ecosystem, "package": name, "license": license_name}
            )
    receipt = {
        "schema_version": 1,
        "status": "PASS" if not denied else "FAIL",
        "policy": {"denied_markers": list(DENIED_MARKERS)},
        "packages_checked": len(packages),
        "denied": denied,
    }
    args.json_output.parent.mkdir(parents=True, exist_ok=True)
    args.json_output.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    print(f"license policy: {receipt['status']} ({len(packages)} packages checked)")
    return 0 if not denied else 1


if __name__ == "__main__":
    raise SystemExit(main())
