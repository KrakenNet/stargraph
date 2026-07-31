<!-- SPDX-License-Identifier: Apache-2.0 -->
# SOC Triage++ — auditable SOC alert triage on Stargraph

A running Stargraph graph that triages SIEM alerts the way an enterprise security
team needs to deploy an agent: **every decision is risk-scored by a pinned
model, governed by a signed policy pack, gated by a human, and sealed into a
tamper-evident audit chain — and any past decision can be replayed and
counterfactually re-run months later.**

This demo promotes [`soc-triage.md`](soc-triage.md) (the original design pitch)
to running code. It is one tile in the [`all-demo`](../../../all-demo/README.md)
launcher and runs under `stargraph serve`.

---

## What the demo shows (sales walkthrough)

A SOC L1 analyst's queue, end to end, with audit-committee-grade guarantees at
every step:

1. **Ingest** — a SIEM alert (`data/alerts_sample.jsonl`) is read into typed run
   state. The hero alert is `case_8821`: a LockBit-style mass file-encryption
   detonation on a **prod** billing database (severity 9.6, tier-0 asset).

2. **Retrieval (RRF priors)** — historical triage outcomes for similar
   signatures / asset tiers are fused with Reciprocal Rank Fusion
   (`data/priors/`) to give the model and analyst relevant precedent.

3. **ONNX risk scoring, sha256-pinned** — a small scikit-learn classifier
   exported to ONNX (`models/severity_classifier.onnx`) scores the alert in
   process. The model's content hash is **pinned in the graph IR**
   (`expected_sha256` in `graph/stargraph.yaml`) and verified on load by
   `stargraph.ml.loaders` — a supply-chain guarantee that the deployed model is the
   audited one. Features are a fixed 7-float vector
   `[severity_raw, asset_tier_onehot×3, source_reputation, hour_of_day,
   repeat_count]`.

4. **Deterministic triage decision** — a DSPy `triage_decide` node drafts the
   disposition + reasoning. In the default (Fathom-less) serve path it runs on
   Stargraph's deterministic stub, so the demo is fully reproducible without a live
   LLM (point an LLM at it via `LLM_BASE_URL`/`LLM_MODEL` to make it generative).

5. **Signed Bosun policy pack** — the `soc-policy` pack (`bosun-packs/soc-policy/`)
   carries 4 CLIPS governance rules, **Ed25519/JWS-signed** and verified at boot
   against a committed dev key:
   - prod asset + `auto_remediate` → **interrupt** (require human sign-off);
   - exec-owned asset + high severity → **escalate** (route to Tier 3);
   - `risk_confidence < 0.6` → **second opinion** (re-run with a different model);
   - every rule firing emits a provenance fact (`#actions == #provenance`).

6. **Analyst HITL interrupt** — the run pauses at `analyst_gate` for human
   sign-off (`POST /v1/runs/{id}/respond` resumes it). The prod ransomware hero
   case is exactly the kind of high-stakes action that should never auto-execute.

7. **Hash-chained audit JSONL (tamper evidence)** — the `AuditChain` node seals
   the run's append-only provenance trail into `.audit/<run_id>.jsonl`, each line
   carrying `SHA-256(prev_sha256 + canonical record)`. Deletion, reorder, or edit
   of any record breaks the chain and is detectable offline.

8. **Counterfactual replay** — because every step is checkpointed and
   deterministic, an auditor can fork a finished run from any checkpoint, replay
   it byte-identically ("the decision you made in March, reproduced in
   October"), or ask a what-if ("what if this asset were prod tier?") and diff
   the outcomes. See [the replay drill](#replay-drill) below.

---

## Boot command

Run it directly from the stargraph checkout (uses the stargraph venv via
`--no-project`):

```bash
cd /path/to/stargraph
uv run --no-project python demos/soc-triage/serve_soc.py --host 127.0.0.1 --port 9020
```

Point a triage LLM at it (optional — boots and reaches the HITL gate without one):

```bash
LLM_BASE_URL=http://localhost:41001 LLM_MODEL=qwen2.5 \
  uv run --no-project python demos/soc-triage/serve_soc.py --host 127.0.0.1 --port 9020
```

This exposes the standard `stargraph serve` surface: `GET /v1/runs`,
`POST /v1/runs`, HITL `POST /v1/runs/{id}/respond`, `WS /v1/runs/{id}/stream`,
SQLite checkpoints, and the counterfactual fork route — all for free.

Equivalently, launch the **SOC Triage++** tile from the `all-demo` dashboard
(it runs this exact command on port 9020).

### Graph viewer pairing

The launcher's **Stargraph Graph Viewer** tile (read-only, port **9100**) preloads
this graph's topology and attaches its live run watch to this server via
`--upstream http://localhost:9020` (it proxies `/api/runs*` → `/v1/runs*` and
forwards the WS stream). This is **zero graph-viewer code change** — viewer flags
only (NFR-8). Open the graph-viewer tile to watch a triage run stream node by
node while soc-triage++ runs headless.

---

## Replay drill

[`scripts/replay_drill.sh`](scripts/replay_drill.sh) is the auditor walkthrough.
Boot the server first (above), then:

```bash
# defaults to BASE_URL=http://localhost:9020, ALERT_ID=case_8821
demos/soc-triage/scripts/replay_drill.sh
```

It (1) starts a run for the hero alert, (2) polls it to the HITL pause, (3)
**replays** it with an empty mutation at step 0 (byte-identical fork — the
"cryptographic replay months later" proof), and (4) **counterfactuals** it with
`asset_tier=prod` (`fixtures/cassette_case_8821/counterfactual_tier_prod.json`)
to show how the soc-policy routing flips. It never fakes success — if the server
is unreachable or a step fails it prints an actionable error and exits non-zero.
Requires `curl` + `jq`.

---

## ⚠️ Development signing key — DEMO ONLY

`bosun-packs/keys/dev_signing_key.pem` is a dev-only Ed25519 private signing key
(key id `dev-soc-1cdb9c59`). It is **not shipped** (gitignored); only the
public-key sidecar (`<key_id>.pub.pem`) and the detached `manifest.jwt` are
committed, so `verify_pack` works out of the box. To re-sign the packs, generate
your own key (see `bosun-packs/README.md`).

> **NEVER use this key, or this pattern, in production.** In production, Bosun
> packs are signed with a key held in a KMS/HSM and only the public-key sidecar
> (`<key_id>.pub.pem`) ships. See `bosun-packs/README.md` for re-sign / verify
> recipes.

---

## Known limitations (honest gaps)

This demo's `serve_soc.py` runs **Fathom-less** (the serve path wires
`fathom: None`). Two consequences are documented honestly rather than hidden:

- **The HITL pause is static-IR-driven, not policy-driven, in this serve path.**
  With Fathom off, the CLIPS routing rules (including the prod + auto-remediate →
  `analyst_gate` branch) never fire; routing falls back to the static IR edge /
  declaration order. `analyst_gate` is in that linear order, so **every** run
  reaches the interrupt regardless of disposition or tier. The signed policy pack
  is real and verifies at boot, but it does not *drive the route* in this build.

- **The ONNX inference path does not re-verify sha256 at run time.** The
  `expected_sha256` pin **is** enforced **at model load** (`stargraph.ml.loaders`
  hashes the file when building the session). The cached ONNX session is not
  re-hashed per inference, and a wrong pin does not fail a run mid-flight. The
  meaningful supply-chain guarantee — "the committed model bytes match the IR
  pin" — holds at load; it is not an inference-time check.

- **The audit chain here is a demo-local hash chain, not the full Ed25519/JWS
  serve sink.** `AuditChain` writes a SHA-256-linked tamper-evident JSONL. The
  production-grade `stargraph.audit.jsonl.ChainedJSONLAuditSink` (EdDSA JWS +
  `prev_sha256`, `stargraph verify-audit`) cannot be wired through `serve_soc` deps
  today without new stargraph serve-side feature code; see the gap note in
  `graph/nodes.py` and the spec `.progress.md`.

---

## Layout

```
demos/soc-triage/
├── soc-triage.md          # original design pitch
├── README.md              # this file
├── serve_soc.py           # stargraph serve wrapper (--host/--port, default 9020)
├── graph/
│   ├── stargraph.yaml        # graph IR (ingest→retrieval→risk_score→triage→policy→gate→write→audit→halt)
│   ├── state.py           # RunState
│   └── nodes.py           # IngestAlert, RetrievalPriors (RRF), AuditChain
├── bosun-packs/
│   ├── soc-policy/        # 4 signed CLIPS governance rules
│   ├── budgets/           # per-alert token/cost/latency caps
│   └── keys/              # COMMITTED dev signing key (demo only)
├── models/severity_classifier.onnx   # sha256-pinned ONNX risk model
├── scripts/
│   ├── train_severity.py  # sklearn → ONNX (prints sha256)
│   └── replay_drill.sh    # auditor replay + counterfactual walkthrough
├── data/                  # alerts_sample.jsonl, priors/, assets.csv, runbooks/
├── fixtures/cassette_case_8821/       # hero-case cassette + counterfactual
└── tests/test_soc_graph.py            # graph load + sha256 + interrupt + replay
```
