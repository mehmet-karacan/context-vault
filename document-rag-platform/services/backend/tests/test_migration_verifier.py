"""Unit coverage for the repository-level migration verifier."""

import importlib.util
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[4]
SCRIPT_PATH = REPO_ROOT / "scripts" / "verify_migrations.py"
SPEC = importlib.util.spec_from_file_location("verify_migrations", SCRIPT_PATH)
assert SPEC and SPEC.loader
verify_migrations = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verify_migrations)


def test_repository_migration_graph_has_expected_single_head():
    graph = verify_migrations.migration_graph(REPO_ROOT)

    assert graph["heads"] == [verify_migrations.EXPECTED_HEAD]
    assert len(graph["revisions"]) == 3


def test_migration_graph_reports_multiple_heads(tmp_path):
    directory = tmp_path / verify_migrations.VERSIONS_RELATIVE
    directory.mkdir(parents=True)
    (directory / "one.py").write_text(
        "revision = 'one'\ndown_revision = None\n", encoding="utf-8"
    )
    (directory / "two.py").write_text(
        "revision = 'two'\ndown_revision = None\n", encoding="utf-8"
    )

    graph = verify_migrations.migration_graph(tmp_path)

    assert graph["heads"] == ["one", "two"]
