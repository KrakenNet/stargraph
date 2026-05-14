# Tool-ID Mapping

Canonical status of every tool id referenced in the cve-remediation IRs.
Updated whenever new tools land in Harbor's `@tool` registry or as MCP
servers come online.

## Status legend

| status               | meaning                                                                  |
| -------------------- | ------------------------------------------------------------------------ |
| `registered`         | `@tool`-decorated callable exists in Harbor's tool registry today        |
| `nautilus-adapter`   | Resolved via `nautilus.broker_request` → adapter (servicenow, neo4j, etc.) |
| `mcp-pending`        | Intended to be served via MCP (Model Context Protocol); not wired yet    |
| `placeholder`        | Design intent only; implementation lands per-phase as nodes harden       |
| `harbor-internal`    | Built into Harbor runtime (e.g. `artifacts.write`); not user-registered  |

## Master table

| tool id                                | status              | notes                                                      |
| -------------------------------------- | ------------------- | ---------------------------------------------------------- |
| `nautilus.broker_request@1`            | registered          | `harbor.tools.nautilus.broker_request`; `namespace=nautilus`, `name=broker_request`, `version=1`; `side_effects=read`, `replay=recorded-result`. **Canonical broker.** |
| `artifacts.write@1.0`                  | harbor-internal     | Backed by `harbor.artifacts.fs.FsArtifactStore`; bound on every `write_artifact` node automatically; explicit IR ref for capability gate clarity. |
| `ryugraph.cypher@1.0`                  | mcp-pending         | RyuGraph driver (Asset-KG, Plan-KG, Doctrine-KG, Retrospective-KG). Stop-gap: dispatch via `nautilus.broker_request` with `target=ryugraph` adapter. |
| `tei.embed@1.0`                        | mcp-pending         | text-embeddings-inference; deployed alongside Harbor; expose via MCP. |
| `redis.reflexion_buffer@1.0`           | mcp-pending         | Reflexion episodic-memory ops (read/write per CWE-class). |
| `fathom.ssvc_evaluate@1.0`             | placeholder         | Fathom CLIPS rule eval. Implemented by mounting an SSVC pack and calling `engine.evaluate()`; the IR-level "tool" abstraction wraps this. |
| `fathom.code_safety_check@1.0`         | placeholder         | Same pattern: code-safety pack + evaluate. |
| `fathom.watermark_recheck@1.0`         | placeholder         | Re-checks `fact_set_watermark` at validate stage. |
| `fathom.redaction_pack@1.0`            | placeholder         | Phase 6 replica boundary redaction. |
| `ansible.lint@1.0`                     | placeholder         | Shell-out wrapper. Trivial to register as `@tool`. |
| `k8s.kubeval@1.0`                      | placeholder         | Same; or use kubeconform / kyverno. |
| `terraform.tflint@1.0`                 | placeholder         | Shell-out wrapper. |
| `container.sbom_scan@1.0`              | placeholder         | syft / trivy SBOM. |
| `vendor.dry_run@1.0`                   | placeholder         | Vendor-CLI wrapper, vendor-specific. |
| `docker.compose_probe@1.0`             | placeholder         | Sandbox 11b; local docker harness orchestration. |
| `batfish.diff@1.0`                     | placeholder         | Sandbox 11c; static config-diff + behavioral sim. |
| `dspy.gepa_compile@1.0`                | placeholder         | DSPy GEPA driver; called from Phase 6 only. |
| `krakntrust.ed25519_sign@1.0`          | placeholder         | Signed-CLI integration; signs artifacts/manifests. |
| `krakntrust.shamir_sign@1.0`           | placeholder         | 2-of-3 ceremony driver (Phase 6 step 21). |
| `krakntrust.ship_artifact@1.0`         | placeholder         | Drops compiled prompt tar to `/etc/krakn/prompts/`. |
| `krakntrust.allowlist_update@1.0`      | placeholder         | Boot-gate doctrine-manifest allowlist append (Phase 0 D5). |
| `krakntrust.artifact_select@1.0`       | placeholder         | Rolling-restart artifact picker. |
| `krakntrust.pointer_snapshot@1.0`      | placeholder         | Save previous-generation prompt pointer. |
| `krakntrust.pointer_rollback@1.0`      | placeholder         | Roll prompt pointer back. |
| `postgres.audit_query@1.0`             | nautilus-adapter    | Routes via `nautilus.broker_request` + Nautilus postgres adapter. |
| `postgres.audit_failure_record@1.0`    | nautilus-adapter    | Same. |

## Why most ids are placeholders

The cve-remediation pipeline integrates ~5 production systems (ServiceNow,
Doc+, Nautobot, CMDB, CargoNet) plus ~10 dev/security tools (Fathom,
ansible-lint, kubeval, tflint, syft, batfish, etc.). Today, Harbor ships
exactly one `@tool`-registered callable: `nautilus.broker_request`.
Everything else is intended to land via:

1. **Nautilus adapters** — for system integrations. The broker dispatches
   to existing Nautilus adapters (`servicenow.py`, `neo4j.py`, `pgvector.py`,
   `s3.py`, etc.) via intent payload. The IR doesn't need a separate tool
   id per system — `nautilus.broker_request` plus an intent string is
   the right abstraction.

2. **MCP servers** — for typed tool-call interfaces (TEI embeddings,
   RyuGraph cypher, Redis Reflexion buffer). These are external
   processes Harbor consumes via MCP; the IR tool id is the address.

3. **`@tool`-decorated callables** — for in-process Python tools that
   don't need an external system (Fathom rule eval, hash/sign helpers,
   DSPy module wrappers). These land per-phase as nodes harden and the
   binding gets stable.

The IR's `tools:` block declares **intent**: it tells the runtime what
capabilities the graph expects to use, so the capability gate can fail
fast on missing bindings. Until each placeholder lands, runs that
exercise those nodes will fail loudly at capability resolution — which
is the correct fail-loud behaviour during scaffold.

## Per-IR tool budget (reference)

| IR                                  | broker | other tools | placeholders |
| ----------------------------------- | ------ | ----------- | ------------ |
| `harbor.yaml`                       | 1      | 14          | 13           |
| `phase0/doctrine_ingest.yaml`       | 1      | 3           | 2            |
| `phase6/offline_learning.yaml`      | 1      | 5           | 4            |
| `triggered/drift_watch.yaml`        | 1      | 0           | 0            |
| `triggered/tier_re_eval.yaml`       | 1      | 3           | 2            |
| `triggered/audit_anchor.yaml`       | 1      | 3           | 2            |
| `triggered/lab_leak_reaper.yaml`    | 1      | 1           | 0            |
| `triggered/rolling_restart.yaml`    | 1      | 4           | 3            |
