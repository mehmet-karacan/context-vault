# A9 stable approval-input evidence

This public-safe package records local hardening of the A9 approval boundary.
It contains hashes and bounded outcomes only; the external private execution and
golden records are not copied into Git.

The exact implementation rejects symlinks and oversized authority manifests,
uses one stable byte read for parsing and hashing, and repeats stable hash checks
before returning a preflight/approval receipt or dispatching the approved runner.
Post-run checks still detect changes during execution. Adversarial tests mutate
each authority file in the former race windows and verify that no valid receipt
or runner dispatch occurs.

This is not an owner review, benchmark approval, provider execution,
non-transfer verification or baseline seal. The real A9 human gates remain open.
