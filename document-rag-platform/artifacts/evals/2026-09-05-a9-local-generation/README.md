# A9 local generation runtime evidence

Scope: exact MacBook-local generation runtime admission only. The accepted
snapshot is `Qwen/Qwen2.5-1.5B-Instruct` at revision
`989aa7980e4cf806f80c7fef2b1adb7bc71aa306`, with 10 resolved files,
3,098,973,447 bytes and bundle SHA-256
`5a6a6259762fed70e38ae346763b105a7b05aa981e5ab078c8e5b033f87f0c87`.
Model weights are not in this evidence directory or Git.

Final evidence:

- `LOCAL_GENERATION_MPS.json`: real MPS load and three bounded deterministic
  generations; exact grounded/no-answer output hashes only, no generation text.
- `TIER_JUNIT.xml`: 247 eval contract tests passed.
- `BACKEND_JUNIT.xml`: 829 backend tests passed, 2 skipped, 1 known warning.
- `INDEPENDENT_REVIEW.json`: three rejection rounds and final bounded technical
  approval by a separate subagent.
- `REPRODUCTION_SUMMARY.json`: test-first failures, rejected candidates, final
  resource envelope and scope limits.
- `SOURCE_MANIFEST.json`: exact tested source hashes.
- `PUBLIC_TREE.json`: staged public-tree safety result.
- `PRECOMMIT_RECEIPT.json`: commands, results and exact pre-commit bindings.
- `SHA256SUMS`: evidence integrity inventory.

The smaller 0.5B candidate and the first natural-language 1.5B semantic smoke
were rejected. The accepted opaque-token/no-context case proves a controlled
prompt-adherence and runtime-integrity contract, not broad grounding, Turkish
quality, citation quality or release eligibility. Library offline mode is not
OS network isolation; the report correctly records
`network_isolation_verified=false`.

Cached owner/name/revision and bundle hash establish local byte identity only;
they do not cryptographically prove publisher origin or license provenance.
The worker pipe is a trusted-local guard against accidental/direct CLI bypass,
not authentication against another process owned by the same OS user.

This package has no owner-reviewed private pack, human approval, real
production-path benchmark, independent request/non-transfer evidence or human
baseline seal. It does not close A9 and does not authorize A11.
