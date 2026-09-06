# A11 Operations Runbook Index

Last verified code SHA: `7d36fc74143d7504113ba6ac3adeaf9d736537bc`
Last verified date: `2026-09-06`
Verification scope: the doctor ran against a disposable eight-service Compose
project at the code SHA above. The fully disposable PostgreSQL/MinIO
backup→restore drill ran at exact source SHA
`bde49aca525f7ccc40b21c039af003413621223c`. No production or user-data mutation
is claimed.

The automatic-backup scheduler and executable runbook commands were tested on
committed base `b6d368c4ba75afee5630251e5c0f3649d1c9c920`; their exact uncommitted
source hashes, dry-run receipt and disposable cleanup receipt are in
[`2026-09-06-a11-runbook-drills`](../../artifacts/operations/2026-09-06-a11-runbook-drills/README.md).
The scheduler was run only with `--dry-run`; production scheduling and alert
delivery are not claimed.

| Required §18.7 subject | Runbook |
|---|---|
| Installation / first boot | [installation-first-boot](installation-first-boot.md) |
| Migration / recovery / lineage reset | [migration-lineage-recovery](migration-lineage-recovery.md) |
| Backup / restore / reconciliation | [a11-backup-restore](a11-backup-restore.md) |
| Reindex / embedding profile | [reindex-profile-change](reindex-profile-change.md) |
| Provider / CA / key rotation | [provider-ca-key-rotation](provider-ca-key-rotation.md) |
| Stuck job / outbox / lease | [stuck-job-outbox-lease](stuck-job-outbox-lease.md) |
| Object GC / quarantine | [object-gc-quarantine](object-gc-quarantine.md) |
| Repository ingestion / SSRF | [repository-ingestion-incident](repository-ingestion-incident.md) |
| Degraded readiness / provider outage | [degraded-readiness](degraded-readiness.md) |
| Security incident / public secret | [security-incident](security-incident.md) |
| Release / rollback | [release-rollback](release-rollback.md) |

Every runbook below uses the same invariant: inspect → dry-run → approved effect →
verify → receipt. A command that does not match the recorded source revision, an
unexpected target, missing backup, or failed dry-run is a stop condition.
