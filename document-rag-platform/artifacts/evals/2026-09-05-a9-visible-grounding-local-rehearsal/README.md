# A9 visible-grounding local rehearsal evidence

This package records the bounded diagnostic and visible-citation grounding work
from `run14` through `run19`, evaluated at source revision `807845d`. It contains
aggregate reports only. The raw pack, prompts, model outputs, credentials, and
golden labels are not stored in Git.

The evaluated source is revision `807845de7d35db34bce05a206ba4c87fb10d2aba`,
tree `3eeae9d929f0ebb03e05b7c3ed16ae56c9d0d4bd`. That tree is exactly the
reviewed `run17` tree; the later canonical no-answer example regressed three
answerable cases in `run18` and was reverted with a history-preserving commit.

The retained `run19` database is at Alembic `cv3_00000006` and contains six
projects, seven documents, eight versions, eight completed ingestion jobs, six
retrieval runs, twelve messages, five citations, and five claims. Its separate
MinIO bucket contains 24 objects, all 24 carrying the production AES-GCM marker.
No earlier database or bucket was deleted or reused.

`run19` used the exact offline BGE-M3 and Qwen snapshots. All absolute safety
counters are zero. Answerability FP/FN and unsupported-claim rates are zero;
citation precision/recall/coverage and answer sufficiency are 1.0. Recall@5,
MRR@10, and context precision remain 0.833333 because the expected no-answer
case still returns retrieval candidates. The canonical rule is regression
against an approved baseline, and no approved baseline exists, so retrieval
quality is recorded as partial rather than passed.

This is an unapproved synthetic local rehearsal. It is not an owner-reviewed
private dataset, an approved real-provider benchmark, independent proof that
golden data was not transferred, a human baseline seal, or an A9 release gate.
A9 remains open and A11 is not admitted.
