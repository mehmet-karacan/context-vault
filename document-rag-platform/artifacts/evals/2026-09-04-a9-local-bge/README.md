# A9 local BGE runtime admission evidence

Scope: exact local `BAAI/bge-m3` snapshot admission and real embedding smoke
only. This directory is public-safe and contains no model weights, absolute
model path, credential, private benchmark data or raw prompt/provider payload.

The admitted snapshot is revision
`5617a9f61b028005a4858fdac845db406aefb181`, 11 resolved files,
2,293,331,623 bytes, bundle SHA-256
`d87c47601ade6251c7e0c236d4b863b797bfd280f94ac09be9cc7fbeede74668`.

Final evidence:

- `LOCAL_BGE_CPU.json`: real locked-runtime CPU inference.
- `LOCAL_BGE_MPS.json`: real locked-runtime Apple MPS inference.
- `TIER_JUNIT.xml`: 140 local-BGE and A9 contract tests.
- `BACKEND_JUNIT.xml`: 756 backend tests passed, 2 skipped.
- `PYTHON_AUDIT.json`: opt-in local-eval environment, zero known vulnerabilities.
- `PYTHON_SBOM.cdx.json`: reproducible CycloneDX environment inventory.
- `PYTHON_LICENSES.json`: installed Python license inventory.
- `PUBLIC_TREE.json`: 273 tracked source files checked, PASS.
- `REPRODUCTION_SUMMARY.json`: test-first failures and the fresh-DB recovery.
- `SOURCE_MANIFEST.json`: exact tested source hashes.
- `INDEPENDENT_REVIEW.json`: separate subagent approval for this bounded
  runtime-admission package, including the prior rejection findings.
- `PRECOMMIT_RECEIPT.json`: commands, results, data-safety statement and exact
  evidence bindings before the implementation commit.
- `SHA256SUMS`: integrity inventory for the evidence files.

Both runtime reports use Hugging Face/Transformers offline guards and
`local_files_only=True`. They intentionally state
`network_isolation_verified=false`: these library controls are not an OS-level
network sandbox.

The initial full-suite run selected an older isolated test database at
`cv3_00000003`; the resulting 21 schema failures were retained in the summary.
That database was not migrated or reset. A new empty
`cv3_a9_local_bge_verify` database was upgraded to `cv3_00000006`, after which
the exact-source full suite passed.

This evidence does not approve or run a generation model. BGE-M3 alone cannot
satisfy the real-provider benchmark or close A9.
