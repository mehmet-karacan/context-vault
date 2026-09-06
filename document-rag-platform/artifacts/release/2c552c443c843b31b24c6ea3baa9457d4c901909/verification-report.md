# Context Vault A13 exact-candidate verification

Candidate SHA: `2c552c443c843b31b24c6ea3baa9457d4c901909`

Decision: **machine and independent implementation gates PASS; merge/release
remains NO-GO for three human/external gates.**

The exact candidate produced five successful push workflows and five successful
pull-request workflows. All 15 candidate check runs succeeded. Authenticated
CodeQL analyses reported zero Python and zero JavaScript/TypeScript results. The
backend workflow verified blank and restored-schema migrations at
`cv3_00000007`, 1512 collected unit tests with zero failures, 34 collected
integration tests with zero failures, and 84% combined coverage. Frontend lint,
typecheck, 26 unit tests, production build/image and 20 browser tests passed.
The offline production-pipeline RAG suite passed 55 tests with zero scope leakage
and zero invalid citations.

Ruleset `22369487` protects `main` with PR, strict seven-check, conversation,
non-fast-forward and deletion protections. A real direct-push probe was rejected
with GH013 while `main` remained unchanged. The authenticated status generator
bound the candidate to exact run IDs and ruleset state and returned
`verified=true`.

The supply-chain run checked 742 tracked files and 683 dependency-license records,
found no public-tree issue and no Python/Node or HIGH/CRITICAL image vulnerability,
and produced an exact image SBOM, provenance receipt and keyless signature bundle.
CodeQL path-injection results are zero; dismissed findings were not counted as a
clean result.

An independent adversarial audit first found that the aggregate release verifier
did not bind the no-transfer receipt to the exact `run_eval.json` bytes. Candidate
`2c552c4` closes that fail-open by requiring the exact checker revision, valid
lowercase SHA-256 fields and an exact byte-hash match. Missing bindings, mismatched
hashes and non-hex hashes are now rejected; the independent related suite passed
301 tests and the local focused suite passed 272 tests.

No existing user database, MinIO object, model cache or project file was deleted
or reset. The remaining blocks are the fresh exact-candidate human benchmark
artifacts, a real production alert delivery/acknowledgement receipt, and the
repository owner's license/no-license choice. They are recorded as open rather
than represented as automated approvals.
