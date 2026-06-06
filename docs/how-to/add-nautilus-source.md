# How to Add a Nautilus Broker Source

## Goal

Wire a Nautilus broker source into a Stargraph graph so a `BrokerNode` (or
the `nautilus.broker_request@1` tool) can dispatch agent intents
through the broker, with the `tools:broker_request` capability gate and
provenance envelope wired end-to-end.

## Prerequisites

- Stargraph installed (`pip install stargraph>=0.2`) — `nautilus-rkm>=0.1.5`
  is a core dependency.
- A Nautilus broker config (`nautilus.yaml`) with at least one source
  declared. See [Nautilus broker integration](../serve/nautilus.md) for
  the broker-side specification.
- The `tools:broker_request` capability granted to the deployment.

## Steps

### 1. Author `nautilus.yaml`

The broker is composition-only — Stargraph imports `nautilus` lazily and
expects a `nautilus.yaml` co-located with the deployment to declare
sources. Use the `/nautilus new-source` skill to scaffold a source after
an interview, then validate via the broker's own `/nautilus sources`
listing.

```yaml
# nautilus.yaml (broker-side config — owned by Nautilus, consumed by Stargraph)
version: "1.0"
sources:
  - id: kb_facts
    adapter: nautilus.adapters.fact_store
    config:
      dsn: "sqlite:///./kb_facts.db"
    cost_cap_per_request: 0.05
    purposes: ["agent_research", "alert_triage"]
```

See the [Nautilus broker docs](../serve/nautilus.md) for the canonical
adapter list (eight built-in adapters: ServiceNow, Nautobot, ...).

**Verify:** `/nautilus sources` (the bundled CLI skill) lists your new
source as enabled.

### 2. Grant the capability

```python
# my_app/_caps.py
from stargraph.security import Capabilities, CapabilityClaim


CAPS = Capabilities(
    claims=frozenset({
        CapabilityClaim(namespace="tools", scope="broker_request"),
    }),
)
```

The bundled
[`broker_request`](https://github.com/KrakenNet/stargraph/blob/main/src/stargraph/tools/nautilus/broker_request.py)
tool declares `requires_capability="tools:broker_request"`. Without
this claim, the tool dispatcher raises `CapabilityError` before the
broker is touched.

### 3. Wire `BrokerNode` into a graph

The graph-node form is one fixed slot per call:

```yaml
# stargraph.yaml
ir_version: "1.0.0"
id: "graph:agent.research"

state_class: "my_app.state:ResearchState"

nodes:
  - id: ask_broker
    kind: broker
    config:
      agent_id_field: "agent_id"
      intent_field: "intent"
      output_field: "broker_response"
```

`BrokerNodeConfig` enforces three string fields:

- `agent_id_field` — state attribute holding the requesting agent's id.
- `intent_field` — state attribute holding the free-text intent.
- `output_field` — state key the response is patched into.

The patched payload is `BrokerResponse.model_dump(mode="json")` plus a
`__stargraph_provenance__` envelope:

```json
{
  "data": "...",
  "sources_queried": ["kb_facts"],
  "sources_denied": [],
  "attestation": "<JWS token>",
  "__stargraph_provenance__": {
    "origin": "tool",
    "source": "nautilus",
    "external_id": "<broker request_id>"
  }
}
```

### 4. Or, use the tool form from a skill

For ReAct skills and dynamic dispatch, call the same broker from the
[`nautilus.broker_request@1`](../serve/nautilus.md) tool:

```python
# inside a tool_impls table for a ReactSkill
from stargraph.tools.nautilus.broker_request import broker_request

TRIAGE = ReactSkill(
    name="triage",
    version="0.1.0",
    description="Query broker for context.",
    tools=["nautilus.broker_request@1"],
    tool_impls={"nautilus.broker_request": broker_request},
    ...
)
```

The tool returns the same shape the node patches into state.

### 5. Run the graph under `stargraph serve`

The broker is a lifespan-singleton — `stargraph serve` loads `nautilus.yaml`
at startup and stores the `Broker` on the FastAPI app's
[`current_broker`][contextvars] context var. The bundled `BrokerNode`
and `broker_request` tool both resolve through that context var; if no
lifespan is active they raise `StargraphRuntimeError`.

```bash
stargraph serve --config ./stargraph-serve.yaml
# In another shell:
curl -X POST http://localhost:8000/v1/runs \
    -H "Content-Type: application/json" \
    -d '{"graph_id": "graph:agent.research", "params": {"agent_id": "agent-1", "intent": "find recent CVEs"}}'
```

## Wire it up

`nautilus.yaml` lives **alongside** Stargraph's serve config — the broker
package owns the schema. Stargraph's only configuration surface is:

1. Declaring `kind: broker` nodes (or `nautilus.broker_request@1` tool
   ids) in your IR documents.
2. Granting `tools:broker_request` to the running deployment.
3. Ensuring `nautilus-rkm` is installed (it's a core Stargraph dependency).

There is no `stargraph.brokers` entry-point group — broker source
plug-and-play is a Nautilus concern, not a Stargraph concern.

## Verify

```bash
stargraph run ./stargraph.yaml \
    --inputs agent_id="agent-1" \
    --inputs intent="find recent CVEs"
```

Expected end-of-run output:

```
run_id=<uuid> status=done
```

Inspect the broker call:

```bash
stargraph inspect <run_id> --db .stargraph/run.sqlite
# Look for the broker tool_call event with the request_id and provenance envelope.
```

Verify the JWS attestation against the broker pubkey via
`/nautilus verify-attestation`.

## Troubleshooting

!!! warning "Common failure modes"
    - **`StargraphRuntimeError: no current Broker`** — the run is not
      executing inside a `stargraph serve` lifespan. Either wire the broker
      manually in your launcher or run via `stargraph serve`.
    - **`CapabilityError: tools:broker_request not granted`** — the
      deployment is missing the capability claim. Add it to your
      `Capabilities` instance.
    - **Broker `sources_denied` non-empty** — Nautilus's policy engine
      refused the source. Check `/nautilus audit-tail` for the denied
      reason; usually a missing purpose or compartment.
    - **Replay mismatch** — `BrokerNode.side_effects = read`, so
      replay re-executes by default. Pin `replay_policy=must_stub` if
      determinism matters across replays of remote-data calls.

## See also

- [Nautilus broker integration](../serve/nautilus.md) — broker-side
  config + adapter list.
- [`BrokerNode`](../reference/nodes/index.md) — graph-node reference.
- [`broker_request` tool source](https://github.com/KrakenNet/stargraph/blob/main/src/stargraph/tools/nautilus/broker_request.py).
- [`BrokerNode` source](https://github.com/KrakenNet/stargraph/blob/main/src/stargraph/nodes/nautilus/broker_node.py).
- [`stargraph.security.Capabilities`](https://github.com/KrakenNet/stargraph/blob/main/src/stargraph/security/capabilities.py).

[contextvars]: https://github.com/KrakenNet/stargraph/blob/main/src/stargraph/serve/contextvars.py
