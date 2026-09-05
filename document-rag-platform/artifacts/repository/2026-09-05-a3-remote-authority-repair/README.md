# A3 remote-authority repair evidence

This package records the exact-source repair and adversarial re-review of the
verified-status remote evidence boundary. It is not remote CI or repository
governance evidence and must not be used to close those gates.

The verifier now repeats authenticated GitHub API reads for every claimed run,
workflow and ruleset. It also checks the live repository default branch, exact
`main` SHA, empty ruleset exclusions, actual job/check contexts and the GitHub
Actions integration id. Two independent reviews returned P0/P1/P2 `0` after
adversarial default-branch, wildcard, integration-id and fabricated-receipt
tests.

GitHub remained unchanged during this work. At observation time, remote `main`
was still behind the local candidate, unprotected and covered by no ruleset.
Consequently the generated exact-checkout state correctly remained
`verified=false`.
