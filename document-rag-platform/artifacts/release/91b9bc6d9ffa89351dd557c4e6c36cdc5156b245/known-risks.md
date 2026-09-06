# Known risks and external release gates

Candidate SHA: `91b9bc6d9ffa89351dd557c4e6c36cdc5156b245`

No open local P0/P1 implementation or data-integrity finding remained after the
independent clean verification. The following release blockers are intentionally
open and were not represented as completed:

- The candidate branch has not been pushed; authenticated GitHub inspection found
  no exact-SHA Actions runs.
- GitHub returned no repository ruleset and reported `main` as unprotected, so PR,
  required-check and direct-push enforcement is not proved.
- The locally verified image has no GitHub OIDC/Cosign signature.
- The exact current real-benchmark runner/source bundle invalidates the older
  approval binding. A fresh human-issued manifest and final release seal are still
  required; the CLI did not bypass or manufacture them.
- The public repository has no selected license. No license grant or proprietary
  policy was invented on the owner's behalf.
- Production alert ownership/escalation routing still needs an external deployment
  receipt.
- No remote push, PR, merge, tag or release was performed.

The historical real benchmark is retained as historical evidence only. Local
BGE-M3/Qwen execution proves the intended offline model snapshots work on this
MacBook; it does not transfer the old approval to the new source closure.
