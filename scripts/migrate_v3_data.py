#!/usr/bin/env python3
"""Idempotently copy an admitted compatible snapshot into an empty V3 database."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

TOOL_VERSION = "1.0.0"
TARGET_HEAD = "cv3_00000001"
TABLES = (
    "embedding_profiles",
    "projects",
    "documents",
    "document_versions",
    "source_files",
    "document_artifacts",
    "ingestion_jobs",
    "ingestion_events",
    "chunks",
    "chunk_embeddings",
    "conversations",
    "messages",
    "message_citations",
)
CIRCULAR_FIELDS = {
    "documents": "active_version_id",
    "document_versions": "normalized_artifact_id",
}


class MigrationError(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def connect(url: str, *, readonly: bool):
    try:
        import psycopg2
    except ImportError as exc:
        raise MigrationError("psycopg2 is unavailable") from exc
    try:
        connection = psycopg2.connect(url)
        if readonly:
            connection.set_session(readonly=True, autocommit=False)
        return connection
    except Exception as exc:
        raise MigrationError("database connection unavailable") from exc


def revision(connection) -> str:
    with connection.cursor() as cursor:
        cursor.execute("SELECT version_num FROM alembic_version")
        return cursor.fetchone()[0]


def primary_key(connection, table: str) -> list[str]:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT a.attname
            FROM pg_index i
            JOIN pg_attribute a ON a.attrelid=i.indrelid AND a.attnum=ANY(i.indkey)
            WHERE i.indrelid=%s::regclass AND i.indisprimary
            ORDER BY array_position(i.indkey, a.attnum)
            """,
            (table,),
        )
        result = [row[0] for row in cursor.fetchall()]
    if not result:
        raise MigrationError(f"table has no primary key: {table}")
    return result


def source_rows(connection, table: str, pk: list[str]) -> list[dict[str, Any]]:
    order = ", ".join(f'"{column}"' for column in pk)
    with connection.cursor() as cursor:
        cursor.execute(f'SELECT to_jsonb(t) FROM "{table}" t ORDER BY {order}')
        rows = [row[0] for row in cursor.fetchall()]
    circular = CIRCULAR_FIELDS.get(table)
    if circular:
        for row in rows:
            row[circular] = None
    return rows


def identity_hash(rows: list[dict[str, Any]], pk: list[str]) -> str:
    identities = [[str(row[column]) for column in pk] for row in rows]
    material = json.dumps(identities, separators=(",", ":"), sort_keys=True).encode()
    return hashlib.sha256(material).hexdigest()


def copy_table(source, target, table: str) -> dict[str, Any]:
    from psycopg2.extras import Json, execute_batch

    pk = primary_key(source, table)
    rows = source_rows(source, table, pk)
    conflicts = ", ".join(f'"{column}"' for column in pk)
    statement = (
        f'INSERT INTO "{table}" SELECT * FROM '
        f'jsonb_populate_record(NULL::"{table}", %s::jsonb) '
        f"ON CONFLICT ({conflicts}) DO NOTHING"
    )
    if rows:
        with target.cursor() as cursor:
            execute_batch(
                cursor, statement, [(Json(row),) for row in rows], page_size=100
            )
    target.commit()
    with target.cursor() as cursor:
        cursor.execute(f'SELECT to_jsonb(t) FROM "{table}" t ORDER BY {conflicts}')
        target_rows = [row[0] for row in cursor.fetchall()]
    source_ids = identity_hash(rows, pk)
    target_subset = [
        row
        for row in target_rows
        if tuple(str(row[column]) for column in pk)
        in {tuple(str(item[column]) for column in pk) for item in rows}
    ]
    target_ids = identity_hash(target_subset, pk)
    if len(target_subset) != len(rows) or target_ids != source_ids:
        raise MigrationError(f"identity verification failed for table {table}")
    return {"table": table, "rows": len(rows), "identity_sha256": source_ids}


def restore_circular_links(source, target) -> None:
    mappings = (
        ("documents", "id", "active_version_id"),
        ("document_versions", "id", "normalized_artifact_id"),
    )
    for table, pk, field in mappings:
        with source.cursor() as cursor:
            cursor.execute(
                f'SELECT "{pk}", "{field}" FROM "{table}" WHERE "{field}" IS NOT NULL'
            )
            rows = cursor.fetchall()
        if rows:
            with target.cursor() as cursor:
                cursor.executemany(
                    f'UPDATE "{table}" SET "{field}"=%s WHERE "{pk}"=%s',
                    [(value, identity) for identity, value in rows],
                )
            target.commit()


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-url", default=os.getenv("SOURCE_DATABASE_URL"))
    parser.add_argument("--target-url", default=os.getenv("TARGET_DATABASE_URL"))
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--stop-after-table", choices=TABLES)
    parser.add_argument("--json-output", type=Path)
    parser.add_argument("--strict", action="store_true")
    return parser.parse_args(argv)


def execute(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    if not args.source_url or not args.target_url:
        raise MigrationError("source and target database URLs are required")
    started = utc_now()
    source = connect(args.source_url, readonly=True)
    target = connect(args.target_url, readonly=False)
    try:
        source_revision = revision(source)
        target_revision = revision(target)
        if target_revision != TARGET_HEAD:
            raise MigrationError("target database is not at the expected V3 head")
        report: dict[str, Any] = {
            "schema": "context-vault-v3-data-migration/v1",
            "tool_version": TOOL_VERSION,
            "started_at_utc": started,
            "source_revision": source_revision,
            "target_revision": target_revision,
            "source_read_only": True,
            "mode": "APPLY" if args.apply else "DRY_RUN",
            "tables": [],
        }
        if not args.apply:
            for table in TABLES:
                rows = source_rows(source, table, primary_key(source, table))
                report["tables"].append({"table": table, "rows": len(rows)})
            report["result"] = "PASS"
            return report, 0
        for table in TABLES:
            report["tables"].append(copy_table(source, target, table))
            if table == args.stop_after_table:
                report["result"] = "RECOVERY_REQUIRED"
                report["resume_safe"] = True
                return report, 1
        restore_circular_links(source, target)
        report["result"] = "PASS"
        report["resume_safe"] = True
        return report, 0
    finally:
        source.close()
        target.close()


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        report, exit_code = execute(args)
    except MigrationError as exc:
        report = {
            "schema": "context-vault-v3-data-migration/v1",
            "tool_version": TOOL_VERSION,
            "finished_at_utc": utc_now(),
            "result": "ENVIRONMENT_UNAVAILABLE",
            "error": str(exc),
        }
        exit_code = 3
    report["finished_at_utc"] = utc_now()
    serialized = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.json_output:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(serialized, encoding="utf-8")
    sys.stdout.write(serialized)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
