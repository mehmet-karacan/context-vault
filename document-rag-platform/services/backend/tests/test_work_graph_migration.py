from __future__ import annotations

import ast
from pathlib import Path

from src import models  # noqa: F401
from src.migration_settings import EXPECTED_ALEMBIC_HEAD
from src.persistence import Base


MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions_v3"
    / "20260906_0000_cv3_00000007_context_vault_work_graph.py"
)
EXPECTED_TABLES = {
    "work_items",
    "work_item_dependencies",
    "work_attempts",
    "work_claims",
    "work_events",
    "work_approvals",
    "work_receipts",
    "artifact_refs",
    "decision_records",
    "knowledge_items",
    "knowledge_revisions",
    "context_sources",
    "context_manifests",
    "compiled_contexts",
    "provider_registry",
    "model_registry",
    "skill_registry",
}


def test_work_graph_schema_is_registered_and_head_is_advanced() -> None:
    assert EXPECTED_ALEMBIC_HEAD == "cv3_00000007"
    assert EXPECTED_TABLES.issubset(Base.metadata.tables)
    attempt = Base.metadata.tables["work_attempts"]
    assert {
        "claim_request_hash",
        "rollback_idempotency_key",
        "rollback_request_hash",
        "rollback_started_at",
    }.issubset(attempt.columns.keys())
    constraint_names = {constraint.name for constraint in attempt.constraints}
    assert {
        "ck_work_attempt_rollback_intent",
        "uq_work_attempt_rollback_idempotency",
    }.issubset(constraint_names)


def test_upgrade_path_is_additive_and_data_preserving() -> None:
    tree = ast.parse(MIGRATION.read_text(encoding="utf-8"))
    upgrade = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "upgrade"
    )
    forbidden = {"drop_table", "drop_column", "execute_many", "bulk_insert"}
    calls = {
        node.func.attr
        for node in ast.walk(upgrade)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert calls.isdisjoint(forbidden)


def test_knowledge_revision_model_declares_complete_authority_audit_constraints() -> (
    None
):
    table = Base.metadata.tables["knowledge_revisions"]
    constraint_names = {constraint.name for constraint in table.constraints}
    assert {
        "ck_knowledge_revision_reviewer",
        "ck_knowledge_revision_reviewer_pair",
        "ck_knowledge_revision_approver_pair",
        "ck_knowledge_revision_terminal_pair",
        "ck_knowledge_revision_authority_audit",
        "ck_knowledge_revision_terminal_audit",
    }.issubset(constraint_names)


def test_migration_declares_reversible_order_and_append_only_guards() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    assert 'down_revision: str | None = "cv3_00000006"' in source
    assert "cv3_reject_append_only_mutation" in source
    assert "cv3_enforce_work_item_transition" in source
    assert "cv3_enforce_knowledge_revision_lifecycle" in source
    assert "cv3_enforce_knowledge_active_head" in source
    assert "DEFERRABLE INITIALLY DEFERRED" in source
    assert "status must start at PROPOSED" in source
    assert "invalid knowledge transition" in source
    assert "active knowledge revision must belong to its item and be APPROVED" in source
    assert "every APPROVED knowledge revision must be the active head" in source
    assert source.index('op.drop_table("compiled_contexts")') < source.index(
        'op.drop_table("context_manifests")'
    )
