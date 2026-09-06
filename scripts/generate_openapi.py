#!/usr/bin/env python3
"""Deterministic OpenAPI export without startup, DB or provider effects."""

import argparse
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "document-rag-platform/services/backend"


def schema_text() -> str:
    # Fixed non-secret export-only config; no DB connections or lifespan.
    os.environ.update(
        {
            "APP_ENV": "local",
            "AUTH_MODE": "disabled",
            "BIND_HOST": "127.0.0.1",
            "LITELLM_API_KEY": "schema-export-not-a-provider-key",
            "DATABASE_URL": "postgresql://schema:schema@127.0.0.1:1/schema_export_only",
            "OBJECT_STORAGE_ENCRYPTION_KEY": "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY=",
        }
    )
    sys.path.insert(0, str(BACKEND))
    from src.main import app

    return (
        json.dumps(app.openapi(), indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "document-rag-platform/packages/contracts/openapi.json",
    )
    args = parser.parse_args()
    target = args.output
    generated = schema_text()
    if args.check:
        if not target.exists() or target.read_text() != generated:
            print("OpenAPI drift: regenerate schema and TypeScript before committing")
            return 1
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(generated)
    print("OpenAPI deterministic contract: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
