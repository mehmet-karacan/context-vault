# A9 trusted-local production-core rehearsal

Status: `PARTIAL_PASS / A9_OPEN`.

This evidence is public-safe. It contains no document bytes, query text, prompt,
model output, credential, raw transcript, or private label content. The external
one-record pack was explicitly an unapproved rehearsal fixture, not the
owner-reviewed private pack required by A9.

## Verified

- Exact offline snapshots were used: BGE-M3
  `5617a9f61b028005a4858fdac845db406aefb181` and Qwen2.5-1.5B-Instruct
  `989aa7980e4cf806f80c7fef2b1adb7bc71aa306`.
- Every rehearsal used a newly created `cv3_eval_*` PostgreSQL database at
  Alembic `cv3_00000006` and a previously absent `cv3-eval-*` MinIO bucket.
- Production `IngestionOrchestrator`, retrieval, answer, and persistence paths
  were invoked. Run `02` reached two persisted messages; all four runs reached a
  completed ingestion job and a retrieval run.
- Each retained bucket has three objects and every object has the Context Vault
  AES-GCM envelope marker. No rehearsal DB or bucket was reset or deleted.
- The runner now binds the concrete executor and actual production embedding
  profile, prompt, and pipeline hashes. It restricts infrastructure endpoints
  to loopback and fails closed for golden source constraints it cannot measure.
- Production state identities are derived from the execution-only projection;
  changing golden-label bytes cannot affect provider requests or runtime state.
  Source closure rejects non-bytecode payloads under `__pycache__`, and an
  adversarial label without measurable forbidden sources fails closed instead
  of reporting a false zero prompt-injection rate.
- Focused A9 contracts passed `279/279`; the full backend suite on dedicated
  fresh `cv3_eval_fullsuite_20260905_02` PostgreSQL/Redis/MinIO targets passed
  `950`, skipped `2`, and emitted
  one known Starlette warning. Ruff and `git diff --check` passed.
- MyPy's 14-source ratchet, deterministic OpenAPI generation, the 163-package
  offline lock check, and local dependency audit (no known vulnerabilities)
  passed. `SOURCE_MANIFEST.json` binds the exact source revision, runner bundle,
  and every relevant source/test byte; its SHA-256 is
  `4b17461b52ffd4294c141b2992bdf312c789ccf022bbae46f5073e4209fb3b53`.

## Not verified / open gates

- No run is an approved real-provider baseline. Run `02` completed the production
  case but the post-request label gate correctly rejected a non-semantic
  rehearsal dataset version. The fixture was corrected and independently
  admitted before later runs.
- The 64-token Qwen/Transformers MPS profile exceeded its 900-second run budget
  in runs `03` and `04`, including after `torch.inference_mode()` and KV-cache
  hardening. Neither run produced a report and neither is counted as success.
- The approved private pack, independent request/golden non-transfer evidence,
  adversarial permission fixture coverage, and first human baseline seal remain
  required. A9 is open and A11 was not started.

See `runtime-summary.json` for bounded machine-readable counts.
