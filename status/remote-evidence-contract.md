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
is bound to the same SHA and carries its GitHub run/workflow ids, attempt,
completed status, repository run URL and workflow path. The generator resolves
each id through the authenticated GitHub REST API and compares repository,
workflow identity, event, SHA, status, conclusion and URL. A syntactically valid
local JSON file is therefore insufficient.

## Main ruleset receipt

Path: `document-rag-platform/artifacts/governance/latest/main-ruleset.json`

The JSON object must bind the exact checkout SHA and an active positive
`ruleset_id` for `main`. The generator resolves the ruleset and `main` branch
through the authenticated GitHub REST API. It requires `main` to point at the
exact SHA, branch-targeting conditions, required PRs, up-to-date status checks,
conversation resolution, blocked force-push/deletion, and the owner-only admin
bypass representation. Required status checks are the actual GitHub job/check
contexts (`backend`, `frontend`, `offline-contract-fixture`, `supply-chain`, two
CodeQL matrix checks and `check-ownership`), not workflow display names.

A ruleset response does not prove that an attempted direct push was rejected.
That acceptance item stays separate and open until a real rejection probe has
its own evidence; the receipt's self-reported `direct_push_probe` field is not
trusted by this generator.

These files are evidence projections, not configuration inputs. They must be
produced from actual GitHub API/run results; hand-authored placeholders fail
closed because validation repeats authenticated API reads. If `gh` is missing,
authentication fails, an id is unknown, or live state disagrees, validation is
false without trusting receipt text. Both `latest/` paths are Git-ignored so
adding evidence after a run does not change or dirty the exact commit being
verified; durable release evidence is copied to the later immutable release
package with its checksum.
