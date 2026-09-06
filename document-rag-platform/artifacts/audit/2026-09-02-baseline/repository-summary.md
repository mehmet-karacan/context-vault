# CV3 Baseline Repository Summary

- Observation time: `2026-09-02T10:43:33Z`
- Repository: `https://github.com/mehmet-karacan/context-vault`
- Remote default branch: `main`
- Baseline and remote HEAD: `6b99a53d19e7f5f7b07b403e751c629f79ab7663`
- Baseline divergence (`HEAD...origin/main`): `0/0`
- Initial working tree: clean
- Working branch created from the baseline: `fix/context-vault-v3-hardening`
- Baseline tracked files: `277`
- Repository bootstrap file `00_BASLA.md`: absent
- Remote writes, merges, releases, tags, and history rewrites: not performed
- Zekam registry: exact source root resolved as a local-only, read-only binding

The previous root task was archived without changing its bytes:

- Baseline task blob/archive SHA-256: `ec2e537c59fafc87340f4271075dcc12813073df44aaa0e901a5e86d3fe65f56`
- Activated V3 task source SHA-256: `6de7b2fe31e8fc11f125615239c7602c2c4de6d5d9426af10abbf9497dcfcd8f`
- Current canonical task SHA-256 after evidence/status update:
  `70b4bdf4dd3faff17cfa77213d255837a8a7ab702094bfab5bcd8b36ad6b10e0`

The current dirty state contains only authorized CV3 bootstrap coordination,
verification, and audit files. It is not presented as a clean baseline.

`scripts/verify_baseline.py` was also executed against a temporary clean,
detached worktree at the exact baseline. Strict result: `PASS`; dirty: `false`;
migration head: `b2f1c0a10003`; findings: none. The private machine-readable
verification report SHA-256 is
`cd93832eee06e6384289c7efac368d7d8da93fca9be8aa8209dd096c0c59c893`.

## Local observer runtime

- Git `2.55.0`
- Docker client/server `29.7.2`
- `uv` `0.12.5`
- Host Python `3.9.6`; CI reproduction environment Python `3.12`
- Node `24.18.0`; npm `11.16.0`
