# cve_remediation — runbook

Three fidelity levels, each progressively closer to production. Pick the
lowest level that proves what you need; only escalate when you hit a real
gap.

| Level | Network | Backends | Broker | ServiceNow | What it proves |
|-------|---------|----------|--------|------------|----------------|
| 1 — Offline simulate | none | none | offline envelopes | dry-run | IR shape, rule firings, real-node behavior, pack signing, intent payloads |
| 2 — In-process pipeline | none | none | offline | dry-run | Full Phase 0–6 + 5 triggered subgraphs run end-to-end (status=done) via in-process FastAPI |
| 3 — Live broker | localhost backends | docker-compose | live Nautilus | dry-run by default, live opt-in | Real `Broker.arequest`, signed Ed25519 attestations, audit chain, ServiceNow read roundtrip; CR creation under explicit env opt-in |

---

## Level 1 — Offline simulate + unit tests (zero deps)

```bash
# Per-IR simulate (no node execution; rule-firing trace only)
uv run --no-project harbor run demos/cve_remediation/graph/harbor.yaml --inspect

# Walk every IR through Graph.simulate
uv run --no-project python -m demos.cve_remediation.run_demo

# Full unit + demo test suite
uv run --no-project pytest demos/cve_remediation/graph/tests --no-cov

# Pack signing round-trip (Ed25519 + JWT)
uv run --no-project python -m demos.cve_remediation.sign_packs
```

**What it proves:** IRs validate, rules fire deterministically, real
nodes' state-deltas match expected behavior, packs sign + tamper-detect.

---

## Level 2 — In-process FastAPI smoke (still no deps)

Boots the Harbor serve app via `httpx.ASGITransport` (no uvicorn, no port
bind, no docker), pre-registers every demo IR, drives the main pipeline
through HITL gates with auto-approve, asserts `status=done`. This is the
demo's CI gate.

```bash
uv run --no-project python -m demos.cve_remediation.live_test --json
```

Drive a different graph or override the seed CVE:

```bash
uv run --no-project python -m demos.cve_remediation.live_test \
  --graph graph:cve-rem-doctrine-ingest

uv run --no-project python -m demos.cve_remediation.live_test \
  --cve-id CVE-2024-3094
```

**What it proves:** Phase 0–6 + all 5 triggered subgraphs execute
end-to-end. Scheduler dispatches real `GraphRun` (no synthetic POC
summary). SubGraphNode mounts; broker intents emit offline envelopes;
artifact writes land in `<artifact_root>/<blake3>.json`.

**What it does NOT prove:** real broker dispatch, real ServiceNow CR
creation, real RyuGraph / pgvector queries.

---

## Level 3 — Live broker + backends

Two opt-in toggles. Each layer adds real network IO; each is independent
of the others.

> **Use the wrapper.** Markdown code blocks mangle backslash-newline
> continuation; bash sees `\<space><newline>` and you get `: command
> not found`. The wrapper at
> `demos/cve_remediation/scripts/run_live.sh` sources `.env`, sets
> `HARBOR_CONFIG_DIR`, applies toggles, and forwards everything else to
> `harbor run`. Use it instead of typing the env-prefixed commands by
> hand.
>
> ```bash
> ./demos/cve_remediation/scripts/run_live.sh --help
> ./demos/cve_remediation/scripts/run_live.sh                            # offline
> ./demos/cve_remediation/scripts/run_live.sh --live-broker              # 3a
> ./demos/cve_remediation/scripts/run_live.sh --live-broker --live-sn    # 3a + 3c
> ./demos/cve_remediation/scripts/run_live.sh --cve-id CVE-2024-3094 --live-broker
> ./demos/cve_remediation/scripts/run_live.sh --live-broker -- --log-file /tmp/run.jsonl
> ```
>
> Pass-through args after `--` go straight to `harbor run`.

### 3a — `--live-broker` (read-only Nautilus)

Engages the lifespan-singleton `nautilus.Broker`, signs every request
with Ed25519, writes the audit chain to `${NAUTILUS_AUDIT_PATH}`. The
broker adapter surface is **read-only** (`HTTP GET` only); no writes
escape this layer.

**Prereqs:**

1. `.env` — populated with backend endpoints + auth. See `.env` in this
   directory; required keys:

   ```
   SERVICENOW_BASE_URL, SERVICENOW_USERNAME, SERVICENOW_PASSWORD
   PGVECTOR_DSN, POSTGRES_DSN, RYUGRAPH_URL
   NAUTILUS_AUDIT_PATH, NAUTILUS_API_KEY
   ```

2. `nautilus.yaml` — at `demos/cve_remediation/nautilus.yaml`. Already
   wired against the demo's 4 sources (servicenow / pgvector /
   threat_graph / audit_store).

3. **Source classifications/purposes** in `nautilus.yaml` MUST be
   compatible with the agent's clearance + per-intent purposes injected
   by `_dispatch_intent`. See `_INTENT_PURPOSE_OVERRIDES` in
   `graph/real_nodes.py`.

**Run (CLI, blocking, offline ServiceNow):**

```bash
./demos/cve_remediation/scripts/run_live.sh --live-broker
```

**Run (in-process FastAPI smoke -- broker engaged):**

```bash
set -a; source demos/cve_remediation/.env; set +a
HARBOR_CONFIG_DIR=$(pwd)/demos/cve_remediation CVE_REM_LIVE_BROKER=1 uv run --no-project python -m demos.cve_remediation.live_test --json
```

**Verify:**

```bash
# Audit chain populated; one entry per broker call.
wc -l .harbor/nautilus-audit.jsonl

# Inspect routing decisions per intent.
uv run --no-project python -c "
import json
for line in open('.harbor/nautilus-audit.jsonl'):
    d = json.loads(json.loads(line)['metadata']['nautilus_audit_entry'])
    q = d.get('sources_queried', [])
    e = [er.get('source_id') for er in d.get('error_records', [])]
    print(f'{d[\"raw_intent\"]}: queried={q} errored={e}')
"
```

### 3b — Backends via docker-compose

Default services (always up): postgres, pgvector, redis, mock-servicenow,
llm-shim. The graph-store backing for Nautilus's `threat_graph` source is
**neo4j community** under the `full` profile (Nautilus's neo4j adapter
speaks bolt; RyuGraph is embedded-library-only and ships no daemon).
Harbor's own `RyuGraphStore` keeps using the embedded driver — the
neo4j container is broker-side only.

```bash
# Default stack (no neo4j)
docker compose -f demos/cve_remediation/docker-compose.yml up -d

# Full stack including neo4j (required for live ``threat_graph`` reads)
docker compose -f demos/cve_remediation/docker-compose.yml --profile full up -d

# Wait for health
docker compose -f demos/cve_remediation/docker-compose.yml ps

# Tear down (drops volumes)
docker compose -f demos/cve_remediation/docker-compose.yml --profile full down -v
```

With `--profile full` up, level-3a runs satisfy `threat_graph` reads (no
more `Couldn't connect to localhost:7687` in audit `error_records`).
First-run query against an empty Neo4j returns a `label X does not
exist` *warning* (not an error) until the demo schema-seed lands as a
follow-up task.

### 3c — Live ServiceNow writes (HARBOR_SERVICENOW_LIVE)

The Phase 4 `CreateChangeRequestNode` calls
`harbor.tools.servicenow.create_change_request`. The tool is **dry-run
by default** — only `HARBOR_SERVICENOW_LIVE=1` flips it to a real POST
against `${SERVICENOW_BASE_URL}/api/now/table/change_request`.

**Before enabling live writes:**

- [ ] `${SERVICENOW_BASE_URL}` points at a developer / sandbox PDI
      (`venXXXXX.service-now.com`), NOT a production instance.
- [ ] `${SERVICENOW_USERNAME}` is a least-privilege account scoped to
      `change_request` writes only.
- [ ] You understand that every demo run will create one real CR per
      pipeline pass. The deterministic `cr_correlation_id` (sha256 of
      `cve_id|plan_hash`) lets ServiceNow's correlation-dedupe absorb
      retries when the matching system property is wired; without that
      property each retry creates a duplicate.
- [ ] You have a rollback plan for closing or deleting test CRs.

**Run:**

```bash
./demos/cve_remediation/scripts/run_live.sh --live-broker --live-sn
```

The wrapper prints a banner highlighting that ServiceNow is in LIVE
mode so an operator can Ctrl-C if they didn't mean it.

**Verify CR creation:**

```bash
# correlation_id (sha256 prefix) carried through to ServiceNow
curl -u "${SERVICENOW_USERNAME}:${SERVICENOW_PASSWORD}" \
  "${SERVICENOW_BASE_URL}api/now/table/change_request?\
sysparm_query=correlation_id=$(python -c '
import hashlib,sys
print(\"CR-\"+hashlib.sha256(\"CVE-2021-44228|\".encode()).hexdigest()[:12].upper())
')&sysparm_limit=1"
```

---

## Environment variable reference

| Var | Where read | Default | Purpose |
|-----|-----------|---------|---------|
| `HARBOR_CONFIG_DIR` | `harbor.serve.lifecycle.resolve_config_dir` | `~/.config/harbor/` | Where `nautilus.yaml` is loaded from. |
| `CVE_REM_LIVE_BROKER` | `_dispatch_intent` in `graph/real_nodes.py` | unset | Flip demo nodes from offline envelopes to `Broker.arequest`. |
| `HARBOR_SERVICENOW_LIVE` | `harbor.tools.servicenow.create_change_request` | unset | Flip the SN tool from dry-run to real POST. |
| `SERVICENOW_BASE_URL` | SN tool | — | `https://venXXXXX.service-now.com/`. |
| `SERVICENOW_AUTH_KIND` | SN tool | `basic` | One of `basic` / `bearer` (mtls deferred to v2). |
| `SERVICENOW_USERNAME` / `_PASSWORD` | SN tool (basic) | — | Least-privilege CR-creation account. |
| `SERVICENOW_BEARER_TOKEN` | SN tool (bearer) | — | OAuth/PAT bearer when `AUTH_KIND=bearer`. |
| `NAUTILUS_AUDIT_PATH` | `nautilus.yaml` | `./.harbor/nautilus-audit.jsonl` | Where Nautilus appends signed audit entries. |
| `NAUTILUS_API_KEY` | `nautilus.yaml` | — | API key gate for the broker's HTTP surface. |

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| `harbor run --live-broker` exits with `ConfigError: Each source entry must have a string 'id'` | `nautilus.yaml` schema drift (Nautilus 0.1.5 expects `id`/`type`) | Confirm `nautilus.yaml` matches the canonical schema in `nautilus/examples/full-showcase/nautilus.yaml`. |
| Audit log empty even with `CVE_REM_LIVE_BROKER=1 --live-broker` | broker_lifespan engaged AFTER scheduler.start() — worker tasks didn't inherit ContextVar | Make sure `broker_lifespan` enters before `scheduler.start()` (live_test.py already does). |
| Every audit entry shows `decision: deny` with `deny-purpose-mismatch` | `_dispatch_intent` not injecting `purpose` into context, or source's `allowed_purposes` doesn't list the demo's purpose | Verify `_INTENT_PURPOSE_OVERRIDES` covers the intent and `nautilus.yaml`'s `allowed_purposes` includes that value. |
| Audit shows `clearance does not dominate source classification` | Agent clearance + source classification not on Nautilus's primary lattice (`unclassified < cui < confidential < secret < top-secret`) | Use canonical levels — `cui-basic` is NOT a primary level (it's CUI-sub). |
| `threat_graph` always errors with `Couldn't connect to localhost:7687` | RyuGraph not running locally | `docker compose -f demos/cve_remediation/docker-compose.yml up -d ryugraph`. |
| `servicenow` errors with `invalid table None` | Source missing `table:` field in `nautilus.yaml` | Add `table: change_request` (or whatever target table). |
| `HARBOR_SERVICENOW_LIVE=1` set but tool still dry-runs | Tool reads env at call time; if env not propagated to `harbor run` (e.g. via `set -a; source .env; set +a`), it stays unset | Confirm `printenv HARBOR_SERVICENOW_LIVE` is `1` in the same shell that runs `harbor run`. |
| Real CR not created despite `status=ok` from tool | ServiceNow returned 2xx but the body lacked `result.sys_id` | Inspect `state.servicenow_response.result` — likely a permission scope problem on the SN account. |
| `cr_lifecycle_states` only contains `["assess"]` after a live `--live-sn` run | The PDI Change Model gates `assess→authorize` on a real approval-by-assignment-group record. The pipeline does NOT forge approvals -- it walks as far as the workflow permits. | Either configure your PDI's Standard Change template as pre-approved, or post a real approval (sysapproval_approver state=approved) before the `authorize` transition fires. |
| `cr_status` reads `rejected` after the run | `CVE_REM_HITL_DECISION` defaulted to `block` because `--live-broker` was on; without an external response the gate never produced an approve. | Set `CVE_REM_HITL_DECISION=approve` (or POST a real respond) when running with `--live-broker`. |

---

## Quick smoke (CI gate)

```bash
# Should always pass on a clean checkout.
uv run --no-project pytest demos/cve_remediation tests/unit -q --no-cov
uv run --no-project python -m demos.cve_remediation.live_test --json
```

If either fails on `main`, it's a real regression.
