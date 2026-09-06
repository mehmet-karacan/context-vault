# A11 Operational Metrics and Provisional SLO Rehearsal

Last verified base SHA: `b6d368c4ba75afee5630251e5c0f3649d1c9c920`

Last verified date: `2026-09-06`

Verification scope: local synthetic inputs and unit tests only. The implementation
was uncommitted when this runbook was written, so the SHA above is a base binding,
not release evidence. No production baseline, owner approval, on-call assignment,
or remote alert delivery is claimed.

## Prerequisites

- Python 3.12 and the backend lockfile environment are installed.
- The policy remains
  `infra/a11-slo-provisional.json` with authority
  `provisional_local_only`.
- Backup and restore ages may be observed only from successful, release-eligible,
  non-mutating receipts. Input receipt paths are never copied to output.
- Run the command from the repository root. Choose a new output path; the tool
  refuses overwrite and symlink targets.

## Runtime metric sources

| Metric | Bounded source and behavior |
|---|---|
| `queue.depth` | Redis `LLEN celery`; non-negative integer only. |
| `queue.oldest_age` | Oldest Redis message's producer-added integer UTC epoch. Legacy/malformed/oversized messages leave age absent rather than fabricating zero. Message body is never retained. |
| `outbox.backlog` | Exact count of every non-published PostgreSQL outbox row. |
| `outbox.oldest_age` | Age of the minimum `created_at` among non-published outbox rows. |
| `provider.tokens` | Provider response aggregate `usage.total_tokens`, bounded to one billion per response. |
| `provider.cost_micro_usd` | Provider-supplied aggregate response cost in micro-USD. A zero-valued field cannot hide a later non-zero provider aggregate; no local pricing estimate is invented. |
| `db.query` | Process-local SQLAlchemy execution duration without statement, bind, table, or identifier labels. |
| `db.slow_queries` | Count of queries at or above the provisional 500 ms instrumentation threshold. |
| `db.pool.checked_out` | Current process pool count; no connection details. |
| `db.index.scans_total` / `db.index.unused_count` | Bounded aggregate `pg_stat_user_indexes` observation; relation names are never retained. |
| `backup.age_seconds` | UTC age projected from an eligible `backup-complete` receipt. |
| `restore_drill.age_seconds` | UTC age projected from an eligible `fresh-target-restore-drill` receipt. |

Durations include bounded local p95 and are emitted as StatsD timers. Backend
and worker use label-free `context_vault.backend.*` and
`context_vault.worker.*` names. Local/test defaults disable the sink. Staging
and production boot fails unless `METRICS_EXPORT_MODE=statsd` plus a bounded
`METRICS_STATSD_HOST` and `METRICS_STATSD_PORT` are configured.
Gauge value, `<metric>.known`, and `<metric>.observed_at_epoch` are emitted in
one UDP datagram. Alerts evaluate a value only while `known=1` and the source
timestamp is within its expected collection interval plus one grace interval;
otherwise they alert as unknown. Minimum TTLs: readiness/queue/outbox/DB 120 seconds,
lease/backup/restore 180 seconds, and the 15-minute capped orphan inventory 1200
seconds. The collector must not refresh persisted gauges without a new source
datagram and must expire/delete gauges after these TTLs.

No prompt, response, document content,
credential, token value, raw path, task argument, SQL statement, or high-cardinality
identifier is recorded.

## Dry-run / local alert-delivery rehearsal

The example deliberately breaches provisional API latency so the local-only alert
path is exercised:

```bash
python3 scripts/slo_rehearsal.py \
  --policy document-rag-platform/infra/a11-slo-provisional.json \
  --canonical-policy document-rag-platform/infra/release/slo-policy.json \
  --samples document-rag-platform/infra/a11-slo-synthetic-samples.example.json \
  --expect-alert \
  --json-output /tmp/context-vault-slo-rehearsal.json
```

The synthetic contract also rehearses the remaining A11 objectives: ingestion
success/duration, outbox age, backup freshness, restore-drill success/age,
permission-leakage and invalid-citation absolute-zero counters, and benchmark
regression. Those values are explicitly synthetic and are not a production
baseline.

The provisional policy embeds the canonical release-policy SHA-256. The tool
stable-reads both files and fails closed if the hash, SLO names, comparators, or
mapped thresholds drift.

To add real backup/restore-age observations without making a production SLO
claim, append:

```bash
  --backup-receipt /approved/private-or-local/BACKUP_RECEIPT.json \
  --restore-receipt /approved/private-or-local/RESTORE_RECEIPT.json
```

The receipt contains only the two source SHA-256 bindings and derived ages; it
does not contain either input path or raw receipt content.

## Expected result

- Exit code `0`.
- New mode-`0600` JSON receipt with schema
  `context-vault-slo-rehearsal-receipt/v1`.
- `classification=synthetic`, `authority=provisional_local_only`,
  `production_baseline=false`, `production_owner_approval=false`.
- `delivery.mode=local_receipt_only` and `remote_delivery=false`.
- With the example, `evaluation.state=ALERT` and at least one bounded metric alert.

## Stop conditions

Stop and do not interpret output as evidence when:

- an input is a symlink, non-regular, changes during read, exceeds 1 MiB, has
  duplicate JSON keys, or violates the exact schema;
- sample classification is not exactly `synthetic`;
- policy authority is not exactly `provisional_local_only`;
- a metric is non-finite, negative, out of range, missing, or contains an
  unexpected field;
- a backup/restore receipt is failed, mutating, retains credentials/raw object
  names, lacks fresh-target proof, or is not release-gate eligible;
- `--expect-alert` is used and no alert fires;
- the output already exists or is unsafe.

Failures emit only `SLO rehearsal failed closed`; no input value or path is
logged.

## Rollback

The rehearsal reads inputs and writes only the exclusive receipt path. Remove the
local receipt if it is no longer needed. Runtime instrumentation has no schema or
user-data mutation; roll it back through the normal reviewed Git revert for the
exact implementation commit.

## Evidence

For release evidence, rerun on a clean committed SHA and retain:

- exact code SHA;
- policy and sample SHA-256 bindings;
- the mode-`0600` rehearsal receipt;
- focused test, lint, and format results;
- separately authorized production baseline, owner/on-call and real alert-channel
  delivery evidence. The local receipt cannot substitute for those external
  gates.
