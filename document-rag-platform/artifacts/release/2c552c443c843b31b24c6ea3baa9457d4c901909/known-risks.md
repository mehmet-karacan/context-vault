# Known risks and human/external release gates

Candidate SHA: `2c552c443c843b31b24c6ea3baa9457d4c901909`

No open implementation, migration, data-integrity, CI, CodeQL, governance or
supply-chain P0/P1 finding remained after the exact-candidate verification. The
candidate branch and pull request are published, but merge, tag and release stay
fail-closed because these non-automatable gates remain:

- The fresh exact-candidate real benchmark needs an owner-issued approval bound
  to the private pack, local BGE-M3 and generation model, followed by an
  independent request-boundary/non-transfer receipt and a post-run human seal.
- A production alert route needs a named owner/escalation and a provider-side
  delivery/acknowledgement receipt. The local rehearsal intentionally does not
  impersonate a production notification provider.
- The public repository owner has not selected a repository license or an
  explicit proprietary/no-license policy. Dependency-license scanning is PASS,
  but it cannot make this legal choice.

The local BGE-M3/Qwen evidence proves the pinned snapshots run offline on this
MacBook. It does not fabricate the missing human authority artifacts.
