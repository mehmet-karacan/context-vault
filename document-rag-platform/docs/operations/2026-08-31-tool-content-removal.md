# Tool-specific content removal record (2026-08-31)

## Purpose

Tool-local configuration and bundled assistant skills were removed from the
repository. This historical record describes provenance without publishing a
user path, machine location or archived content.

## Removed content classes

- tool-local permission settings;
- bundled UI/design guidance;
- third-party skill-discovery instructions with unclear licensing/provenance.

The removed files were not application runtime dependencies. A local archive
was recorded outside the repository at the time of removal; its machine path is
intentionally not published. Restoration requires a new license, provenance and
supply-chain review rather than copying from a tool-specific directory.

## Current policy

- Tool-local settings are ignored by Git.
- External skills require an immutable source/version, checksum, license and
  admission review.
- CLI or assistant transcripts are not canonical project state.
