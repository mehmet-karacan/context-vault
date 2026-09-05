# A3 canonical tree and documentation evidence

The implementation commit binds historical-projection staleness to the actual
checkout SHA, rejects missing provenance/warnings, and runs the projection-safety
gate in backend CI. Four historical views are correctly reported as stale and
safe; none is treated as canonical truth.

The current ignored `status/verified-state.json` was generated on a clean
checkout for the exact implementation commit. It remains truthfully
`verified=false` because remote CI and main-ruleset receipts do not exist. The
receipt therefore closes only the local A3 source/documentation work and does
not claim the owner license decision or remote governance.
