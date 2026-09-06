# A9 grounding-repair local production rehearsal

Status: `SAFETY PASS / QUALITY PARTIAL / A9 OPEN`.

This public-safe evidence contains no document bytes, query text, prompt, model
output, credential, raw transcript, or private golden-label content. The reused
six-case external fixture remains synthetic and explicitly unapproved. It is not
the owner-reviewed private benchmark pack and cannot become an approved baseline.

## Change and regression evidence

The application repair prompt now states the dynamic grounding invariants that
JSON Schema cannot express: answer/no-answer shape, claim text containment,
allowed non-empty claim labels, and exact first-seen `used_source_labels` order.
It does not echo the rejected provider payload and does not add another retry.

Regression coverage composes the real LocalQwen test double with the application
boundary: schema-valid but grounding-invalid output, application repair,
malformed JSON, internal LocalQwen repair, and a valid grounded result. It proves
the bounded `generation_calls=2` / `repair_calls=1` chain and non-transfer of both
rejected outputs. Independent code review returned `APPROVE`, P0/P1/P2 `0`.

## Runtime result

The first `run12` command used a wrong temporal query-type spelling and failed
closed during pack admission, before model/provider construction. Its fresh DB
and bucket contain zero domain rows/objects and were retained without reuse.

`run13` used source revision `ea843a1`, exact offline BGE-M3 and
Qwen2.5-1.5B-Instruct snapshots, fresh PostgreSQL database
`cv3_eval_a9_local_20260905_13` at Alembic `cv3_00000006`, a separate
absent-before-run MinIO bucket, and loopback Redis health. All 24 retained objects
passed the production AES-GCM envelope check.

All absolute safety counters were zero. The same reader/cross-scope case still
produced a safe `malformed_response` no-answer, so aggregate Recall/MRR/citation/
sufficiency remained `0.833333` and answerability false-negative rate remained
`0.166667`. Unsupported-claim rate improved from run11 `0.166667` to `0.0`.
This is a measured partial improvement, not a quality pass.

The report field `golden_results_sent_to_provider=false` remains only the
runner's assertion. No independent request-capture receipt or human baseline
seal was issued.

## Verification

- Focused answer/provider contracts: `69 passed`.
- Full backend on fresh `fullsuite07`: `1041 passed, 2 skipped`, one known
  Starlette warning.
- MyPy ratchet: 14 strict sources.
- Deterministic OpenAPI, 163-package offline lock, dependency audit, Ruff,
  format, py_compile, and diff-check passed.
- Independent code review: `APPROVE`, no P0/P1/P2.

## Remaining gates

A9 still needs the owner's actual private-pack review and exact approval, a
separately issued independent provider-request/golden non-transfer receipt, and
human review/sealing of the first approved benchmark. This rehearsal does not
close A9 and A11 was not started.
