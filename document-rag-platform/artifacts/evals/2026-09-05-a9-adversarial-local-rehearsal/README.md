# A9 adversarial local production rehearsal

Status: `SAFETY PASS / QUALITY PARTIAL / A9 OPEN`.

This public-safe evidence contains no document bytes, query text, prompt, model
output, credential, raw transcript, or private golden-label content. The six-case
external fixture is synthetic and explicitly unapproved. It is not the
owner-reviewed private benchmark pack and cannot become an approved baseline.

## What changed and was exercised

- The execution contract now admits only the six supported personas, declares
  `case` versus `project` document scope, and expresses an ordered revision chain
  through `revises_document_id`.
- Project scope removes the case-document whitelist so the production
  project/workspace predicates are exercised against pre-existing foreign data.
- A second source can be ingested as a real new version of the same Document.
  Runtime evidence shows version 1 `superseded` and inactive, version 2 `ready`
  and active.
- Source identity is version-aware. Prior active out-of-case sources remain
  observable without being mislabeled as version leakage. Old-version or foreign
  citations become opaque invalid citations and critical findings instead of
  disappearing before scoring.
- A strict independent golden non-transfer receipt schema/checker now binds the
  exact source, runner, private bundle, label/execution files, normalized execution
  projection, report, environment, models, request count, and independent evidence.
  The checker validates but never creates reviewer authority and always leaves
  `release_gate_eligible=false`.

## Runtime result

Run `11` used exact offline BGE-M3 and Qwen2.5-1.5B-Instruct snapshots, fresh
PostgreSQL database `cv3_eval_a9_local_20260905_11` at Alembic
`cv3_00000006`, an absent-before-run MinIO bucket, and loopback Redis health.
All 24 retained objects passed the production AES-GCM envelope check.

The six cases covered cross-project seed, cross-workspace seed, reader target,
same-document temporal versioning, prompt/citation boundary, and no-answer.
All absolute safety counters were zero. The reader target produced a safe
`malformed_response` no-answer, so aggregate Recall/MRR/citation/sufficiency was
`0.833333` and answerability false-negative rate was `0.166667`. This is a real
quality miss and is not presented as an approved baseline pass.

The report field `golden_results_sent_to_provider=false` is still only the
runner's assertion. No independent request capture receipt or human baseline seal
was issued in this work.

## Verification

- Post-commit focused contracts: `317 passed`.
- Full backend on fresh `fullsuite05`: `1038 passed, 2 skipped`, one known
  Starlette warning.
- A preceding `fullsuite04` diagnostic retained `1036 passed, 2 failed, 2 skipped`:
  the supplied SQLAlchemy DSN was not accepted by two direct psycopg2 tests. The
  database was retained, not reused, and the correct fresh run passed.
- MyPy ratchet (14 sources), deterministic OpenAPI, 163-package offline lock,
  dependency audit, Ruff, format, py_compile, and diff-check passed.
- Independent source review returned `APPROVE`, with no P0/P1/P2 findings.

## Remaining gates

A9 still needs the owner's real private-pack review and exact approval, a
separately issued independent provider-request/golden non-transfer receipt, and
human review/sealing of the first approved benchmark. This rehearsal does not
close A9 and A11 was not started.
