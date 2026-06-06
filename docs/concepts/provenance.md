# Provenance and Replay

Every Stargraph run emits a trace that is sufficient to replay the run: IR hash, plugin versions, RNG seeds, tool inputs/outputs, and Fathom decisions. Provenance is non-negotiable — a run without a complete trace is treated as an error, not a warning.

## What is captured

- **IR hash** — content-addressed identity of the graph.
- **Plugin set** — distribution name, version, and `api_version` for every registered plugin.
- **Decision log** — Fathom rule firings, in order, with fact snapshots.
- **I/O envelopes** — content-addressed payloads for all node inputs and outputs.

> TODO: cross-link the replay CLI and the trace schema once they ship.
