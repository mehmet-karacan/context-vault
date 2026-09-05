# A9 phased local production rehearsal

Status: `LOCAL_REHEARSAL_PASS / A9_OPEN`.

This evidence is public-safe. It contains no document bytes, query text, prompt,
model output, credential, raw transcript, or private golden-label content. The
one-record external pack is an explicitly unapproved rehearsal fixture. It is
not the owner-reviewed private benchmark pack required by A9.

## Verified

- Source commit `6dbbfbbe5939f710faad3aa06258fe1dc32d2d89`
  introduced phased model residency: BGE-M3 performs ingestion/retrieval, is
  closed, and Qwen2.5-1.5B-Instruct is loaded lazily for structured generation.
- The prompt boundary uses exact `<KANITLAR>` delimiters. Query, conversation
  history, document metadata, paths, headings, and evidence content use a
  deterministic JSON-string encoding before entering the model-facing prompt.
  Generated labels remain the only unescaped citation labels.
- The structured schema, application repair, and provider-internal repair use a
  shared serialization contract. The evidence budget includes the worst-case
  structured/repair envelope. Invalid provider output is not copied into repair
  requests.
- Run `10` used exact offline BGE and Qwen snapshots, a fresh PostgreSQL database
  at Alembic `cv3_00000006`, a previously absent MinIO bucket, and loopback-only
  Redis health. It exited successfully inside the enforced 900-second limit.
- Run `10` produced retrieval, citation, and answer-sufficiency metrics of `1.0`;
  answerability false-positive/false-negative rates, leakage, invalid labels,
  unsupported claims, retries, duplicates, orphans, and critical/high findings
  were all `0`.
- Production persistence contains one project, one document, one completed
  ingestion job, one retrieval run, two messages, one claim, one message
  citation, and one claim-citation link. All three retained MinIO objects have
  the Context Vault AES-GCM envelope marker.
- Focused contracts passed `337/337`. The full backend suite on separate fresh
  `cv3_eval_fullsuite_20260905_03` PostgreSQL/Redis/MinIO targets passed `992`,
  skipped `2`, and emitted one known Starlette deprecation warning.
- MyPy's 14-source ratchet, deterministic OpenAPI, the 163-package offline lock,
  local dependency audit, Ruff, and `git diff --check` passed.
- Independent post-commit review returned `APPROVE` with no P0/P1/P2 findings.
  It verified commit tree `bcc9b651916e6bd853e1992a0a6882d40e34e9da`,
  the 109-member runner source closure, and runner bundle
  `abeaaf8a3d1901318139f41ec215c362470e60336ba3b639056e93fc7d133858`.

## Retained diagnostic runs

- Runs `05`, `06`, and `07` proved the duration rescue but failed answerability
  and citation quality because the model used a filename instead of a generated
  source label. Their reports and isolated DB/buckets were retained.
- Run `08` was stopped before application writes when independent review found
  prompt-boundary vulnerabilities. Its empty migrated DB was retained and its
  bucket was never created.
- Run `09` failed before application import because the direct diagnostic command
  selected the unavailable `psycopg` driver instead of installed `psycopg2`.
  Its empty migrated DB was retained and its bucket was never created.
- No rehearsal database or bucket was deleted, reset, or reused.

## Open gates

- A9 still requires an owner-reviewed private pack and exact approval manifest.
- The approved pack must include adversarial permission/version/cross-scope
  coverage and an independent request/golden non-transfer check.
- The first human baseline review and seal remain required.
- This rehearsal does not close A9 and does not authorize A11.

See `runtime-summary.json`, `provider-report.json`, `SOURCE_MANIFEST.json`, and
`INDEPENDENT_REVIEW.json` for bounded machine-readable evidence.
