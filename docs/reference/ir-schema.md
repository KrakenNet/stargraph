# IR Schema Reference

The Stargraph intermediate representation (IR) is a portable, JSON-serializable
description of an executable graph plus its rules, tools, skills, stores,
governance packs, and migration metadata. Every IR Pydantic type subclasses
[`IRBase`](#irbase), which pins `extra='forbid'` so unknown keys are rejected
at load time (FR-6, AC-9.1).

The canonical entry points live in `stargraph.ir`:

| Symbol                        | Purpose                                                                  |
| ----------------------------- | ------------------------------------------------------------------------ |
| `IRDocument`                  | Root document model.                                                     |
| `dumps(ir, *, hashable=False)`| Canonical JSON serialization (FR-15, AC-11.4). Compact, ASCII-safe.      |
| `dumps_canonical`             | `partial(dumps, hashable=True)`: sort keys for content-addressable hash. |
| `loads(text, model=IRDocument)` | Parse JSON to an `IRBase` subclass via `model_validate_json`.          |
| `validate(ir)`                | Eager structured validation; returns `list[ValidationError]`, never raises. |
| `STARGRAPH_IR_VERSION`           | The `MAJOR.MINOR.PATCH` IR version this Stargraph build understands.        |

!!! info "IR is the wire format"
    Every persisted graph (Bosun packs, sample fixtures, replay payloads)
    round-trips through `dumps` / `loads`. Hashes used for `graph_hash`,
    pack identity, and counterfactual derivation are computed against
    `dumps_canonical` output -- key order matters.

---

## `IRBase`

Parent class for every Stargraph IR Pydantic model. Subclasses inherit
`extra='forbid'` so unknown keys raise on `model_validate`. This is the
single place to wire any future portable-subset config knob.

| Field          | Type   | Required | Default    | Description                                            |
| -------------- | ------ | -------- | ---------- | ------------------------------------------------------ |
| _model_config_ | `dict` | _(meta)_ | `{"extra": "forbid"}` | Pydantic config; not user-facing. |

Per FR-7 / AC-13.1, IR records may not carry `computed_field` or
`model_validator` decorators (cross-language portability constraint).
Validation that needs cross-field context (slug enforcement, version
divergence) lives in [`stargraph.ir._validate`](#validation-gates).

---

## `IRDocument`

Root document. A minimal valid document needs only `ir_version`, `id`,
and `nodes`; every other section defaults to an empty list / dict.

| Field          | Type                  | Required | Default    | Description                                                                |
| -------------- | --------------------- | -------- | ---------- | -------------------------------------------------------------------------- |
| `ir_version`   | `str`                 | yes      | _req._     | `MAJOR.MINOR.PATCH`. Major divergence from `STARGRAPH_IR_VERSION` is rejected.|
| `id`           | `str`                 | yes      | _req._     | Document identifier (free-form `str` in POC).                              |
| `nodes`        | `list[NodeSpec]`      | yes      | _req._     | Graph nodes.                                                               |
| `rules`        | `list[RuleSpec]`      | no       | `[]`       | Top-level rule definitions.                                                |
| `tools`        | `list[ToolRef]`       | no       | `[]`       | Tool references the graph relies on.                                       |
| `skills`       | `list[SkillRef]`      | no       | `[]`       | Skill references.                                                          |
| `stores`       | `list[StoreRef]`      | no       | `[]`       | Store bindings (lightweight refs; canonical record is `StoreSpec`).        |
| `state_schema` | `dict[str, str]`      | no       | `{}`       | Flat primitive map of state field name -> type string.                     |
| `state_class`  | `str \| None`         | no       | `None`     | Optional `module.path:ClassName` reference to an existing Pydantic model. Mutually exclusive with a non-empty `state_schema` (resolved at `Graph` construction, not in IR validation). |
| `parallel`     | `list[ParallelBlock]` | no       | `[]`       | Top-level parallel/join declarations.                                      |
| `governance`   | `list[PackMount]`     | no       | `[]`       | Mounted Bosun rule packs.                                                  |
| `migrate`      | `list[MigrateBlock]`  | no       | `[]`       | Hash-to-hash migration descriptors for resume.                             |

```yaml
ir_version: "1.0.0"
id: "graph:triage"
nodes:
  - { id: "node_a", kind: "echo" }
  - { id: "node_b", kind: "dspy" }
  - { id: "halt",   kind: "halt" }
rules:
  - id: "rule.escalate"
    when: "(severity ?s&:(>= ?s 4))"
    then:
      - { kind: "goto", target: "node_b" }
state_schema:
  message: "str"
  severity: "int"
governance:
  - id: "pack.bosun.routing"
    version: "1.0.0"
    requires: { stargraph_facts_version: "1.0", api_version: "1" }
```

---

## `NodeSpec`

A graph node. Phase 1 carries only `id` and `kind`; later phases extend
the surface with IO and config blocks.

| Field   | Type  | Required | Default | Description                                                                  |
| ------- | ----- | -------- | ------- | ---------------------------------------------------------------------------- |
| `id`    | `str` | yes      | _req._  | Stable slug (see [ID generation](#id-generation)).                           |
| `kind`  | `str` | yes      | _req._  | Node factory key (`echo`, `halt`, `dspy`) or `module.path:ClassName`.        |

```yaml
- id: "classify"
  kind: "stargraph_extra.nodes:DSPyClassifyNode"
```

---

## Edges and routing

Stargraph's IR has no explicit `EdgeSpec` model. Routing is expressed two ways:

1. **Static fall-through** -- the engine walks `nodes` in declaration order
   when no rule fires.
2. **Rule-driven transitions** -- a [`RuleSpec`](#rulespec) emits one or more
   [actions](#actions) (e.g. `goto`, `parallel`, `interrupt`) that select the
   next node or terminate the run.

<!-- TODO: verify whether a future EdgeSpec / typed transition model is on the IR roadmap. -->

### `RuleSpec`

A single rule (POC: id + `when` pattern + `then` action list).

| Field   | Type           | Required | Default | Description                                                                    |
| ------- | -------------- | -------- | ------- | ------------------------------------------------------------------------------ |
| `id`    | `str`          | yes      | _req._  | Stable slug (see [ID generation](#id-generation)).                             |
| `when`  | `str`          | no       | `""`    | CLIPS-pattern condition string.                                                |
| `then`  | `list[Action]` | no       | `[]`    | Discriminated union of [actions](#actions); FR-11 forbids nested actions.      |

### Actions

`Action` is a discriminated union over `kind`. Per FR-11 it appears only at
the top level of `RuleSpec.then`; variant fields cannot themselves contain
nested actions, which keeps rule semantics inspectable without recursion.

| `kind`      | Class             | Notable fields                                                       |
| ----------- | ----------------- | -------------------------------------------------------------------- |
| `goto`      | `GotoAction`      | `target: str`                                                        |
| `halt`      | `HaltAction`      | `reason: str = ""`                                                   |
| `parallel`  | `ParallelAction`  | `targets: list[str]`, `join: str = ""`, `strategy: str = "all"`      |
| `retry`     | `RetryAction`     | `target: str`, `backoff_ms: int = 0`                                 |
| `assert`    | `AssertAction`    | `fact: str`, `slots: str = ""` (JSON-encoded slot dict)              |
| `retract`   | `RetractAction`   | `pattern: str`                                                       |
| `interrupt` | `InterruptAction` | see [`InterruptAction`](#interruptaction)                            |

#### `InterruptAction`

Pause the run and request human input (FR-81, AC-14.1). Per design §17
Decision #1, dispatch happens on `Action.kind == "interrupt"` **before**
`translate_actions` -- it is a control-flow primitive, not a routing
decision.

| Field                   | Type              | Required | Default | Description                                                                |
| ----------------------- | ----------------- | -------- | ------- | -------------------------------------------------------------------------- |
| `prompt`                | `str`             | yes      | _req._  | Operator-facing prompt surfaced on the `WaitingForInputEvent`.             |
| `interrupt_payload`     | `dict[str, Any]`  | no       | `{}`    | Free-form payload echoed on the wait event.                                |
| `requested_capability`  | `str \| None`     | no       | `None`  | Capability gate for `POST /runs/{id}/respond`.                             |
| `timeout`               | `timedelta\|None` | no       | `None`  | Wait bound; `None` means no timeout.                                       |
| `on_timeout`            | `Literal["halt"] \| str` | no | `"halt"` | `"halt"` (terminal) or `"goto:<node_id>"` (resume target).               |

---

## `StoreRef`

Lightweight reference to a store binding inside an `IRDocument`. The
canonical registration record is [`StoreSpec`](#storespec).

| Field      | Type  | Required | Default | Description                                                |
| ---------- | ----- | -------- | ------- | ---------------------------------------------------------- |
| `name`     | `str` | yes      | _req._  | Local binding name (used in capability strings).           |
| `provider` | `str` | yes      | _req._  | Provider plugin id.                                        |

The `to_capabilities()` helper returns
`["db.{name}:read", "db.{name}:write"]`, matching the AC-8.1 default
pair derived by `StoreSpec.effective_capabilities()`.

```yaml
stores:
  - { name: "kb", provider: "stargraph.stores.qdrant" }
```

### `StoreSpec`

Canonical store registration record (design §3.16, FR-19/FR-20).

| Field           | Type                    | Required | Default | Description                                                                   |
| --------------- | ----------------------- | -------- | ------- | ----------------------------------------------------------------------------- |
| `name`          | `str`                   | yes      | _req._  | Local binding name.                                                           |
| `provider`      | `str`                   | yes      | _req._  | Provider plugin id.                                                           |
| `protocol`      | `Literal[...]`          | yes      | _req._  | One of `vector`, `graph`, `doc`, `memory`, `fact`.                            |
| `config_schema` | `dict[str, object]`     | yes      | _req._  | JSON Schema for the provider's config payload.                                |
| `capabilities`  | `list[str]`             | no       | `[]`    | Explicit capabilities; if empty, `effective_capabilities()` returns the AC-8.1 default pair. |

---

## `ToolRef` and `ToolSpec`

`ToolRef` is the trim shape used inside `IRDocument.tools`; the rich
descriptor is `ToolSpec`. Permissions, side effects, and replay policy
on the descriptor side gate tool execution under each profile.

### `ToolRef`

| Field     | Type          | Required | Default | Description                          |
| --------- | ------------- | -------- | ------- | ------------------------------------ |
| `id`      | `str`         | yes      | _req._  | Namespaced tool id (e.g. `ns.name`). |
| `version` | `str \| None` | no       | `None`  | Optional pinned version.             |

### `ToolSpec`

Tool descriptor (AC-9.4). Key fields summarized below; for the full
operational contract -- `side_effects` semantics, replay policies,
idempotency keys, deprecation flow -- see the
[Plugin Manifest reference](plugin-manifest.md).

| Field             | Type                  | Required | Default              | Description                                                          |
| ----------------- | --------------------- | -------- | -------------------- | -------------------------------------------------------------------- |
| `name`            | `str`                 | yes      | _req._               | Tool name (unique within `namespace`).                               |
| `namespace`       | `str`                 | yes      | _req._               | Namespace prefix (matches `PluginManifest.namespaces`).              |
| `version`         | `str`                 | yes      | _req._               | Tool semver.                                                         |
| `description`     | `str`                 | yes      | _req._               | Human-readable summary.                                              |
| `input_schema`    | `dict[str, object]`   | yes      | _req._               | JSON Schema for the input payload.                                   |
| `output_schema`   | `dict[str, object]`   | yes      | _req._               | JSON Schema for the output payload.                                  |
| `side_effects`    | `SideEffects`         | yes      | _req._               | Enum: `none`, `read`, `write`, `external`. Cleared profile refuses `write`/`external`. |
| `replay_policy`   | `ReplayPolicy`        | no       | `must_stub`          | How replay treats the tool call.                                     |
| `permissions`     | `list[str]`           | no       | `[]`                 | Capabilities required to execute.                                    |
| `idempotency_key` | `str \| None`         | no       | `None`               | Optional caller-supplied dedupe key.                                 |
| `cost_estimate`   | `Decimal \| None`     | no       | `None`               | FR-9: monetary fields use `Decimal`, never `float`.                  |
| `examples`        | `list[dict]`          | no       | `[]`                 | Inline IO examples.                                                  |
| `tags`            | `list[str]`           | no       | `[]`                 | Discoverability tags.                                                |
| `deprecated`      | `bool`                | no       | `false`              | Marks the tool deprecated.                                           |

The legacy v0.1 `side_effects: bool` shape is up-converted by
`stargraph.ir._migrate.coerce_legacy_tool_spec` (`True` -> `SideEffects.write`,
`False` -> `SideEffects.none`) with a one-shot `DeprecationWarning`.

---

## `SkillRef` and `SkillSpec`

### `SkillRef`

| Field     | Type          | Required | Default | Description                |
| --------- | ------------- | -------- | ------- | -------------------------- |
| `id`      | `str`         | yes      | _req._  | Namespaced skill id.       |
| `version` | `str \| None` | no       | `None`  | Optional pinned version.   |

### `SkillSpec`

Skill descriptor (AC-9.6): a named bundle of agent / workflow / utility
logic with optional sub-graph and system-prompt template.

| Field           | Type                                    | Required | Default | Description                                          |
| --------------- | --------------------------------------- | -------- | ------- | ---------------------------------------------------- |
| `name`          | `str`                                   | yes      | _req._  | Skill name.                                          |
| `namespace`     | `str`                                   | yes      | _req._  | Namespace prefix.                                    |
| `version`       | `str`                                   | yes      | _req._  | Skill semver.                                        |
| `description`   | `str`                                   | yes      | _req._  | Human-readable summary.                              |
| `kind`          | `Literal["agent","workflow","utility"]` | yes      | _req._  | Skill category.                                      |
| `tools`         | `list[str]`                             | no       | `[]`    | Tool ids the skill may call.                         |
| `examples`      | `list[dict]`                            | no       | `[]`    | Inline IO examples.                                  |
| `subgraph`      | `str \| None`                           | no       | `None`  | Optional graph fragment id.                          |
| `system_prompt` | `str \| None`                           | no       | `None`  | Optional prompt template.                            |

---

## `PluginManifest`

Plugin manifest (AC-9.5): identity, namespaces, and the entity kinds a
distribution provides. Full lifecycle, namespace-conflict rules, and
load-order semantics are documented in the
[Plugin Manifest reference](plugin-manifest.md).

| Field         | Type                                            | Required | Default | Description                                                                   |
| ------------- | ----------------------------------------------- | -------- | ------- | ----------------------------------------------------------------------------- |
| `name`        | `str`                                           | yes      | _req._  | Plugin distribution name.                                                     |
| `version`     | `str`                                           | yes      | _req._  | Plugin semver.                                                                |
| `api_version` | `Literal["1"]`                                  | yes      | _req._  | Pinned to `"1"`; bumps gate forward-compat upgrades explicitly (FR-6).        |
| `namespaces`  | `list[str]`                                     | yes      | _req._  | Namespaces the plugin claims; conflicts abort load.                           |
| `provides`    | `list[Literal["tool","skill","store","pack"]]`  | yes      | _req._  | Entity kinds contributed.                                                     |
| `order`       | `int` (0..10000)                                | no       | `5000`  | Load priority; collisions raise `PluginLoadError`.                            |

---

## Governance: `PackMount` and `PackRequires`

Packs are the unit of mounted Bosun governance.

### `PackMount`

| Field      | Type                  | Required | Default | Description                                                                  |
| ---------- | --------------------- | -------- | ------- | ---------------------------------------------------------------------------- |
| `id`       | `str`                 | yes      | _req._  | Stable slug (see [ID generation](#id-generation)).                           |
| `version`  | `str \| None`         | no       | `None`  | Pinned pack version.                                                         |
| `requires` | `PackRequires \| None`| no       | `None`  | Version-compat declaration; load-time gate (FR-39).                          |

### `PackRequires`

| Field                    | Type          | Required | Default | Description                                                                  |
| ------------------------ | ------------- | -------- | ------- | ---------------------------------------------------------------------------- |
| `stargraph_facts_version`   | `str \| None` | no       | `None`  | Required stargraph-facts schema version (e.g. `"1.0"`).                         |
| `api_version`            | `str \| None` | no       | `None`  | Required plugin api_version (e.g. `"1"`).                                    |

`stargraph.ir._versioning.check_pack_compat` enforces both fields at
pack-load time and raises `PackCompatError` on mismatch -- silent
runtime drift is impossible (FR-6 force-loud). Comparison is
**pinned-string equality** in the POC; semver-aware matching is
deferred.

---

## `ParallelBlock`

Top-level parallel/join declaration. Mirrors the inline
`ParallelAction` variant but lives outside any rule.

| Field       | Type        | Required | Default | Description                                                |
| ----------- | ----------- | -------- | ------- | ---------------------------------------------------------- |
| `targets`   | `list[str]` | yes      | _req._  | Node ids fanned out in parallel.                           |
| `join`      | `str`       | no       | `""`    | Join node id (empty = end of block).                       |
| `strategy`  | `str`       | no       | `"all"` | `all` / `any` / `race` / `quorum` (POC: free-form string). |

---

## `MigrateBlock`

Migration descriptor used when a graph hash drifts between authoring
and resume.

| Field       | Type  | Required | Default | Description                                |
| ----------- | ----- | -------- | ------- | ------------------------------------------ |
| `from_hash` | `str` | yes      | _req._  | Pre-migration `graph_hash`.                |
| `to_hash`   | `str` | yes      | _req._  | Post-migration `graph_hash`.               |

---

## `FactTemplate` and `SlotDef`

CLIPS deftemplate descriptors used to type rule facts.

### `FactTemplate`

| Field    | Type             | Required | Default | Description                       |
| -------- | ---------------- | -------- | ------- | --------------------------------- |
| `name`   | `str`            | yes      | _req._  | Template name.                    |
| `slots`  | `list[SlotDef]`  | no       | `[]`    | Typed slot definitions.           |

### `SlotDef`

| Field     | Type          | Required | Default | Description                       |
| --------- | ------------- | -------- | ------- | --------------------------------- |
| `name`    | `str`         | yes      | _req._  | Slot name.                        |
| `type`    | `str`         | yes      | _req._  | Slot type string.                 |
| `default` | `str \| None` | no       | `None`  | Optional default value.           |

---

## Versioning and compatibility

`stargraph.ir._versioning` owns the IR version this build understands and
the major-divergence check (FR-35, AC-19.2).

* `STARGRAPH_IR_VERSION = "1.0.0"` -- the single source of truth.
* `parse_version(s)` -- splits `MAJOR.MINOR.PATCH` into a 3-tuple of
  ints; rejects malformed values.
* `check_version(ir)` -- returns a single `version_mismatch`
  `ValidationError` when the document's major differs from
  `STARGRAPH_IR_VERSION`. Missing / non-string `ir_version` is left to
  Pydantic upstream and skipped here.
* `check_pack_compat(pack_mount, stargraph_facts_version, api_version)` --
  load-time gate for `PackMount.requires` (raises `PackCompatError`).

A major bump signals a breaking schema change: callers should treat
`version_mismatch` as a refusal to interpret the document, not a soft
warning.

### Migration shim (`_migrate.py`)

`coerce_legacy_tool_spec(data)` is the only migration helper currently
shipped. It up-converts foundation v0.1's `side_effects: bool` to the
engine v0.2 `SideEffects` enum with a `DeprecationWarning`. Run it on
the raw dict **before** `ToolSpec.model_validate`. The shim is not
imported from `_models.py` to keep the JSON Schema round-trip pure
(FR-7 / AC-13.1).

---

## Validation gates

`stargraph.ir.validate(ir)` is the single eager-validation entry point
(FR-17, FR-18, AC-12.1, AC-12.2, AC-12.5). It accepts a JSON string or
an already-decoded dict and returns `list[ValidationError]` -- empty on
success, populated on failure. **It never raises.**

Each error carries structured `path`, `expected`, `actual`, and `hint`
fields:

* `path` -- RFC 6901 JSON Pointer derived from the Pydantic `loc` tuple
  (with `~` -> `~0`, `/` -> `~1` escaping).
* `hint` -- pulled from the `_HINTS` table keyed on
  `pydantic_core.ErrorDetails.type`, with a fallback to the Pydantic
  docs URL or the raw message.

The gate runs in this order:

1. **JSON parse** -- malformed JSON returns a single
   `IR JSON parse error` and stops.
2. **Pydantic structural validation** (`IRDocument.model_validate`) --
   catches missing required fields, unknown keys (`extra='forbid'`),
   discriminator/tag mismatches, type/pattern errors, and bound
   violations. All Pydantic errors are mapped through `_to_stargraph_error`.
3. **Stable-ID slug enforcement** (FR-33) -- every `node.id`,
   `rule.id`, and `governance[*].id` is run through
   `validate_node_id` / `validate_rule_id` / `validate_pack_id`. ID
   errors are returned as a batch and short-circuit the version check.
4. **Major-version divergence** (`check_version`) -- single
   `version_mismatch` error when `ir_version`'s major differs from
   `STARGRAPH_IR_VERSION`.

<!-- TODO: verify whether cypher linting and namespace-conflict checks are wired into validate() in this build, or whether they live exclusively in the plugin loader and store registry. -->

---

## ID generation

`stargraph.ir._ids` owns ID utilities (FR-30, FR-31, FR-33, design §3.4.1).

### Run + checkpoint identifiers

* `new_run_id()` -- UUIDv7 string; sortable by creation time (FR-30).
* `new_checkpoint_id()` -- UUIDv7 string for checkpoint rows (FR-30).

### Fact content hash

* `fact_content_hash(fact)` -- 32-char BLAKE2b hex digest computed over
  the canonical JSON of `fact` (`json.dumps(..., sort_keys=True,
  separators=(",", ":"))`). Powers the FR-31 content-addressable fact
  store.

### Stable slugs (FR-33)

Node, rule, and pack ids must match the regex
`^[a-z0-9][a-z0-9_\-.]{0,127}$` -- lowercase first character,
alphanumeric or `_-.` continuation, total length 1..128.

| Helper                 | Description                                          |
| ---------------------- | ---------------------------------------------------- |
| `validate_node_id(s)`  | Validate `s` as a node id; raises `ValueError`.      |
| `validate_rule_id(s)`  | Validate `s` as a rule id; raises `ValueError`.      |
| `validate_pack_id(s)`  | Validate `s` as a pack id; raises `ValueError`.      |
| `_slug(text)`          | Lowercase + collapse non-alphanumerics to `-` (max 24 chars). |

### ID autogeneration

When IR fragments arrive without explicit ids (e.g. authored via a
high-level builder), three helpers fill the missing fields with
`slug(name)`, suffixing collisions `-2`, `-3`, ...:

* `autogen_node_ids(items, kind="node")`
* `autogen_rule_ids(items, kind="rule")`
* `autogen_pack_ids(items, kind="pack")`

The `kind` argument is reserved for future per-kind namespace prefixes
(POC implementations are uniform).

---

## Serialization seam (`_dumps.py`)

Every JSON serialization of an IR type inside `src/stargraph/` flows
through `stargraph.ir.dumps` / `dumps_canonical` / `loads`. This is the
only module that calls `model_dump` / `model_validate_json` /
`json.dumps` on IR types (FR-15, AC-11.4) so the wire shape stays
deterministic and round-trip-stable (AC-11.1, AC-11.2).

| Symbol                                  | Behavior                                                                  |
| --------------------------------------- | ------------------------------------------------------------------------- |
| `dumps(ir, *, hashable=False)`          | `model_dump(mode="json", exclude_defaults=True)` + compact `json.dumps`.  |
| `dumps_canonical = partial(dumps, hashable=True)` | Pinned canonical-form alias; sorts keys for content-addressable hashing. |
| `loads(text, model=IRDocument)`         | `model.model_validate_json(text)`; `model` must be an `IRBase` subclass.  |

`dumps` uses `ensure_ascii=False`, `separators=(",", ":")`, and
`sort_keys=hashable`. With `hashable=False` (the default), Pydantic v2
declared field order is preserved -- callers that care about hash
stability must use `dumps_canonical`.

```python
from stargraph.ir import IRDocument, dumps, dumps_canonical, loads

doc = IRDocument(ir_version="1.0.0", id="graph:demo", nodes=[])
wire = dumps(doc)                  # human-readable, declaration order
canon = dumps_canonical(doc)       # sorted keys, hash-stable
restored = loads(wire)             # round-trips byte-for-byte
```

See also: [Concepts: IR](../concepts/ir.md),
[Plugin Manifest](plugin-manifest.md),
[Signing](signing.md).
