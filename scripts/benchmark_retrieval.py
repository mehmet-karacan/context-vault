#!/usr/bin/env python3
"""Run a bounded, synthetic retrieval benchmark against PostgreSQL.

The script creates transaction-local temporary tables, never calls a model
provider, rolls back all rows, and emits aggregate/public-safe JSON only.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path

import psycopg2
import psycopg2.extras

DIMENSION = 1024


def _vector(values: list[float]) -> str:
    return "[" + ",".join(f"{value:.8f}" for value in values) + "]"


def _row_vector(index: int) -> str:
    values = [0.0] * DIMENSION
    for offset in range(12):
        values[offset] = math.sin((index + 1) * (offset + 1)) + 1.5
    return _vector(values)


def _ids(cursor, sql: str, params: tuple = ()) -> list[int]:
    cursor.execute(sql, params)
    return [int(row[0]) for row in cursor.fetchall()]


def benchmark(database_url: str) -> dict:
    connection = psycopg2.connect(database_url)
    try:
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL statement_timeout='60s'")
            cursor.execute(
                "CREATE TEMP TABLE cv3_vector_bench "
                "(id integer PRIMARY KEY, embedding vector(1024)) ON COMMIT DROP"
            )
            psycopg2.extras.execute_values(
                cursor,
                "INSERT INTO cv3_vector_bench (id,embedding) VALUES %s",
                [(index, _row_vector(index)) for index in range(320)],
                template="(%s,%s::vector)",
                page_size=80,
            )
            cursor.execute(
                "CREATE INDEX cv3_vector_bench_hnsw ON cv3_vector_bench "
                "USING hnsw (embedding vector_cosine_ops)"
            )
            cursor.execute("ANALYZE cv3_vector_bench")
            query_vector = _row_vector(137)
            cursor.execute("SET LOCAL enable_indexscan=off")
            cursor.execute("SET LOCAL enable_bitmapscan=off")
            exact = _ids(
                cursor,
                "SELECT id FROM cv3_vector_bench "
                "ORDER BY embedding <=> %s::vector LIMIT 10",
                (query_vector,),
            )
            cursor.execute("SET LOCAL enable_indexscan=on")
            cursor.execute("SET LOCAL enable_bitmapscan=on")
            cursor.execute("SET LOCAL enable_seqscan=off")
            hnsw: dict[str, dict] = {}
            for ef_search in (20, 40, 80):
                cursor.execute(
                    "SELECT set_config('hnsw.ef_search', %s, true)",
                    (str(ef_search),),
                )
                timings: list[float] = []
                observed: list[int] = []
                for _ in range(12):
                    started = time.perf_counter()
                    observed = _ids(
                        cursor,
                        "SELECT id FROM cv3_vector_bench "
                        "ORDER BY embedding <=> %s::vector LIMIT 10",
                        (query_vector,),
                    )
                    timings.append((time.perf_counter() - started) * 1000)
                hnsw[str(ef_search)] = {
                    "recall_at_10": len(set(exact) & set(observed)) / 10,
                    "median_latency_ms": round(statistics.median(timings), 4),
                    "p95_latency_ms": round(sorted(timings)[-2], 4),
                }
            cursor.execute(
                "EXPLAIN SELECT id FROM cv3_vector_bench "
                "ORDER BY embedding <=> %s::vector LIMIT 10",
                (query_vector,),
            )
            vector_plan = "\n".join(row[0] for row in cursor.fetchall())

            cursor.execute(
                "CREATE TEMP TABLE cv3_fts_bench "
                "(id integer PRIMARY KEY, body text, search_vector tsvector) ON COMMIT DROP"
            )
            fts_rows = [
                (1, "payment invoice reconciliation"),
                (2, "payment flag exact phrase"),
                (3, "flag payment reversed order"),
                (4, "invoice archival"),
                (5, "what is unrelated filler"),
                (6, "stp network protocol"),
            ]
            psycopg2.extras.execute_values(
                cursor,
                "INSERT INTO cv3_fts_bench (id,body,search_vector) VALUES %s",
                [(row_id, body, body) for row_id, body in fts_rows],
                template="(%s,%s,to_tsvector('simple',%s))",
            )
            cursor.execute(
                "CREATE INDEX cv3_fts_bench_gin ON cv3_fts_bench USING gin(search_vector)"
            )
            cases = [
                (
                    "websearch_or",
                    "websearch_to_tsquery",
                    "payment OR invoice",
                    {1, 2, 3, 4},
                ),
                ("phrase", "phraseto_tsquery", "payment flag", {2}),
                ("plain_baseline_or", "plainto_tsquery", "payment OR invoice", set()),
                ("filtered_stopword", "websearch_to_tsquery", "stp", {6}),
                (
                    "unfiltered_stopword_baseline",
                    "plainto_tsquery",
                    "what is stp",
                    set(),
                ),
            ]
            fts: dict[str, dict] = {}
            for label, function, query, expected in cases:
                cursor.execute(
                    f"SELECT id FROM cv3_fts_bench WHERE search_vector @@ {function}('simple',%s) ORDER BY id",
                    (query,),
                )
                found = {int(row[0]) for row in cursor.fetchall()}
                true_positive = len(found & expected)
                fts[label] = {
                    "hits": len(found),
                    "precision": round(true_positive / len(found), 4)
                    if found
                    else (1.0 if not expected else 0.0),
                    "recall": round(true_positive / len(expected), 4)
                    if expected
                    else (1.0 if not found else 0.0),
                }
            cursor.execute("SET LOCAL enable_seqscan=off")
            cursor.execute(
                "EXPLAIN SELECT id FROM cv3_fts_bench "
                "WHERE search_vector @@ websearch_to_tsquery('simple','payment')"
            )
            fts_plan = "\n".join(row[0] for row in cursor.fetchall())

            cursor.execute(
                "CREATE TEMP TABLE cv3_identifier_bench "
                "(id integer PRIMARY KEY, identifiers text[], symbol_name text) ON COMMIT DROP"
            )
            psycopg2.extras.execute_values(
                cursor,
                "INSERT INTO cv3_identifier_bench (id,identifiers,symbol_name) VALUES %s",
                [
                    (1, ["PAYMENT_FLAG"], "PAYMENT_FLAG"),
                    (2, ["payment_flag"], "payment_flag"),
                    (3, [], "payment_flag_extra"),
                    (4, [], "paymant_flag"),
                    (5, [], "xxpayment_flagxx"),
                    (6, [], "invoice_total"),
                ],
            )
            cursor.execute(
                "SELECT similarity(lower(symbol_name),'payment_flag') "
                "FROM cv3_identifier_bench WHERE id=4"
            )
            trigram_similarity = float(cursor.fetchone()[0])
            prefix_hits = _ids(
                cursor,
                "SELECT id FROM cv3_identifier_bench "
                "WHERE lower(symbol_name) LIKE 'payment\\_flag%%' ORDER BY id",
            )
            substring_hits = _ids(
                cursor,
                "SELECT id FROM cv3_identifier_bench "
                "WHERE lower(symbol_name) LIKE '%%payment\\_flag%%' ORDER BY id",
            )
            identifier_calibration = {
                "score_ladder": {
                    "exact": 1.0,
                    "normalized_exact": 0.95,
                    "symbol_exact": 0.9,
                    "qualified_exact": 0.85,
                    "prefix": 0.75,
                    "qualified_prefix": 0.72,
                    "path_prefix": 0.7,
                    "substring_opt_in": 0.5,
                },
                "typo_trigram_similarity": round(trigram_similarity, 4),
                "default_prefix_hit_count": len(prefix_hits),
                "explicit_substring_hit_count": len(substring_hits),
                "substring_additional_hits": len(
                    set(substring_hits) - set(prefix_hits)
                ),
                "leading_wildcard_default": False,
            }

            chosen_ef = min(
                (
                    int(key)
                    for key, value in hnsw.items()
                    if value["recall_at_10"] >= 0.99
                ),
                default=80,
            )
            return {
                "schema": "context-vault-retrieval-benchmark/v1",
                "observed_at_utc": datetime.now(timezone.utc)
                .isoformat()
                .replace("+00:00", "Z"),
                "dataset": {
                    "kind": "synthetic",
                    "vector_rows": 320,
                    "fts_rows": len(fts_rows),
                },
                "provider_credentials_loaded": False,
                "transaction_rolled_back": True,
                "hnsw": hnsw,
                "chosen_hnsw_ef_search": chosen_ef,
                "vector_index_used": "cv3_vector_bench_hnsw" in vector_plan,
                "fts": fts,
                "fts_index_used": "cv3_fts_bench_gin" in fts_plan,
                "identifier_calibration": identifier_calibration,
                "selected_fts_strategy": {
                    "default": "websearch_to_tsquery(simple)",
                    "quoted": "phraseto_tsquery(simple)",
                    "identifier": "exact-normalized-prefix-trigram",
                    "leading_wildcard_default": False,
                },
            }
    finally:
        connection.rollback()
        connection.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL"))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not args.database_url:
        parser.error("--database-url or DATABASE_URL is required")
    report = benchmark(args.database_url)
    payload = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
    print(payload, end="")
    return 0 if report["vector_index_used"] and report["fts_index_used"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
