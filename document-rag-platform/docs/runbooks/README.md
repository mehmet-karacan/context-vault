# Runbook Index

| Runbook | Owner | Last verified | Evidence/scope |
|---|---|---|---|
| [A11 backup and restore](a11-backup-restore.md) | Mehmet KARACAN | 2026-09-06 | hardened backup, fresh-target restore and scheduler evidence |
| [A11 observability and SLO](a11-observability-slo.md) | Mehmet KARACAN | 2026-09-06 | trace, metric, redaction and SLO rehearsal |
| [A11 operations index](a11-operations-index.md) | Mehmet KARACAN | 2026-09-06 | ownership and operational drill matrix |
| [A11 release candidate](a11-release-candidate.md) | Mehmet KARACAN | 2026-09-06 | fail-closed local and external release gates |
| [Backup / restore](backup-restore.md) | Mehmet KARACAN | 2026-09-02 | synthetic restore drill receipt |
| [Degraded readiness](degraded-readiness.md) | Mehmet KARACAN | 2026-09-06 | dependency failure and 503 recovery drill |
| [Dense index repair](dense-index-repair.md) | Mehmet KARACAN | 2026-09-02 | legacy repair procedure; no live execution claim |
| [Embedding model change](embedding-model-change.md) | Mehmet KARACAN | 2026-09-02 | profile/reindex procedure |
| [Installation and first boot](installation-first-boot.md) | Mehmet KARACAN | 2026-09-06 | locked install, migration and readiness sequence |
| [Migration lineage recovery](migration-lineage-recovery.md) | Mehmet KARACAN | 2026-09-06 | v7 head, sentinel-preserving upgrade and downgrade path |
| [Object GC quarantine](object-gc-quarantine.md) | Mehmet KARACAN | 2026-09-06 | quarantine, retention and citation-hold drill |
| [OCR models](ocr-models.md) | Mehmet KARACAN | 2026-09-02 | provider/routing configuration |
| [Provider CA and key rotation](provider-ca-key-rotation.md) | Mehmet KARACAN | 2026-09-06 | fail-closed trust and rotation procedure |
| [Reindex profile change](reindex-profile-change.md) | Mehmet KARACAN | 2026-09-06 | profile-safe rebuild, activation and rollback drill |
| [Re-index](reindex.md) | Mehmet KARACAN | 2026-09-02 | reindex service workflow |
| [Release rollback](release-rollback.md) | Mehmet KARACAN | 2026-09-06 | exact image/source rollback and verification gates |
| [Repository ingestion incident](repository-ingestion-incident.md) | Mehmet KARACAN | 2026-09-06 | quarantine, scope and credential response drill |
| [Repository scan limits](repository-scan-limits.md) | Mehmet KARACAN | 2026-09-02 | repository security policy |
| [Scoped retrieval](retrieval.md) | Mehmet KARACAN | 2026-09-02 | typed scope, plans, benchmark, incident diagnosis |
| [Security incident](security-incident.md) | Mehmet KARACAN | 2026-09-06 | containment, key/CA rotation and evidence handling |
| [Stuck job, outbox and lease](stuck-job-outbox-lease.md) | Mehmet KARACAN | 2026-09-06 | stale lease reconciliation and retry drill |
| [Structured answers](structured-answers.md) | Mehmet KARACAN | 2026-09-02 | schema gate, claim citation, provider failure/retention |
| [Web product contract](web-product-contract.md) | Mehmet KARACAN | 2026-09-02 | generated schema, scoped auth/browser and fresh-connection durability |
| [Upload and ingestion jobs](upload-and-ingestion-jobs.md) | Mehmet KARACAN | 2026-09-02 | job API and worker workflow |
| [Migration/recovery](../../services/backend/MIGRATION_RUNBOOK.md) | Mehmet KARACAN | 2026-09-02 | V3 lineage receipts |

The inventory is completeness-checked by `scripts/generate_verified_status.py`.
Operational commands must still be verified against the exact candidate SHA
before production use.

Historical, non-runbook operational records live under `../operations/` and are
not included in the runbook completeness set.
