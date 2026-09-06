# A9 local production-runner admission evidence

Scope: local benchmark runner input, source-closure and runtime-identity
hardening only. `scripts/run_eval.py` remains a no-effect admission wrapper
unless a separate exact human approval manifest and private-pack manifest are
provided.

Final evidence:

- `TIER_JUNIT.xml`: 188 runner/eval contract tests passed.
- `BACKEND_JUNIT.xml`: 859 backend tests passed, 2 skipped, 1 known warning.
- `INDEPENDENT_REVIEW.json`: five concrete rejection rounds and final bounded
  technical approval by a separate subagent.
- `REPRODUCTION_SUMMARY.json`: test-first failures, repairs, final verification
  and scope limits.
- `SOURCE_MANIFEST.json`: exact tested source and lock hashes.
- `PUBLIC_TREE.json`: staged public-tree safety result.
- `PRECOMMIT_RECEIPT.json`: commands, results and pre-commit source bindings.
- `SHA256SUMS`: evidence integrity inventory.

The accepted contract binds validated private-pack metadata and the runner
bundle into reserved child inputs. An optional `<runner>.sources.json` binds
repo-local Python roots and fixed files. Path escapes, symlinks, bytecode/native
import artifacts, execution-control credential names, ambient PATH drift and a
retargeted runner-selected Python interpreter fail closed. Runner, manifest and
environment identities are checked before and after execution.

The boundary is a trusted local operator model. It does not claim protection
against a malicious same-user process racing filesystem state. No private pack,
credential value, provider request, user data or model weight is present here.

This package is not owner review, human benchmark approval, a production-path
benchmark, request/non-transfer evidence or a human baseline seal. It does not
close A9 and does not authorize A11.
