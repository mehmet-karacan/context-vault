# A11 Doctor Runtime Evidence

This package records a real, read-only A11 doctor run against an isolated
Compose project at exact repository revision
`7d36fc74143d7504113ba6ac3adeaf9d736537bc`.

The disposable environment used the committed base, development and CI Compose
overlays. It contained eight declared services and four declared volumes. The
database was migrated to `cv3_00000006`; PostgreSQL, Redis and MinIO probes
passed; the backend and worker were running and healthy. The doctor found zero
missing, duplicate or unready contract services and zero orphan containers or
volumes. The receipt retains neither credentials nor raw container identifiers.

The rehearsal reproduced two defects before PASS: Compose's dedicated
`config --volumes` projection required interpolation despite
`--no-interpolate`, and `docker ps -q` returned short identifiers while inspect
returned full identifiers. The implementation now derives both service and
volume names from one bounded non-interpolated JSON projection and requests
full container identifiers. Both failures have regression tests.

The development overlay was used only to expose the disposable PostgreSQL port
for the host-side migration-head check. This receipt is evidence for the doctor
runtime contract, not evidence that a development overlay is production-safe.
