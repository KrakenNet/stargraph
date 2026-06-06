# Stargraph — Markdown Skill Format (`stargraph-md-skills`)

**Status:** Proposal v0.1
**Audience:** Plugin authors, Stargraph core developers, would-be skill authors who don't want to write Python
**Stability:** Extension over the stable Plugin API; can ship as a separate plugin without core changes

---

## 1. Motivation

Stargraph's `stargraph.skills.Skill` is a typed Pydantic model with a `state_schema`,
versioned tool ids, and a runnable IR sub-graph. That's the right contract for
the runtime — it's strict enough that the engine can validate the boundary
write-set and replay deterministically.

But the developer experience is heavy. Authoring a skill today means:

1. Writing a Pydantic `state_schema`.
2. Writing or generating an IR `subgraph` document.
3. Wiring tool ids by `<namespace>.<name>@<version>`.
4. Packaging it as a Python distribution with a `stargraph_plugin` entry point.

Claude Code, Cursor, and other coding-agent ecosystems have settled on a much
lighter format: a single `SKILL.md` file with YAML frontmatter and a markdown
body. Authors love it. The tradeoff is that the runtime can't enforce a
boundary contract because there isn't one to read.

`stargraph-md-skills` resolves the tradeoff: keep the strict typed surface
internally, but expose a markdown authoring format that compiles into it.

---

## 2. Layered model

```
SKILL.md  (author writes)
   │
   ▼
stargraph-md-skills compiler
   │
   ├── parses YAML frontmatter           -> Skill metadata
   ├── extracts tool references          -> tools: list[str]
   ├── extracts state schema             -> state_schema: type[BaseModel]
   ├── extracts examples                 -> examples: list[Example]
   ├── compiles markdown body            -> system_prompt
   └── (optional) compiles IR sub-graph  -> subgraph
   │
   ▼
stargraph.skills.Skill   (typed runtime contract)
   │
   ▼
register_skills() hookspec   (existing pluggy entry point)
```

The compiler is the only new code. Everything downstream of it is the
existing typed plugin path — replay, governance, capabilities, audit chain
all light up unchanged.

---

## 3. SKILL.md format

```markdown
---
name: refund-handler
version: 1.4.0
kind: agent                 # agent | workflow | utility
description: |
  Handle a customer refund request end-to-end: validates the order,
  checks policy, drafts the response.
requires:
  - billing.read
  - billing.refund
tools:
  - core.search@^1.0
  - billing.lookup_order@^2.1
  - billing.issue_refund@^1.0
state_schema:
  decision: { type: string, enum: [approve, deny, escalate] }
  amount_cents: { type: integer, minimum: 0 }
  reason: { type: string }
examples:
  - inputs:
      order_id: "ORD-12345"
      reason_code: "shipping_delay"
    expected_output:
      decision: "approve"
      amount_cents: 5400
---

# Refund handler

You are the refund decisioner for ACME Inc. Follow this exact procedure...

## When to approve

Approve automatically when ALL of the following hold:
- Order is within the 30-day return window
- Total refund amount is under $100
- Customer has no prior policy abuse flags

## When to escalate

Escalate to a human reviewer when ...
```

### 3.1 Frontmatter rules

- `name`, `version`, `kind`, `description` — required, mirror `Skill` model.
- `requires` — list of capability strings; checked at registration.
- `tools` — list of `<namespace>.<name>@<semver-range>` ids; resolved against the registered `ToolSpec` set.
- `state_schema` — JSON Schema fragment; the compiler synthesizes a Pydantic model with these field names as the engine's declared write-whitelist (`SubGraphNode` rejects undeclared writes per FR-23).
- `examples` — typed few-shots; surfaced to the DSPy adapter and to documentation generators.
- `subgraph` (optional) — if present, must point at an IR document path; absent means the skill ships as a single-step React subgraph.

### 3.2 Body rules

The markdown body becomes `Skill.system_prompt`. The compiler:

1. Strips frontmatter.
2. Inlines any `{{ tool_descriptions }}` template references using registered ToolSpec descriptions.
3. Refuses any `<script>` / raw HTML (XSS-equivalent — the prompt is data, not code).
4. Hashes the final string into the skill's content-addressable id (skills are content-addressable per `stargraph.skills.base` — same identity rule as today, just computed from a different source).

---

## 4. Compiler implementation

Lives at `stargraph.skills.md_compile` (or shipped as the standalone
`stargraph-md-skills` plugin). Public surface:

```python
def compile_md(path: Path) -> Skill: ...
def compile_directory(root: Path) -> list[Skill]: ...
```

A reference plugin manifest using it:

```python
# my_skills/manifest.py
from importlib.resources import files
from stargraph.plugin import PluginManifest, hookimpl
from stargraph.skills.md_compile import compile_directory


def stargraph_plugin() -> PluginManifest:
    return PluginManifest(
        name="my_skills",
        api_version="1.x",
        order=100,
        namespaces=["my_skills"],
    )


@hookimpl
def register_skills():
    skills_dir = files("my_skills") / "skills"
    return compile_directory(Path(str(skills_dir)))
```

---

## 5. Authoring directory layout

```
my_skills/
  pyproject.toml                          -- declares stargraph_plugin entry point
  src/
    my_skills/
      __init__.py
      manifest.py                         -- (above)
      skills/
        refund-handler/
          SKILL.md                        -- the skill
          subgraph.ir.json                -- (optional) typed IR sub-graph
          examples/                       -- (optional) golden test inputs
            01_under_threshold.json
            02_escalation.json
        cancel-order/
          SKILL.md
        ...
      tools/                              -- ordinary @tool callables
        billing.py
```

A pure-skills plugin needs only the `skills/` tree; no Python code beyond
the manifest stub.

---

## 6. Validation surface

`stargraph skills compile <path>` (CLI subcommand contributed by this plugin)
runs the same validation the loader will run at plugin discovery time:

- YAML frontmatter parses and matches the schema.
- Every referenced tool id resolves under the current Tool registry.
- Every capability in `requires` is a known capability string.
- `state_schema` is a valid JSON Schema (Draft 7 subset, same as IR).
- Markdown body is HTML-injection-clean.
- Examples (if present) round-trip through `state_schema`.

Failure exits non-zero with the same error envelope the loader uses, so the
same skill compiles identically in dev and at plugin-load time.

---

## 7. Comparison to Claude Code SKILL.md

| | Claude Code | `stargraph-md-skills` |
|---|---|---|
| Frontmatter | `name`, `description`, optional fields | typed schema mirroring `Skill` |
| Body | Markdown prose | Same; becomes `system_prompt` |
| Tools | Mentioned in prose | Typed `<ns>.<name>@<semver>` ids, resolved at compile |
| State contract | None | Required `state_schema` (JSON Schema fragment) |
| Activation | LLM reads description | LLM + capability gate + state-schema enforcement |
| Replay | None | Inherits Stargraph's deterministic-replay contract |
| Distribution | Filesystem dir | pip-installable Python package (or via `stargraph-dir-plugins`, see sibling doc) |

Authors get the lightweight DX. The runtime keeps every guarantee.

---

## 8. Out of scope (v0.1)

- Hot-reload of markdown skills without restart. (Possible later via the
  same Fathom rules-reload mechanism applied to skills.)
- Inline tool definitions in SKILL.md. Tools remain Python callables;
  markdown skills only **reference** them.
- Multi-file markdown skills (split system prompt across files). Single
  file is the format.

---

## 9. Open questions

- Should the JSON-Schema state fragment support `$ref` to shared schemas
  in the same package?
- Does the compiler emit the schema's content-hash into a sidecar
  `SKILL.lock` so reviewers can diff schema changes without re-running
  `compile_md`?
- Should we ship a `stargraph skills new` scaffolder that drops a SKILL.md
  template + tests?
