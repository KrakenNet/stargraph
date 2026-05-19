# Rule packs for cve-remediation

5 custom packs supplement the 4 mandatory Bosun packs (`budgets`,
`audit`, `safety_pii`, `retries`) mounted on the IRs.

| pack                          | flavor       | mounted by                | files                                      |
| ----------------------------- | ------------ | ------------------------- | ------------------------------------------ |
| `cve_rem.routing`             | routing      | main `harbor.yaml`        | `pack.yaml`                                |
| `cve_rem.kill_switches`       | governance   | main `harbor.yaml`        | `manifest.yaml`, `rules.clp`, `__init__.py` |
| `cve_rem.doctrine_trust`      | governance   | `phase0/doctrine_ingest.yaml` | `manifest.yaml`, `rules.clp`, `__init__.py` |
| `cve_rem.offline_isolation`   | governance   | `phase6/offline_learning.yaml` | `manifest.yaml`, `rules.clp`, `__init__.py` |
| `cve_rem.gepa_score_policy`   | governance   | `phase6/offline_learning.yaml` | `manifest.yaml`, `rules.clp`, `__init__.py` |

## Routing vs governance

- **Routing packs** (YAML inline rules) supplement IR routing. They emit
  `goto` / `assert` actions to add context-sensitive behavior without
  changing the inline topology. Loaded as flavor `routing`.
- **Governance packs** (CLIPS) enforce invariants. They consume facts
  asserted by graph rules / external probes / RBAC-gated CLI and emit
  `bosun.violation` (severity `halt`) when policy is breached. The
  runtime auto-fires the appropriate Temporal kill-switch on
  halt-severity violations.

## What each pack does

### `cve_rem.routing`
- Tier escalation overlays — auto-escalate TRACK/DEFER on EPSS spike
  or KEV listing flip.
- Template-lookup ranking — weighted success × recency for multi-hit.
- Code-runtime preference — deterministic pick when extractor returns
  multiple candidates.
- Defer-window computation — EPSS-inverse mapping to days.
- Reflexion cross-CWE fallback — sibling-class buffer entries.
- Sandbox-runtime override for air-gapped environments.

### `cve_rem.kill_switches`
- Error-budget rules: rollback-rate >5%/24h, sandbox-mismatch >3%/24h,
  cross-bucket plan reuse, stuck-state >14d (informational page).
- Signal RBAC for `halt-new` and `halt-pause-in-flight` (single-signer
  roles: pipeline-owner OR security-eng).
- 2-of-3 quorum collection for `halt-rollback-in-flight` (3 rules,
  one per role pair: PO+SE, PO+NO, SE+NO).

### `cve_rem.doctrine_trust`
- Source-class policy — only trusted-doctrine sources may bypass
  injection classifier on Phase 0.
- Manifest-hash allowlist enforcement — active doctrine manifest hash
  must be in boot-gate allowlist.
- Pin sha256 immutability — same `corpus_version_pin` with divergent
  sha256 across two source facts is a supply-chain compromise signal.
- Deactivated-manifest refusal.

### `cve_rem.offline_isolation`
- No inbound from production zone (Phase 6 host).
- Egress only to `approved-drop` zone (signed prompts.tar drop).
- Replica load requires non-empty `redaction_pack_hash`.
- Replica `redaction_pack_hash` must match the currently-active signed
  redaction pack.

### `cve_rem.gepa_score_policy`
- Score-component range check (`[0,1]`); halt on out-of-range.
- Weighted score computation: `0.35*validation + 0.25*sandbox +
  0.15*cr_approved + 0.15*no_drift_7d + 0.10*no_rollback_30d`.
- Strictly-better epsilon-margin gate; emits `gepa_decision` accept/reject.
- Refuses Shamir ceremony on a rejected artifact.

## JWT signing

`manifest.jwt` files are NOT included in the scaffold. The deploy-time
`krakntrust` signing pipeline produces them from `manifest.yaml` +
`rules.clp` and the production signer key. For development, the
runtime accepts unsigned packs from a configured dev-allowlist; for
production all 5 packs are loaded only after their JWTs verify against
the boot-gate trust root.

## Tests

```bash
uv run python -m pytest demos/cve-remediation/graph/tests -v
```

102 tests total:

| file                              | count | scope                                                     |
| --------------------------------- | ----- | --------------------------------------------------------- |
| `test_smoke.py`                   |    41 | IR load + routing + structural invariants for 9 graphs   |
| `test_packs.py`                   |    33 | pack manifest + content + IR-pack referential integrity  |
| `test_pack_kill_switches.py`      |     9 | CLIPS round-trip: 4 metrics + RBAC + 2-of-3 quorum       |
| `test_pack_offline_isolation.py`  |     7 | CLIPS round-trip: inbound/egress/replica-redaction       |
| `test_pack_doctrine_trust.py`     |     6 | CLIPS round-trip: source-class/allowlist/pin-immutability |
| `test_pack_gepa_score_policy.py`  |     6 | CLIPS round-trip: weighted score + epsilon margin gate    |

The 28 CLIPS round-trip tests load each `rules.clp` directly into a fresh
Fathom Engine's CLIPS environment, assert input facts, run rules to
quiescence, and verify expected `bosun.violation` (or other emitted)
facts. Pattern matches `harbor/tests/integration/bosun/_helpers.py`.

Tests carry `pytest.mark.integration` per `pyproject.toml` marker
registry; they run without needing harbor-serve or external services.
