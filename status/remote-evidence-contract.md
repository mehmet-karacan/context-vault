# Remote evidence contract

`scripts/generate_verified_status.py` never treats file presence as remote
authority. The ignored `status/verified-state.json` can become `verified=true`
only when both public-safe receipts below validate against its exact checkout
SHA.

## CI receipt

Path: `document-rag-platform/artifacts/ci/latest/ci-runs.json`

The JSON object must use `schema_version: "1.0"`, repository
`mehmet-karacan/context-vault`, exact `head_sha`, and `status: "PASS"`. Its
`runs` list must contain successful, automatic (`push` or `pull_request`) runs
for `ci-backend`, `ci-frontend`, `ci-rag-contract`, and `ci-security`. Every run
is bound to the same SHA and carries a positive GitHub run id and repository run
URL.

## Main ruleset receipt

Path: `document-rag-platform/artifacts/governance/latest/main-ruleset.json`

The JSON object must bind the exact checkout SHA and an active positive
`ruleset_id` for `main`. It must prove required PRs, up-to-date branches,
conversation resolution, blocked force-push/deletion/direct-push, the explicit
`owner_emergency_receipt_only` bypass policy, and all five required status
checks (the four workflows above plus `commit-ownership`).

These files are evidence projections, not configuration inputs. They must be
produced from actual GitHub API/run results; hand-authored placeholders fail
closed. Both `latest/` paths are Git-ignored so adding evidence after a run does
not change or dirty the exact commit being verified; durable release evidence is
copied to the later immutable release package with its checksum.
