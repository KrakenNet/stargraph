# cve-remediation graph scaffold

Harbor IR scaffold for the CVE remediation pipeline (v6, P0+P1+P2+P3 applied).

Design:
- Spec: [`../cve-rem-graph.md`](../cve-rem-graph.md) (v6, definitive).
- Source notes: [`../cve-rem-pipeline.md`](../cve-rem-pipeline.md).
- Earlier draft: [`../pipeline-graph.md`](../pipeline-graph.md) (v5).

## Layout

```
graph/
  harbor.yaml                             parent IR — Phase 1..5 (steps 1-18)
  state.py                                CveRemState pydantic schema
  tool-id-mapping.md                      master table: every tool id + status
  nodes/                                  production-wiring slot
  rules/README.md                         custom rule packs referenced by IRs
  subgraphs/
    sandbox_dispatch.yaml                 step 11 — branched sandbox runtime
    progressive_execute.yaml              step 13 — canary -> stage -> fleet
  triggered/                              spawned independently by triggers
    drift_watch.yaml                      step 15 — 24-72h passive watch
    tier_re_eval.yaml                     hourly cron; re-fires SSVC on TRACK/DEFER
    audit_anchor.yaml                     daily 03:00 UTC; chain head -> Nautilus JWS
    lab_leak_reaper.yaml                  hourly cron; sweeps expired CargoNet labs
    rolling_restart.yaml                  webhook (post-Shamir) + weekly cron
  phase0/
    doctrine_ingest.yaml                  one-shot doctrine load + manifest sign (idempotent)
  phase6/
    offline_learning.yaml                 network-isolated GEPA + Shamir + ship
  tests/
    test_smoke.py                         IR load / routing / structural checks
```

## Why split

| boundary                      | rationale                                                    |
| ----------------------------- | ------------------------------------------------------------ |
| main vs phase 0               | bootstrap cadence; runs once per corpus version-pin bump    |
| main vs phase 6               | network isolation boundary (firewall-enforced)              |
| main vs sub-graph             | tightly-bound child workflows (probe, rollout)              |
| main vs triggered             | independent cadences (drift watch, cron sweeps, signals)    |

## Sandbox runtime selection

Deterministic — not LLM-driven. `sandbox_dispatch` reads `vuln_class` from
extractor output and sets `sandbox_runtime`:

| `vuln_class`                                                      | runtime              | branch |
| ----------------------------------------------------------------- | -------------------- | ------ |
| `network-protocol / routing / switching / firewall / ipsec / bgp`| `cargonet_lab`       | 11a    |
| `application / library / web-framework / container / host-os-pkg`| `docker_compose`     | 11b    |
| `config-only / cipher-suite / tls-policy / acl-rule`             | `static_detection`   | 11c    |
| `logic-flaw / business-rule / no-probe`                          | `skip` (forces HITL) | 11d    |

## HITL gates

4 durable-wait gates. All set `timeout: null` (Temporal `wait_condition`
semantics — durable, zero CPU during wait, never auto-deny). Each gate is
followed by a `branch_resp_<gate>` passthrough that pattern-matches
`(response (decision approve|reject|approve_replan))` and routes:

| gate                          | approve                            | reject                                  | approve_replan          |
| ----------------------------- | ---------------------------------- | --------------------------------------- | ----------------------- |
| `hitl_ingest_review`          | `correlate_assets`                 | halt (quarantine artifact retained)     | —                       |
| `hitl_plan_review`            | `validate_dispatch`                | halt (pipeline aborted)                 | `mcp_retrieval_dispatch` |
| `hitl_change_approval`        | `progressive_execute`              | halt (nothing applied to prod)          | —                       |
| `hitl_retrospective_review`   | `action_done` (cmdb_match=true)    | `action_done` (cmdb_match=false; GEPA)  | —                       |

Sandbox-fail (`r-sandbox-fail-replan`) routes to `hitl_plan_review` —
re-plan only via human approval.

## Parallel fan-outs

| rule                  | targets                                                            | join                  |
| --------------------- | ------------------------------------------------------------------ | --------------------- |
| `r-mcp-fanout`        | 5 retrieval tools (vec_search_retros, graph_priors, blast, framework, cargonet_telemetry) | `planner`             |
| `r-validate-fanout`   | `judge_safety`, `judge_lint`                                       | `validate_plan_join`  |
| `r-retro-fanout`      | `publish_docplus`, `cargonet_writeback`, `plan_kg_writeback`       | `retro_join`          |

## Artifacts emitted

Main pipeline writes 6 ArtifactRefs across the run:

| node                          | artifact                                                          |
| ----------------------------- | ----------------------------------------------------------------- |
| `emit_quarantine_artifact`    | raw untrusted text + canonicalized pair                           |
| `emit_remediation_bundle`     | apply / rollback / verify / metadata 4-tuple per runtime          |
| `emit_sandbox_evidence`       | probe traces, Batfish diffs, container logs                        |
| `emit_evidence_bundle`        | plan + bundles + sandbox + JWS chain + Reflexion + recon_anomaly  |
| `emit_retro_payload`          | retro record bytes                                                 |
| `emit_docx_archive`           | DOCX summary (also serves as Doc+ staging)                         |

Phase 0 emits `emit_manifest_artifact`. Phase 6 emits `emit_redacted_corpus`
+ `emit_compiled_artifact`. Triggered graphs emit summary artifacts
(`emit_re_eval_summary`, `emit_anchor_receipt`, `emit_reaper_summary`,
`emit_restart_summary`, `emit_rollback_record`).

## Node kinds

| kind             | usage in main                                                              |
| ---------------- | -------------------------------------------------------------------------- |
| `passthrough`    | branching/dispatch helpers, sub-state mutators                             |
| `broker`         | external calls via Nautilus (Nautobot, CMDB, ServiceNow, Doc+, CargoNet, Harbor `/v1/runs` for drift_watch_spawn) |
| `dspy`           | extractor, classifier, critique, planner, code_writer, critic, render_docx |
| `tool`           | Fathom checks, runtime lints (ansible/k8s/tf/sbom/vendor), gNMI, Batfish, redis Reflexion buffer, Ed25519, sha256, TEI |
| `ml`             | (used in triggered drift_watch.yaml + phase6 score_on_holdout)             |
| `write_artifact` | 6 artifact emissions above                                                  |
| `interrupt`      | 4 HITL gates                                                                |
| `subgraph`       | sandbox_dispatch + progressive_execute                                      |

## Triggers (declared outside IR)

| graph                | trigger spec                                          |
| -------------------- | ----------------------------------------------------- |
| `harbor.yaml`        | webhook (Nautilus CVE feed event) + manual            |
| `phase0/...`         | manual + cron (corpus-pin-bump check)                |
| `phase6/...`         | cron (weekly Phase-2 / nightly Phase-3+; isolated host) |
| `triggered/drift_watch`     | webhook (parent emits) + cron (orphan-sweep)   |
| `triggered/tier_re_eval`    | cron (hourly default)                          |
| `triggered/audit_anchor`    | cron (daily 03:00 UTC)                         |
| `triggered/lab_leak_reaper` | cron (hourly)                                  |
| `triggered/rolling_restart` | webhook (artifact_ready) + cron (Sun 04:00 UTC) + manual |

## Rule packs (custom, contents stubs)

| pack                          | mounted by                          |
| ----------------------------- | ----------------------------------- |
| `cve_rem.routing`             | main                                |
| `cve_rem.kill_switches`       | main                                |
| `cve_rem.doctrine_trust`      | phase0                              |
| `cve_rem.offline_isolation`   | phase6                              |
| `cve_rem.gepa_score_policy`   | phase6                              |

## Stores

| protocol      | provider     | use                                                                  |
| ------------- | ------------ | -------------------------------------------------------------------- |
| `VectorStore` | `lancedb`    | TEI embeddings: CVE text, doctrine corpus, retros similarity         |
| `GraphStore`  | `ryugraph`   | Asset-KG + Plan-KG + Doctrine-KG + Retrospective-KG                  |
| `DocStore`    | `sqlite`     | canonicalized records, doctrine docs, DOCX staging                    |
| `MemoryStore` | `redis`      | Reflexion episodic buffer (per CWE-class, cross-class similarity)    |
| `FactStore`   | `sqlite`     | CLIPS-mirrored facts at node-exit; provenance-typed                  |

## Run smoke tests

```bash
uv run python -m pytest demos/cve-remediation/graph/tests/test_smoke.py -v
```

137 tests:
- 41 IR/graph structural (smoke)
- 33 pack manifest + referential integrity
- 28 CLIPS round-trip integration tests across the 4 governance packs
- 35 tool-id consolidation + canonical-broker invariants
  (1 skipped: drift_watch has no write_artifact nodes, by design)

All pass on a clean checkout. Integration tests use Fathom Engine's
embedded CLIPS environment — no external services required.

## Tool ids

Every IR with a broker node declares the canonical
`nautilus.broker_request@1` (the single `@tool`-decorated callable
Harbor ships today). All ServiceNow / Doc+ / Nautobot / CMDB / CargoNet
calls dispatch through that broker via Nautilus adapter intent —
no per-system tool ids in the IR. Non-broker tool ids are placeholders
documented in [`tool-id-mapping.md`](tool-id-mapping.md) with status
(`mcp-pending`, `placeholder`, `nautilus-adapter`, etc.).

## What this scaffold does NOT yet include

- Real node implementations. Many nodes carry the right `kind` label but
  the runtime substitutes contextvar-bound stubs at run time per the
  `tests/fixtures/cve_triage.yaml` validation-gate POC pattern.
  Production wiring lands per-phase as nodes harden.
- Custom Fathom rule pack contents. Pack ids are referenced; `pack.yaml`
  files are the next deliverable.
- End-to-end execution. Smoke tests cover IR-load + structural-hash
  stability + routing-target resolution + phase coverage + multi-kind
  invariant + parallel-action + artifact-emission + branch_resp +
  durable-wait + sandbox-fail-replan + idempotency + triggered-graph
  presence.
