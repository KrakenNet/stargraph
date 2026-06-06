# Bosun Packs in Serve

Bosun packs are signed CLIPS rule bundles that `stargraph.fathom` evaluates
during graph execution. Phase 4 ships four reference packs:
`stargraph.bosun.budgets`, `stargraph.bosun.audit`, `stargraph.bosun.safety_pii`,
and `stargraph.bosun.retries`. Pack discovery happens at serve startup;
verified packs are loaded into the FathomAdapter and stay loaded for
the process lifetime (no hot-reload in v1, see threat model).

The serve surface intentionally does NOT expose pack-management routes:
operator workflow is filesystem + restart, not API mutation.

## Topics

- TODO: pack discovery (entry-points + filesystem).
- TODO: signing alg-strict (Ed25519 only).
- TODO: TOFU + static allow-list pubkey distribution.
- TODO: pack-load audit events.
- TODO: rule-fact CPU caps (per-pack limits).
- TODO: hot-reload absence (post-1.0; see threat-model.md).
