# Schema index query-plan evidence — 2026-09-02

Environment: PostgreSQL 16 with pgvector, isolated disposable
`cv3-a4-postgres-verify` container, migration head `cv3_00000003`.

The measurement inserted 100 projects and 10,000 related documents, versions,
chunks, jobs, conversations, and audit events in one transaction, ran
`ANALYZE`, executed each target query with `EXPLAIN (ANALYZE, BUFFERS)`, and
then rolled the transaction back. A post-run query returned zero `perf-%`
projects, so no fixture or user data was retained.

| Target query | Selected plan | Actual execution |
|---|---|---:|
| Active error documents by project/status | `Index Scan using ix_documents_project_status_active` | 0.038 ms |
| Ready/completed versions by document | `Index Scan using ix_document_versions_document_status` | 0.020 ms |
| Chunks by document/version | `Index Scan using ix_chunks_document_version` | 0.025 ms |
| Retrying embedding jobs | `Index Scan using ix_ingestion_jobs_status_stage` | 0.104 ms |
| Active conversations by project | `Index Scan using ix_conversations_project_active` | 0.026 ms |
| Latest 20 workspace audit events | `Index Scan Backward using ix_audit_events_workspace_created` | 0.058 ms |

All six target relations used their intended migration-managed index. The
supporting one-row lookup subqueries used sequential scans only on the tiny
100-row `projects` table or the single-row `workspaces` table; these are not
the measured filter paths. No duplicate index was removed because there is no
production usage history supporting a safe removal decision.

## Migration lock-window fixture

A separate disposable database was migrated to `cv3_00000002` and populated
with 10,000 documents plus 10,000 versions, chunks, jobs, and conversations.
The `cv3_00000003` upgrade completed in 1.44 seconds wall time (0.40 seconds
user, 0.06 seconds system) under its 5-second lock timeout. All five 10,000-row
counts were preserved, every document was normalized to `indexed` with its
sole completed version activated, and the strict verifier returned `PASS`
with all 13 invariant counts at zero.

This bounded synthetic result validates the migration strategy and exposes
the rewrite cost; it is not a production cardinality estimate. A production
run still requires the runbook's backup, target identity, cardinality, lock,
and maintenance-window admission checks.
