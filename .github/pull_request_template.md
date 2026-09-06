## Change

Describe the scoped change and the canonical task item it satisfies.

## Risk and data classification

- Risk level: low / medium / high
- Data touched: public / internal configuration / user data / credentials
- External effects or provider calls: none / describe

## Migration

- Schema or data migration: none / describe revision and recovery path
- Backup/restore evidence: not applicable / link artifact or receipt
- Existing-user-data impact: none / describe

## Verification evidence

- [ ] Formatter and lint
- [ ] Type checks
- [ ] Unit tests
- [ ] Integration tests
- [ ] Migration checks
- [ ] Frontend build/browser smoke, when applicable
- [ ] Security and dependency scans

Commands, exact commit SHA, CI run links and artifact hashes:

## Rollback

Describe the reversible rollback path and any operation that is not reversible.

## Review

- [ ] No secret, private endpoint, raw runtime data or PII was added
- [ ] Documentation and `AKTIF_GOREV.md` evidence fields reflect only executed checks
- [ ] Security/governance/migration changes received owner review
