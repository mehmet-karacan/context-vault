# A9 split provider/model contract evidence

Scope: versioned A9 identity contract only. The runtime now binds embedding and
generation provider/model identities independently across human approval,
provider report, child environment and sealed baseline comparison.

Final evidence:

- `TIER_JUNIT.xml`: 158 split-contract and A9 tier tests, all passing.
- `BACKEND_JUNIT.xml`: 787 backend tests passed, 2 skipped.
- `INDEPENDENT_REVIEW.json`: separate subagent review, including the initial
  rejection findings and final bounded approval.
- `REPRODUCTION_SUMMARY.json`: test-first failures, infrastructure correction
  and scope limitations.
- `SOURCE_MANIFEST.json`: exact tested source hashes.
- `PUBLIC_TREE.json`: public-tree safety result after staging the full package.
- `PRECOMMIT_RECEIPT.json`: commands, results, safety statement and exact
  evidence bindings before the implementation commit.
- `SHA256SUMS`: evidence integrity inventory.

The generic v1 schema paths remain byte-identical so prior evidence manifests
remain verifiable. Explicit `-v1` copies are historical only; `run_eval.py`
loads only the `-v2` approval and baseline-seal schemas. V1 and mixed v1/v2
approval, provider-report, baseline and current-candidate inputs fail closed.

This package contains no private benchmark records, model weights, credentials,
raw provider payload or human approval. It does not close A9.
