# A9 review-admission hardening

This public-safe package records a fail-closed repair to human-review actor
admission. Before the repair, schema-valid `UNAPPROVED_*` values were not rejected
and a private-pack reviewer value with leading whitespace could bypass the
`PENDING` guard. The benchmark approval variant could reach runner dispatch when
its remaining bindings were valid.

The repair moves finality out of free-form actor names. The runtime now accepts
only private-pack manifest v2 with exact `review_status=approved` and benchmark
approval manifest v3 with exact `decision=approved`. Baseline seal v2 and golden
non-transfer receipt v1 retain their existing exact `status`/`decision` fields.
Legacy private v1 and approval v1/v2 schemas remain historical-only.

Baseline and seal bytes are read stably and fully validated before runner dispatch.
The post-run provenance and metric comparison consumes that same in-memory
binding, so a malformed, unpaired, future-dated, hash-drifted or non-approved seal cannot
consume provider/model budget before rejection.
Successful regression candidates project only `baseline_report_sha256` and
`baseline_seal_sha256`; no baseline path, raw seal, or reviewer identity is copied.

No human identity, approval, private labels, prompt, model output, credential or
secret is included here. This package does not grant owner authority, approve the
private pack, seal a baseline, prove golden non-transfer or close A9.

The external pending dossier remains deliberately non-authoritative. Its private
manifest uses `review_status=pending`, which the repaired v2 final schema rejects;
its approval template uses `decision=pending` and keeps all human-selected
identity, time and budget fields unset. Every dossier file is mode `0600`.
