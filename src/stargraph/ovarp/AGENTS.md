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
- **Two independent evaluators (ADR-0010).** The routing predicate is encoded
  twice: Fathom YAML (`GOV_ROUTING_PACK`, in-stack) and the OVARP v0 pack
  (`OVARP_PACK`, offline re-eval). They must converge on the same outcome string
  (`goto:<node>`); verify step 4 VOIDs on divergence. Keep them in lockstep.
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

- Attest a new graph: supply an `AttestationSpec` (see
  `example.merge_gate_attestation_spec`) and wire `OvarpReceiptSink` as the run's
  `receipt_sink`. The `dispatch_node` seam (runtime/dispatch.py, step 7b) calls
  `record(...)` per committed tick.
- The reproducer command is `stargraph ovarp-reproduce --store <dir>` (stdin/stdout
  JSON harness protocol); it must write **only** the result JSON to stdout.
- Changing the bundle shape means changing both the sink (writer) and
  `harness.reproduce_from_bundle` (reader) together.

## Verification

`uv run pytest tests/integration/test_ovarp_receipt_e2e.py` — needs the `ovarp`
binary (`$OVARP_BIN` or `../ovarp/target/release/ovarp`); skips if absent. Also
`uv run ruff check` + `uv run pyright` on this package (strict).
