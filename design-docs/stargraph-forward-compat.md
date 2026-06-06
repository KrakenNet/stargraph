# Stargraph — Forward Compatibility Notes

**Status:** Draft v0.1
**Purpose:** Capture the design constraints v1 must respect to keep future plans tractable. Every "do" below has a paired "don't" — the failure mode if we drift.

These futures are **not v1 features.** This doc exists so v1 doesn't accidentally close them off.

---

## 1. Future capabilities targeted

| Future | Earliest realistic phase |
|---|---|
| Bidirectional conversion: LangGraph / Agno / CrewAI / n8n | Post-1.0 |
| Integrations (Stargraph *calls* and *is called by* the above) | 1.x |
| AI-harness plugins (Claude Code, Cursor, etc. author Stargraph graphs) | 1.x |
| Stargraph MCP server (Stargraph exposed as MCP tools) | 1.x |
| No-code UI (graph builder + dashboards) | 2.x |
| Chat agent that builds graphs end-to-end via skills/tools | 2.x |
| Default tool/agent library + one-click DB provisioning | rolling, starts at 1.x |

---

## 2. The IR is the load-bearing decision

Every future above runs through the YAML/JSON IR.

**Do (now).**
- Make IR the canonical graph definition (ADR 0009)
- Validate IR against a published JSON Schema
- Make Python builders thin wrappers that emit IR
- Round-trip IR → Python → IR must be lossless
- Treat new IR fields as a versioned, governed change

**Don't.**
- Add Python-only features that can't be expressed in IR
- Use Pydantic types in the IR shape (use JSON Schema)
- Embed Python objects (callables, classes) in IR — only references
- Allow plugins to extend IR without manifest declaration

**Failure mode if we drift.** Conversions become impossible because the source language has features the target IR can't represent. The no-code UI cannot author graphs that match what Python authors produce, splitting the user base.

---

## 3. Stable IDs everywhere

UIs, conversions, harnesses, history, diffing — all need stable identifiers.

**Do.**
- Generate stable IDs for: nodes, rules, tools, skills, packs, runs, checkpoints, facts (when persisted)
- IDs are human-meaningful when authored, deterministic when not (`node:classify`, `rule:r-3a8f`)
- Rules carry an `id` slot in IR; auto-generated if omitted, persisted on first save

**Don't.**
- Identify by array index or insertion order
- Reassign IDs across versions
- Use Python `id()` or memory addresses anywhere outside a single process

**Failure mode if we drift.** Diffing two graphs reports nonsensical changes. UI selections break across edits. History becomes useless.

---

## 4. Schemas are JSON Schema, not bespoke

State, tool I/O, fact templates, store configs.

**Do.**
- Use JSON Schema for everything serializable
- Generate JSON Schema from Pydantic on the way out
- Publish Stargraph's IR JSON Schema; treat it as part of the public contract

**Don't.**
- Invent custom type DSLs
- Hide constraints in Python validators that JSON Schema can't represent
- Use Pydantic-specific features (`computed_field`, custom validators) in surfaces that need to be portable

**Failure mode if we drift.** Conversion targets and the no-code UI must reverse-engineer constraints that JSON Schema would have given them for free.

---

## 5. Capability metadata is the AI authoring contract

Future chat agents and harness plugins compose graphs by reading metadata, not source code.

**Do.**
- Require `description` and (for skills) `examples` (Plugin API §9)
- Make registry queries return everything an authoring agent needs
- Treat tool/skill metadata as docs *and* spec — they're the same artifact

**Don't.**
- Document features in README that aren't in metadata
- Let critical behavior depend on argument names alone
- Allow undocumented "magic" parameters

**Failure mode if we drift.** AI authoring produces broken graphs. Humans must hand-edit. The chat agent product becomes unbuildable.

---

## 6. Stores have a lifecycle, not just a connection

Future "spin up a DB and integrate" requires Stores to know how to create themselves.

**Do.**
- Add `bootstrap()`, `health()`, `migrate()` to Store Protocols (even if no-op for cloud providers)
- Default Providers (LanceDB, Kuzu, SQLite) implement bootstrap as "create files in this dir"
- Store config is JSON-Schema-validated; UIs can render config forms automatically

**Don't.**
- Assume the DB exists
- Couple Store config to environment variables; use config objects with optional env resolution
- Treat schema migrations as out-of-band operational work

**Failure mode if we drift.** "One-click DB" requires bespoke code per provider. Onboarding stays infrastructure-heavy.

---

## 7. Conversion is an out-of-tree concern

Conversions to/from LangGraph, Agno, CrewAI, n8n live in separate packages, not the core.

**Do.**
- Keep core agnostic of competitor frameworks
- Define conversions as `IR ↔ external IR` translators
- Ship one reference conversion (probably LangGraph) at 1.1 to validate the IR is expressive enough
- Conversions live at `stargraph-convert-*` packages

**Don't.**
- Add concepts to core just to ease conversion
- Let conversion concerns shape primary APIs
- Promise lossless round-tripping in either direction (it isn't possible — document loss explicitly)

**Failure mode if we drift.** Core surface bloats with concepts borrowed from competitors. Stargraph's distinctiveness erodes.

---

## 8. Integrations are first-class but additive

Integration ≠ conversion. Integration = Stargraph calls or is called by another framework at runtime.

**Do.**
- Expose Stargraph as MCP server (Stargraph MCP) at 1.x — this makes Stargraph callable from any AI harness
- Provide a `stargraph.adapters.langgraph` shim that lets a Stargraph graph appear as a LangGraph node and vice versa
- Keep these in plugins, not core

**Don't.**
- Take a runtime dependency on competitor frameworks
- Build adapters that require both frameworks to be installed in core paths
- Special-case adapter behavior in the runtime

**Failure mode if we drift.** Dependency hell, slow CI, and Stargraph becomes "the framework that drags LangChain in too."

---

## 9. The chat-agent-builds-graphs future

This is the most ambitious item. v1 must keep the door open without building it.

**Do (in v1).**
- Make all metadata machine-readable
- Make IR JSON-Schema-validatable so an LLM can be constrained to produce valid IR
- Provide `stargraph.validate(ir)` that returns *useful* errors (path, expected, actual)
- Provide `stargraph.simulate(ir, fixtures)` for dry-run validation before committing

**Don't (in v1).**
- Build the chat agent itself
- Special-case LLM-generated IR vs human IR
- Encode Stargraph knowledge into prompts only (it should be in metadata + schemas)

**Failure mode if we drift.** The chat agent has to read Python source, hand-tuned prompts go stale on every release, and the product isn't maintainable.

---

## 10. Forbidden choices in v1

Each of these is cheap to avoid now and very expensive to undo:

- **Python-only graph definitions.** Always emit IR. Always.
- **Bespoke type systems.** Use JSON Schema.
- **Implicit IDs only.** Persist IDs the moment they exist.
- **DSPy idioms in Stargraph core.** Adapters, not coupling.
- **Tool schemas that aren't fully serializable.** Including callbacks and Python types.
- **Side effects without declarations.** Replay and governance both depend on this.
- **Configuration via environment variables only.** Config objects, env resolution as a layer.
- **Provider-specific features leaking into Store Protocols.** Generalize or skip.
- **Loose coupling that pretends to be a contract.** "Conventions" with no schema = future breakage.
- **Optional provenance.** Mandatory from day one.

---

## 11. What this doc is *not*

- Not a list of v1 features
- Not a commitment to ship any of the future capabilities
- Not architecture — see the design doc and ADRs

It exists to make sure v1 ships in a shape where these futures remain *possible*. Closing them off is the cheap mistake; this doc is the cheap fix.
