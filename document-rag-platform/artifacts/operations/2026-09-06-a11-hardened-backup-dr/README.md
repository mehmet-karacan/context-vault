# A11 Hardened Backup/Restore Evidence

This package records the successful fresh-target rehearsal of the hardened A11
backup/restore tool at exact repository revision
`bde49aca525f7ccc40b21c039af003413621223c`.

The rehearsal used four uniquely labelled disposable resources: a source
PostgreSQL/MinIO pair and a physically distinct target PostgreSQL/MinIO pair.
The source held one synthetic `CVENC1` AES-256-GCM envelope and one matching
`storage_objects` reference. No user database, bucket, volume, object or project
file was read, changed or removed.

The target database and bucket were proven absent before the restore. The target
PostgreSQL container carried `com.context-vault.restore-target=true`; immutable
container and cluster identities were bound by hashes. The manifest SHA-256 was
read from the separately emitted backup receipt and supplied explicitly to the
restore command.

Results:

- backup: `PASS`, versioning enabled, one of one objects authenticated as an
  encrypted envelope;
- restore: `PASS`, Alembic head `cv3_00000006`, auth/retrieval/citation smoke
  checks all true;
- reconciliation: one DB reference, one object, zero missing, zero orphan;
- measured RPO: 16.184 seconds; measured RTO: 0.794 seconds;
- independent post-check: source unchanged, source/target database rows and
  object payloads match, restored envelope authenticates, clusters differ;
- cleanup: all four disposable containers and all four anonymous volumes removed,
  with zero labelled resources remaining.

An earlier setup attempt used a literal escaped header in the synthetic fixture.
The backup tool rejected it after dump creation and before any completed manifest
or restore. That incomplete material is excluded from this package. The fixture
was recreated from scratch with the seven-byte `CVENC1` NUL-terminated header and
authenticated independently before the successful run.

Private restore material remains outside Git under directory mode `0700` and file
mode `0600`, subject to the documented 30-day retention policy. This package
contains only hashes, counts, booleans and timestamps; it contains no object key,
credential, endpoint or private backup path. A successful synthetic drill is not
a release approval, so both tool receipts remain `release_gate_eligible=false`.
