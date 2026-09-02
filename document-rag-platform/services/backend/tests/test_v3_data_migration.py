"""Pure regression tests for the resumable V3 data migration tool."""

import importlib.util
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[4]
SCRIPT_PATH = REPO_ROOT / "scripts" / "migrate_v3_data.py"
SPEC = importlib.util.spec_from_file_location("migrate_v3_data", SCRIPT_PATH)
assert SPEC and SPEC.loader
migrate_v3_data = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(migrate_v3_data)


def test_identity_hash_is_deterministic_and_content_free():
    rows = [
        {"id": "b", "content": "secret-two"},
        {"id": "a", "content": "secret-one"},
    ]

    first = migrate_v3_data.identity_hash(rows, ["id"])
    second = migrate_v3_data.identity_hash(list(rows), ["id"])

    assert first == second
    assert "secret" not in first


def test_copy_order_places_dependencies_before_dependents():
    tables = migrate_v3_data.TABLES

    assert tables.index("projects") < tables.index("documents")
    assert tables.index("documents") < tables.index("document_versions")
    assert tables.index("document_versions") < tables.index("chunks")
    assert tables.index("chunks") < tables.index("chunk_embeddings")
    assert tables.index("messages") < tables.index("message_citations")
