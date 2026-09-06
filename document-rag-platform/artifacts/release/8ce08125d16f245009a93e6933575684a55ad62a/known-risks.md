# Known non-blocking risks

Candidate SHA: `8ce08125d16f245009a93e6933575684a55ad62a`

No open P0/P1 implementation, migration, integrity, CI, security, governance,
benchmark, restore, alert-route, license or exact-candidate verifier gate remains
for the approved local MacBook deployment scope.

- `A11-RISK-002` (P2): the fresh restore drill intentionally uses disposable
  encrypted synthetic data. An approved backup of any future authoritative
  production-data source must pass the same non-destructive drill before that
  source is promoted.
- `A11-RISK-003` (P2): an external scheduler or key-escrow service is not enabled
  in the current local deployment. Enabling either introduces a new admission
  gate and requires provider-side receipts.
- `A9-RISK-005` (P2): the exact local benchmark has Recall@5/MRR `0.833333` and
  one successful `answerable_claims_empty` repair. Absolute safety gates remain
  zero and the owner-sealed regression budget passed.

The repository owner selected explicit proprietary/no-license terms. The active
MacBook SLO policy is baseline-bound, all eleven current measurements passed,
and a real external GitHub Issues test alert was assigned to and acknowledged by
`@mehmet-karacan` in issue #2.
