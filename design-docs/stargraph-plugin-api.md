# Stargraph — Plugin / Skill / Tool API Specification

**Status:** Draft v0.1
**Audience:** Plugin authors, Stargraph core developers
**Stability:** Treat the **public surface** below as a stable contract once 1.0 ships. Breaking changes require a new major version.

---

## 1. Layered model

```
Plugin           pip-installable package; the distribution unit
  └── Skill     bundle: tools + optional sub-graph + optional system prompt
       └── Tool typed callable
  └── Node     custom node type (rare)
  └── Store    provider implementation (e.g., new vector DB)
  └── Pack     governance rule pack (Bosun-compatible)
```

A plugin may ship any combination of skills, tools, nodes, stores, and packs.

---

## 2. Tool spec

Tools are the smallest unit. Defined as decorated callables or class instances.

### 2.1 Required fields

| Field | Type | Purpose |
|---|---|---|
| `name` | str | Unique within namespace |
| `namespace` | str | Dotted (e.g., `web`, `kraken.servicenow`) |
| `version` | semver | Independent of plugin version |
| `description` | str | Human-readable; used by AI authoring agents |
| `input_schema` | JSON Schema | Validated at call time |
| `output_schema` | JSON Schema | Validated at return time |
| `side_effects` | enum: `none` \| `read` \| `write` \| `external` | Drives governance |
| `permissions` | list[str] | Required capability strings (e.g., `net.fetch`) |

### 2.2 Optional fields

| Field | Purpose |
|---|---|
| `idempotency_key` | Function returning a string for de-dup |
| `cost_estimate` | Function returning a `Cost` (tokens, time, money) |
| `examples` | List of input/output examples for AI authoring + tests |
| `tags` | Discoverability |
| `deprecated` | Replacement pointer |

### 2.3 Reference

```python
from stargraph import tool
from pydantic import BaseModel

class SearchArgs(BaseModel):
    query: str
    k: int = 5

class SearchHit(BaseModel):
    url: str
    snippet: str

@tool(
    namespace="web",
    side_effects="read",
    permissions=["net.fetch"],
    examples=[
        {"input": {"query": "stargraph framework"}, "output": [{"url": "...", "snippet": "..."}]},
    ],
)
async def search(args: SearchArgs) -> list[SearchHit]:
    """Search the web. Returns ranked hits."""
    ...
```

### 2.4 Wire form (IR)

Tools serialize to a manifest entry. This is what the no-code UI and AI authoring agents see.

```yaml
tool:
  name: search
  namespace: web
  version: 1.0.0
  description: Search the web. Returns ranked hits.
  input_schema: { ... JSON Schema ... }
  output_schema: { ... JSON Schema ... }
  side_effects: read
  permissions: [net.fetch]
```

---

## 3. Skill spec

A skill is a packaged unit of capability. AI agents and humans compose graphs from skills.

### 3.1 Required fields

| Field | Type | Purpose |
|---|---|---|
| `name` | str | Plugin-namespaced (e.g., `stargraph/research`) |
| `version` | semver | Skill-level versioning |
| `description` | str | What the skill does, in one sentence |
| `kind` | enum: `agent` \| `workflow` \| `utility` | Intent signal |

### 3.2 Optional fields

| Field | Purpose |
|---|---|
| `tools` | List of tool references the skill uses |
| `subgraph` | Path to a YAML graph definition |
| `system_prompt` | Default system text for agent-kind skills |
| `state_schema` | Default state schema (overridable) |
| `requires` | Other skills/tools/stores |
| `examples` | Sample invocations for tests + AI authoring |

### 3.3 Reference

```python
from stargraph import skill

@skill(
    name="stargraph/research",
    kind="agent",
    description="Iteratively researches a topic using web search and arXiv.",
    tools=["web.search", "web.fetch", "arxiv.search"],
    subgraph="graphs/research.yaml",
)
class ResearchAgent:
    system_prompt = "You are a research agent..."
```

---

## 4. Plugin packaging

Plugins are pip-installable Python packages declaring entry points.

### 4.1 `pyproject.toml`

```toml
[project]
name = "stargraph-research"
version = "0.3.1"

[project.entry-points."stargraph.skills"]
research = "stargraph_research:ResearchAgent"

[project.entry-points."stargraph.tools"]
"web.search" = "stargraph_research.tools:search"
"web.fetch"  = "stargraph_research.tools:fetch"
"arxiv.search" = "stargraph_research.tools:arxiv_search"

[project.entry-points."stargraph.stores"]
"vector.qdrant" = "stargraph_research.stores:QdrantVectorStore"

[project.entry-points."stargraph.packs"]
"bosun:research-budgets" = "stargraph_research.packs:research_budgets"
```

### 4.2 Manifest

Every plugin must export `stargraph_plugin` returning a `PluginManifest`:

```python
def stargraph_plugin() -> PluginManifest:
    return PluginManifest(
        name="stargraph-research",
        version="0.3.1",
        api_version="1",                 # Stargraph plugin API version
        namespaces=["web", "arxiv"],     # claimed tool namespaces
        provides=["skills", "tools", "stores", "packs"],
    )
```

The runtime validates manifests at install. Conflicts on namespaces fail fast.

---

## 5. Discovery / Registry API

Used by Stargraph itself, by AI authoring agents, by the planned no-code UI, and by harness integrations.

```python
from stargraph.registry import registry

registry.list_skills()              # → list[SkillManifest]
registry.list_tools(namespace="web")
registry.get_tool("web.search")
registry.search_skills("research")  # full-text over descriptions/tags
registry.compatible_with(graph)     # filters by required permissions/stores
```

All registry returns are JSON-serializable so the same surface drives the UI and chat-agent authoring.

---

## 6. Versioning rules

- **SemVer** across the board: tools, skills, plugins, packs
- Tool input/output schema changes that remove or retype fields are **major**
- Adding optional input fields, examples, tags, deprecation pointers is **minor**
- Internal refactors are **patch**
- Plugins declare `api_version` (Stargraph plugin API). Stargraph refuses to load mismatched majors

---

## 7. Permissions model

Permissions are capability strings, namespace-rooted:

| Capability | Meaning |
|---|---|
| `net.fetch` | Outbound HTTP |
| `net.bind` | Listen on a port |
| `fs.read.<path>` | Read filesystem path |
| `fs.write.<path>` | Write filesystem path |
| `subprocess` | Spawn processes |
| `db.<store>` | Access a configured store |
| `secrets.<key>` | Read a named secret |

Bosun packs can require/grant capabilities. Cleared deployments default-deny.

---

## 8. Side-effect declarations

Used by governance and counterfactual replay.

| Value | Meaning | Replay safe? |
|---|---|---|
| `none` | Pure function | yes |
| `read` | Reads external state but doesn't mutate | yes |
| `write` | Mutates external state (DB, file, KV) | no — must be stubbed |
| `external` | Calls third-party services with billing/rate effects | no — must be stubbed |

The replay engine refuses to re-execute `write` or `external` tools without an explicit policy.

---

## 9. AI authoring contract

Tools, skills, and stores must produce metadata sufficient for an AI agent to compose them into a graph without reading source. This means:

- `description` is the spec, not boilerplate
- `examples` are required for `kind=agent` skills and recommended for tools
- Schemas are the single source of truth for argument shapes
- No "magic" that requires reading Python

This is what enables the future chat-agent UI and the AI-harness plugins (Claude Code, Cursor, etc.) to build Stargraph graphs reliably.

---

## 10. Anti-patterns

- **Tools that perform multiple actions.** Split them; rules and budgets reason per call
- **Skills that hide tools.** All used tools must be declared in `tools=` so governance can see them
- **Plugins that monkey-patch the runtime.** Use entry points; if it can't be done that way, file an issue
- **Tool names without namespaces.** Always namespace, even for app-internal tools
- **Side effects declared as `none` to avoid governance.** This is a security violation, not a shortcut
