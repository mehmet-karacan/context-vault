# Runbook Index

| Runbook | Owner | Last verified | Evidence/scope |
|---|---|---|---|
| [Backup / restore](backup-restore.md) | Mehmet KARACAN | 2026-09-02 | synthetic restore drill receipt |
| [Dense index repair](dense-index-repair.md) | Mehmet KARACAN | 2026-09-02 | legacy repair procedure; no live execution claim |
| [Embedding model change](embedding-model-change.md) | Mehmet KARACAN | 2026-09-02 | profile/reindex procedure |
| [OCR models](ocr-models.md) | Mehmet KARACAN | 2026-09-02 | provider/routing configuration |
| [Re-index](reindex.md) | Mehmet KARACAN | 2026-09-02 | reindex service workflow |
| [Repository scan limits](repository-scan-limits.md) | Mehmet KARACAN | 2026-09-02 | repository security policy |
| [Scoped retrieval](retrieval.md) | Mehmet KARACAN | 2026-09-02 | typed scope, plans, benchmark, incident diagnosis |
| [Structured answers](structured-answers.md) | Mehmet KARACAN | 2026-09-02 | schema gate, claim citation, provider failure/retention |
| [Upload and ingestion jobs](upload-and-ingestion-jobs.md) | Mehmet KARACAN | 2026-09-02 | job API and worker workflow |
| [Migration/recovery](../../services/backend/MIGRATION_RUNBOOK.md) | Mehmet KARACAN | 2026-09-02 | V3 lineage receipts |

The inventory is completeness-checked by `scripts/generate_verified_status.py`.
Operational commands must still be verified against the exact candidate SHA
before production use.

Historical, non-runbook operational records live under `../operations/` and are
not included in the runbook completeness set.
