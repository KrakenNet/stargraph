# AGENTS.md — src/stargraph/ovarp (OVARP attestation integration)

Local contract for attesting StarGraph governance ticks as OVARP receipts.
Parent: [`../AGENTS.md`](../AGENTS.md).

## Purpose

StarGraph's side of the OVARP producer-runtime replay contract (ADR-0012): emit a
Fathom-routed tick as a signed, offline-verifiable `replayable` receipt, and
reproduce that tick bit-for-bit under `ovarp verify --replay`. The sibling repo
`../../../../ovarp` (Rust) owns the receipt format + the offline verifier; this
package only produces receipt inputs and reproduces ticks.

## Local Contracts

- **One construction path, two callers.** `harness.build_attestable_run` builds
  the run for *both* the emit side (the sink) and the reproducer, and
  `harness.producer_output_from_checkpoint` projects the attested body for both,
  so a re-run reconstructs a byte-identical tick. Never fork the two sides — a
  divergence silently VOIDs receipts.
- **`producer_output` is wall-clock-free.** It is a pure projection of the
  committed `Checkpoint` (outcome/node/step/state/side_effects_hash); no
  `timestamp`, no floats (JCS-safe). Its `sha256(JCS(...))` is `result.digest`.
- **Two independent evaluators (ADR-0010).** The decision is encoded twice; verify
  step 4 VOIDs on divergence. Two modes:
  - *routing* (`example.py` merge-gate): Fathom YAML (`GOV_ROUTING_PACK`, in-stack)
    + a hand-authored OVARP v0 pack (`OVARP_PACK`, offline); outcome `goto:<node>`.
  - *governance* (`clearance_gate.py`): **one** Fathom pack, evaluated in-stack by
    CLIPS (`Engine.evaluate().decision`, surfaced as `adapter.last_evaluation`) and
    offline by OVARP's IR — the SAME pack auto-lowered via `ovarp lower-fathom` (no
    hand-authored offline pack); outcome the bare action (`allow`/`deny`).
    Independence is CLIPS-engine vs IR-evaluator on identical rules. The decision is
    read via `harness.governance_decision(run)` (from the adapter's `last_evaluation`)
    on **both** emit and reproduce — one derivation, or the `result.digest` match
    drifts. That is per-adapter mutable state, so governance attestation assumes
    **single-tick** dispatch; a parallel governance graph would need the decision
    carried on the `Checkpoint`.
- **A governance pack must be pure + Mirror-shaped to auto-lower.** `ovarp
  lower-fathom` refuses any rule with a `then.assert` (RHS forward-chaining is out of
  the profile), so a pack that asserts `stargraph_action` for routing CANNOT also
  lower — a governance attestation uses `then: {action, reason}` only. Templates are
  Mirror-shaped (one per mirrored field, `value` slot + provenance) because the
  Mirror boundary crosses one fact per field; rules join across the `value` slots.
  `harness.materialize_pack_dir` (lowering) and `_build_fathom_adapter` (in-stack)
  share `_mirror_template_defs`, so the lowered IR and the CLIPS templates never drift.
  `materialize_pack_dir` writes the pack under `dest/pack_name`, so it validates
  `pack_name` is a single path segment first (no `/`, `..`, or absolute root) — a
  public entry point mirroring the `load_bundle_from_store` content-address guard.
- **Fathom wiring is public-only.** Register `stargraph_action` + mirror
  deftemplates through `engine.load_templates` (YAML). Do **not** use
  `register_stargraph_action_template` here — it builds the CLIPS side only and
  leaves `engine.query` unable to read routing facts back.
- **Pinned reference values live in `harness`.** `FATHOM_ROUTER_MODEL_DIGEST` /
  `FATHOM_ROUTER_DECODING_DIGEST` are imported by both the emit spec and the
  reproducer gate so they cannot drift.
- **Emit is online-allowed; verify is not.** The sink may shell to `ovarp`
  (put/emit) at emit time. Never invoke the verifier from runtime code — it stays
  a separate offline step. The sink is opt-in via `GraphRun.receipt_sink`.
- **Untrusted digest at the reproducer boundary.** `load_bundle_from_store`
  takes its digest from the receipt `replay_trace` (attacker-controllable), so it
  validates the hex tail as a real sha256 content-address *before* using it as a
  path component. Keep that guard — without it a crafted digest drives a path
  traversal or an unbounded read (`/dev/zero`) at verify time.

## Work Guidance

- Attest a new graph: supply an `AttestationSpec` and wire `OvarpReceiptSink` as the
  run's `receipt_sink`; the `dispatch_node` seam (runtime/dispatch.py, step 7b) calls
  `record(...)` per committed tick. Two modes: routing (set `ovarp_pack`; see
  `example.merge_gate_attestation_spec`) or governance (leave `ovarp_pack=None`, set
  `pack_name`; the sink auto-lowers `fathom_pack` — see
  `clearance_gate.clearance_gate_attestation_spec`).
- The reproducer command is `stargraph ovarp-reproduce --store <dir>` (stdin/stdout
  JSON harness protocol); it must write **only** the result JSON to stdout.
- Changing the bundle shape means changing both the sink (writer) and
  `harness.reproduce_from_bundle` (reader) together.

## Verification

`uv run pytest tests/integration/test_ovarp_receipt_e2e.py` (routing) and
`tests/integration/test_ovarp_clearance_gate_e2e.py` (governance auto-lower) — both
need the `ovarp` binary (`$OVARP_BIN` or `../ovarp/target/release/ovarp`); skip if
absent. Also `uv run ruff check` + `uv run pyright` on this package (strict).
