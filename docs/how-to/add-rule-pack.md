# How to Add a Fathom / Bosun Rule Pack

## Goal

Wire a packaged rule pack — Fathom (general-purpose) or Bosun (governance
flavour) — into a Stargraph graph's `governance` block so the engine
evaluates its CLIPS rules each step.

## Prerequisites

- Stargraph + Fathom installed (`pip install stargraph>=0.2`).
- A pack distribution to mount — either:
  - One you authored via [Author a Bosun pack](bosun-pack.md), or
  - A bundled pack: `stargraph.bosun.budgets`, `audit`, `safety_pii`,
    `retries`.
- Familiarity with the
  [Fathom rules tutorial](../tutorials/fathom-rules.md).

## Steps

### 1. Pick the pack flavour

| Flavour | When to use | Examples |
| --- | --- | --- |
| **Fathom** rule pack | General-purpose CLIPS rules; routing, classification, deterministic decisions. | Custom routing pack for a sandbox dispatcher. |
| **Bosun** governance pack | Cross-graph guardrails, signed; policy + audit. | `stargraph.bosun.budgets`, `stargraph.bosun.audit`. |

Both share the same on-disk shape (manifest + rules.clp + signatures);
the difference is the directory of conventions and the consuming
subsystem.

### 2. Author the pack

For Fathom packs, the
[Fathom rules tutorial](../tutorials/fathom-rules.md) is the canonical
walk-through. For Bosun packs, follow
[Author a Bosun pack](bosun-pack.md).

The pack directory minimally contains:

```text
my_pack/
├── manifest.yaml             # id, version, requires, provides
├── rules.clp                 # CLIPS deftemplates + defrules
├── manifest.jwt              # EdDSA-JWT signature
└── <key_id>.pub.pem          # Ed25519 sidecar (TOFU pin)
```

### 3. Validate the pack

Use the bundled Fathom plugin to lint and dry-run:

```bash
fathom validate ./my_pack
fathom test ./my_pack          # if pack ships tests/
fathom bench ./my_pack         # µs/eval regression check
```

**Verify:** `fathom validate` exits `0`. The output reports rule count,
fact templates, and any compile errors with line numbers.

### 4. Mount the pack from a graph

```yaml
# stargraph.yaml
governance:
  - id: "stargraph.bosun.my_pack"
    version: "1.0"
    requires:
      stargraph_facts_version: "1.0"
      api_version: "1"
```

Multiple mounts compose: the engine merges the rule sets and runs them
under one Fathom session per graph step. Order doesn't matter unless
your pack relies on rule salience — declare it explicitly in
`rules.clp` if so.

### 5. Run the graph and inspect rule firings

```bash
stargraph run ./stargraph.yaml --inputs message="hi" --inspect
```

`--inspect` prints the per-rule firing trace against synthetic fixtures:

```
graph_hash=<sha256>
rule_firings=3
  rule=budget-exhausted-token fired=False matched=[] actions=[]
  rule=audit-tool-call fired=True matched=[node_a] actions=[assert]
  ...
```

Run it for real:

```bash
stargraph run ./stargraph.yaml --inputs message="hi" --log-file run.jsonl
stargraph inspect <run_id> --diff 0 5     # CLIPS fact diff between steps 0 and 5
```

## Wire it up

If the pack is shipped as a plugin distribution, install it; the
`stargraph.packs` entry-point + `register_packs` hook surfaces it
automatically:

```bash
pip install stargraph-pack-my-pack
stargraph run ./stargraph.yaml --inspect      # mount-by-id resolves the installed pack
```

If the pack lives on disk locally (no distribution), mount it
filesystem-relative — the loader walks `governance:` entries and
resolves them in order: installed plugins first, then a configured pack
search path (see [serve/bosun](../serve/bosun.md) discovery section).

<!-- TODO: verify the exact filesystem-pack discovery flag once the bosun loader lands its public surface. -->

## Verify

After running:

```bash
stargraph inspect <run_id> --db .stargraph/run.sqlite
```

The timeline shows `rule_firings` per step. For Bosun packs that emit
violations, `stargraph inspect <run_id> --diff <N> <M>` prints CLIPS facts
added between steps — `(bosun.violation ...)` rows are how the engine
halts the run.

## Troubleshooting

!!! warning "Common failure modes"
    - **`PackCompatError: stargraph_facts_version mismatch`** — bump the
      pack's `requires.stargraph_facts_version` or pin Stargraph.
    - **`PackSignatureError`** — the JWT failed verification. Check
      that `manifest.jwt` was signed with EdDSA (the only allowed alg)
      and that the sidecar `<key_id>.pub.pem` matches.
    - **Rules don't fire** — re-run with `--inspect` and confirm the
      rule actually appears under `rule_firings`. If absent, the pack
      mount didn't resolve; check `governance:` IDs against installed
      packs.
    - **`PluginLoadError: namespace conflict`** — two installed pack
      distributions share a namespace. Uninstall the offender.

## See also

- [Author a Bosun pack](bosun-pack.md) — packaging your own.
- [Bosun in serve](../serve/bosun.md) — discovery, signing, lifecycle.
- [Fathom rules tutorial](../tutorials/fathom-rules.md) — CLIPS rules
  end-to-end.
- [Reference: signing](../reference/signing.md).
- [Bundled bosun packs](https://github.com/KrakenNet/stargraph/tree/main/src/stargraph/bosun).
