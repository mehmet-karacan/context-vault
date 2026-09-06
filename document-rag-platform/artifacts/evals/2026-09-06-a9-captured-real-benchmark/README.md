# A9 captured local real-benchmark evidence

This public-safe package records the bounded outcome of the owner-approved local
BGE/Qwen benchmark. The private execution records, golden labels, request-capture
evidence, nonces, credentials and absolute external paths remain outside Git.

The first captured run was retained as a performance outlier and was not promoted.
A second fresh-database run was independently reviewed and human-sealed because its
end-to-end p95 remained inside the existing 20% regression limit. A third fresh
run passed the strict comparator against that immutable baseline. Both the baseline
and strict run have exact independent-request-capture non-transfer receipts whose
bindings were validated without invoking a provider.

The local runner report deliberately keeps `release_gate_eligible=false`: the
receipt checker validates exact hashes and ordering but cannot authenticate human
identity or make a remote release decision. No push, merge, release or existing
database/object deletion occurred.
