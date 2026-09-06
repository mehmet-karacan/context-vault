# A2 local closeout evidence

This package records executed local checks for the A2 runtime, dependency and
supply-chain source contract. It is deliberately `PASS_LOCAL_ONLY`: no GitHub
workflow, ruleset, protected-branch, private-environment or direct-push claim is
made.

The implementation is bound by `COMMIT_BINDING_RECEIPT.json` to commit
`26899b8b6abaeb1e6db8cec1300cf0c4b24d294e` and tree
`02a69c55b6cf9fa5b26b6ecb1e3d8abf4b5ed5b5`.

The backend full suite passed on a newly created PostgreSQL database at migration
head `cv3_00000006`, an authenticated retained Redis service and a newly named
MinIO bucket. The final result was `1070 passed, 2 skipped`; the skips are the
known optional real-binary Pillow/Tesseract paths. Existing databases, buckets
and user objects were not reset or deleted. The dependency and image reports
were generated outside Git; their exact hashes and normalized results are bound
in `PRECOMMIT_CORRECTION_RECEIPT.json`. The original
`PRECOMMIT_RECEIPT.json` is retained byte-for-byte for auditability but is
superseded: it omitted the `local-eval` extra, used inconsistent aggregate count
semantics and included a non-reproducible working-tree diff hash. The correction
receipt records the correction time, reason and original receipt SHA-256.

An earlier cached image scan correctly failed on four HIGH `libuuid/util-linux`
findings. A clean `--no-cache` build consumed the fixed Alpine package and then
passed with zero HIGH/CRITICAL results. The security workflow now uses
`docker build --pull --no-cache`, and repository regression tests require this
plus immutable stateful-service digests and commit-pinned third-party actions.
