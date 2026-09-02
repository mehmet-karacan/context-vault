# Architecture Decision Record Index

Owner alanı tüm kararlar için Mehmet KARACAN'dır. `Supersedes: none`, kararın
önceki bir ADR'yi geçersiz kılmadığını; evidence alanı ise kararı destekleyen
kod, test veya receipt kaynağını gösterir.

| ADR | Status | Owner | Supersedes | Evidence |
|---|---|---|---|---|
| [ADR-001](ADR-001-canonical-application-root.md) | Accepted | Mehmet KARACAN | none | root `README.md`, tree inventory |
| [ADR-002](ADR-002-normalized-content-model.md) | Accepted | Mehmet KARACAN | none | normalized-content tests |
| [ADR-003](ADR-003-versioned-ingestion-immutable-artifacts.md) | Accepted | Mehmet KARACAN | none | ingestion tests, V3 migration |
| [ADR-004](ADR-004-hybrid-retrieval-rrf.md) | Accepted | Mehmet KARACAN | none | retrieval unit tests |
| [ADR-005](ADR-005-repository-scan-security-model.md) | Accepted | Mehmet KARACAN | none | repository security tests |
| [ADR-006](ADR-006-ocr-provider-strategy.md) | Accepted | Mehmet KARACAN | none | OCR routing tests |
| [ADR-007](ADR-007-migration-lineage-recovery-or-reset.md) | Accepted | Mehmet KARACAN | legacy `b2f1c0a10001..5` operations | migration receipts |
| [ADR-008](ADR-008-schema-time-scope-and-retention.md) | Accepted | Mehmet KARACAN | none | `cv3_00000003`, invariant and query-plan tests |
| [ADR-009](ADR-009-durable-ingestion-content-policy-and-gc.md) | Accepted | Mehmet KARACAN | ADR-003 reindex section | `cv3_00000004`, A6 integration/fault tests |
| [ADR-010](ADR-010-typed-scoped-retrieval-context-bundle.md) | Accepted | Mehmet KARACAN | ADR-004 untyped/legacy-vector transition | `cv3_00000005`, A7 retrieval receipt |

Last inventory verification: 2026-09-02. `scripts/generate_verified_status.py`
checks that every `ADR-*.md` file is represented here.
