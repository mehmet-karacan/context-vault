#!/usr/bin/env python3
"""Verify the configured Context Vault migration graph and database state."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

TOOL_VERSION = "1.2.0"
EXPECTED_HEAD = "cv3_00000007"
VERSIONS_RELATIVE = Path("document-rag-platform/services/backend/alembic/versions_v3")
BACKEND_RELATIVE = Path("document-rag-platform/services/backend")


class EnvironmentUnavailable(RuntimeError):
    """A required local executable, package or database is unavailable."""


def _literal(tree: ast.Module, name: str) -> Any:
    for node in tree.body:
        target: str | None = None
        value: ast.expr | None = None
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = (
                node.targets[0].id if isinstance(node.targets[0], ast.Name) else None
            )
            value = node.value
        elif isinstance(node, ast.AnnAssign):
            target = node.target.id if isinstance(node.target, ast.Name) else None
            value = node.value
        if target == name and value is not None:
            return ast.literal_eval(value)
    raise ValueError(f"missing literal assignment: {name}")


def migration_graph(repo: Path) -> dict[str, Any]:
    directory = repo / VERSIONS_RELATIVE
    if not directory.is_dir():
        raise EnvironmentUnavailable("configured migration directory is unavailable")
    revisions: list[dict[str, Any]] = []
    referenced: set[str] = set()
    for path in sorted(directory.glob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            revision = _literal(tree, "revision")
            down_revision = _literal(tree, "down_revision")
        except (OSError, SyntaxError, ValueError) as exc:
            raise EnvironmentUnavailable(f"cannot parse migration {path.name}") from exc
        parents = [] if down_revision is None else [down_revision]
        if not isinstance(revision, str) or not all(
            isinstance(item, str) for item in parents
        ):
            raise EnvironmentUnavailable(f"invalid migration graph entry {path.name}")
        referenced.update(parents)
        revisions.append(
            {
                "revision": revision,
                "down_revision": parents,
                "path": path.relative_to(repo).as_posix(),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        )
    heads = sorted(
        item["revision"] for item in revisions if item["revision"] not in referenced
    )
    return {"heads": heads, "revisions": revisions}


def run_alembic(repo: Path, database_url: str, *arguments: str) -> None:
    env = os.environ.copy()
    env["DATABASE_URL"] = database_url
    for key in ("LITELLM_API_KEY", "MINIO_SECRET_KEY", "REDIS_URL"):
        env.pop(key, None)
    try:
        subprocess.run(
            [sys.executable, "-m", "alembic", *arguments],
            cwd=repo / BACKEND_RELATIVE,
            env=env,
            check=True,
            capture_output=True,
            text=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        raise EnvironmentUnavailable(
            f"Alembic command failed: {' '.join(arguments)}"
        ) from exc


def database_snapshot(database_url: str) -> dict[str, Any]:
    try:
        import psycopg2
    except ImportError as exc:
        raise EnvironmentUnavailable("psycopg2 is unavailable") from exc

    try:
        connection = psycopg2.connect(database_url)
    except Exception as exc:
        raise EnvironmentUnavailable("database connection is unavailable") from exc

    queries = {
        "cross_document_version_chunks": """
            SELECT count(*) FROM chunks c
            JOIN document_versions v ON v.id = c.version_id
            WHERE c.document_id <> v.document_id
        """,
        "active_profiles_missing_config_hash": """
            SELECT count(*) FROM embedding_profiles
            WHERE is_active AND (config_hash IS NULL OR config_hash = '')
        """,
        "unversioned_chunks": "SELECT count(*) FROM chunks WHERE version_id IS NULL",
        "profileless_embeddings": """
            SELECT count(*) FROM chunk_embeddings ce
            LEFT JOIN embedding_profiles ep ON ep.id = ce.embedding_profile_id
            WHERE ep.id IS NULL
        """,
        "indexed_documents_without_active_version": """
            SELECT count(*) FROM documents
            WHERE status = 'indexed' AND deleted_at IS NULL
              AND active_version_id IS NULL
        """,
        "invalid_active_document_versions": """
            SELECT count(*) FROM documents d
            JOIN document_versions v ON v.id = d.active_version_id
            WHERE v.document_id <> d.id OR v.status NOT IN ('ready', 'completed')
        """,
        "invalid_ingestion_jobs": """
            SELECT count(*) FROM ingestion_jobs
            WHERE status NOT IN ('queued','running','retrying','completed','failed','cancelled')
               OR (progress IS NOT NULL AND (progress < 0 OR progress > 100))
               OR (status = 'failed' AND (error_code IS NULL OR error_message IS NULL))
        """,
        "invalid_ingestion_events": """
            SELECT count(*) FROM ingestion_events
            WHERE (stage IS NOT NULL AND stage NOT IN
                    ('validating','storing','parsing','ocr','normalizing','chunking','embedding','indexing','activating'))
               OR (status IS NOT NULL AND status NOT IN
                    ('started','queued','running','retrying','completed','failed','cancelled'))
        """,
        "document_errors_without_details": """
            SELECT count(*) FROM documents
            WHERE status = 'error' AND (error_code IS NULL OR error_message IS NULL)
        """,
        "version_failures_without_details": """
            SELECT count(*) FROM document_versions
            WHERE status = 'failed' AND (error_code IS NULL OR error_message IS NULL)
        """,
        "versions_without_immutable_profiles_or_policy": """
            SELECT count(*) FROM document_versions
            WHERE parser_profile IS NULL OR chunker_profile IS NULL
               OR embedding_profile_id IS NULL
               OR content_policy_decision_id IS NULL
        """,
        "unsafe_remote_policy_decisions": """
            SELECT count(*) FROM content_policy_decisions
            WHERE permit_remote_embedding
              AND (contains_credentials OR contains_private_key OR contains_pii
                   OR classification IN ('confidential','restricted'))
        """,
        "terminal_jobs_without_receipts": """
            SELECT count(*) FROM ingestion_jobs j
            WHERE j.status IN ('completed','failed','cancelled')
              AND NOT EXISTS (
                SELECT 1 FROM ingestion_receipts r
                WHERE r.job_id = j.id
                  AND r.status = CASE j.status
                    WHEN 'completed' THEN 'completed'
                    WHEN 'cancelled' THEN 'cancelled'
                    ELSE 'failed' END
              )
        """,
        "invalid_job_leases": """
            SELECT count(*) FROM ingestion_jobs
            WHERE (lease_expires_at IS NOT NULL AND lease_owner IS NULL)
               OR (status IN ('completed','failed','cancelled')
                   AND lease_expires_at IS NOT NULL)
        """,
        "invalid_activated_versions": """
            SELECT count(*) FROM document_versions
            WHERE activated_at IS NOT NULL
              AND status NOT IN ('ready', 'completed', 'superseded')
        """,
        "deprecated_chunks_embedding_columns": """
            SELECT count(*) FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = 'chunks'
              AND column_name = 'embedding'
        """,
        "non_utc_timestamp_columns": """
            SELECT count(*)
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND data_type = 'timestamp without time zone'
        """,
        "chunks_without_search_profile": """
            SELECT count(*) FROM chunks
            WHERE search_profile IS NULL OR search_profile = ''
        """,
        "invalid_retrieval_runs": """
            SELECT count(*) FROM retrieval_runs
            WHERE candidate_count < 0 OR selected_count < 0
               OR query_hash IS NULL OR length(query_hash) <> 64
               OR principal_id IS NULL OR workspace_id IS NULL
               OR project_id IS NULL OR embedding_profile_id IS NULL
        """,
        "cross_workspace_conversations": """
            SELECT count(*) FROM conversations c
            JOIN projects p ON p.id = c.project_id
            WHERE c.workspace_id IS NOT NULL AND c.workspace_id <> p.workspace_id
        """,
        "validated_citations_without_provenance": """
            SELECT count(*) FROM message_citations
            WHERE validation_result = 'valid'
              AND (evidence_hash IS NULL OR evidence_snapshot_encrypted IS NULL
                   OR prompt_hash IS NULL OR citation_label IS NULL)
        """,
        "claims_without_citations": """
            SELECT count(*) FROM message_claims mc
            WHERE NOT EXISTS (
                SELECT 1 FROM claim_citations cc WHERE cc.claim_id = mc.id
            )
        """,
    }
    try:
        with connection, connection.cursor() as cursor:
            cursor.execute("SELECT version_num FROM alembic_version")
            revision = cursor.fetchone()[0]
            cursor.execute(
                """
                SELECT table_name, column_name, data_type, is_nullable,
                       COALESCE(udt_name, '')
                FROM information_schema.columns
                WHERE table_schema = 'public'
                ORDER BY table_name, ordinal_position
                """
            )
            columns = cursor.fetchall()
            cursor.execute(
                """
                SELECT c.conname, c.contype, pg_get_constraintdef(c.oid)
                FROM pg_constraint c
                JOIN pg_namespace n ON n.oid = c.connamespace
                WHERE n.nspname = 'public'
                ORDER BY c.conname
                """
            )
            constraints = cursor.fetchall()
            invariants: dict[str, int] = {}
            for name, sql in queries.items():
                cursor.execute(sql)
                invariants[name] = int(cursor.fetchone()[0])
            cursor.execute("SELECT count(*) FROM embedding_profiles WHERE is_active")
            active_profiles = int(cursor.fetchone()[0])
    except Exception as exc:
        raise EnvironmentUnavailable("database verification query failed") from exc
    finally:
        connection.close()

    schema_material = json.dumps(
        {"columns": columns, "constraints": constraints},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return {
        "revision": revision,
        "schema_sha256": hashlib.sha256(schema_material).hexdigest(),
        "column_count": len(columns),
        "constraint_count": len(constraints),
        "active_profiles": active_profiles,
        "invariants": invariants,
    }


def verify(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    repo = args.repo.resolve()
    graph = migration_graph(repo)
    findings: list[dict[str, str]] = []
    if graph["heads"] != [args.expected_head]:
        findings.append(
            {
                "criterion": "single_expected_head",
                "severity": "error",
                "message": "configured migration graph does not have the expected single head",
            }
        )

    snapshot = None
    if args.database_url:
        if args.upgrade:
            run_alembic(repo, args.database_url, "upgrade", "head")
            run_alembic(repo, args.database_url, "upgrade", "head")
        snapshot = database_snapshot(args.database_url)
        if snapshot["revision"] != args.expected_head:
            findings.append(
                {
                    "criterion": "database_at_expected_head",
                    "severity": "error",
                    "message": "database revision does not match expected head",
                }
            )
        if snapshot["active_profiles"] != 1:
            findings.append(
                {
                    "criterion": "one_active_embedding_profile",
                    "severity": "error",
                    "message": "database does not have exactly one active embedding profile",
                }
            )
        for name, count in snapshot["invariants"].items():
            if count:
                findings.append(
                    {
                        "criterion": name,
                        "severity": "error",
                        "message": f"invariant violation count is {count}",
                    }
                )
    elif args.strict:
        findings.append(
            {
                "criterion": "database_available",
                "severity": "error",
                "message": "strict mode requires --database-url or DATABASE_URL",
            }
        )

    failed = any(item["severity"] == "error" for item in findings)
    return (
        {
            "schema": "context-vault-migration-verification/v1",
            "tool_version": TOOL_VERSION,
            "observed_at_utc": datetime.now(timezone.utc)
            .isoformat()
            .replace("+00:00", "Z"),
            "repository_head": subprocess.run(
                ["git", "-C", str(repo), "rev-parse", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip(),
            "expected_head": args.expected_head,
            "migration_graph": graph,
            "database": snapshot,
            "provider_credentials_loaded": False,
            "findings": findings,
            "result": "FAIL" if failed else "PASS",
        },
        1 if failed else 0,
    )


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo", type=Path, default=Path(__file__).resolve().parents[1]
    )
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL"))
    parser.add_argument("--expected-head", default=EXPECTED_HEAD)
    parser.add_argument("--upgrade", action="store_true")
    parser.add_argument("--json-output", type=Path)
    parser.add_argument("--strict", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        report, exit_code = verify(args)
    except EnvironmentUnavailable as exc:
        report = {
            "schema": "context-vault-migration-verification/v1",
            "tool_version": TOOL_VERSION,
            "observed_at_utc": datetime.now(timezone.utc)
            .isoformat()
            .replace("+00:00", "Z"),
            "result": "ENVIRONMENT_UNAVAILABLE",
            "error": str(exc),
        }
        exit_code = 3
    serialized = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.json_output:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(serialized, encoding="utf-8")
    sys.stdout.write(serialized)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
