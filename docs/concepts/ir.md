# IR and Schema

Stargraph's intermediate representation (IR) is a portable, JSON-Schema-typed description of an agent graph: nodes, routing rules (Fathom `goto`/`halt`/`parallel` actions plus implicit fall-through node order), state shape, and policy bindings. The IR is the unit of replay — given the same IR, the same plugin versions, and the same inputs, Stargraph reproduces a run bit-for-bit.

> **Terminology note**: Stargraph's IR has no first-class `EdgeSpec` model.
> Transitions between nodes are *derived* from rule-firing outcomes
> (`RuleSpec.then` actions) and from `nodes`-list ordering for
> fall-through. Treat "edges" as a mental model, not an IR-level type.

## Why an IR

- **Portability.** The IR is the contract between authoring tools and the runtime.
- **Determinism.** Every transition in a trace points back to an IR node.
- **Auditability.** Reviewers diff IRs, not Python.

## Nautilus prototype gaps

_None recorded yet. This section is updated when the IR-vs-Nautilus prototype (Phase 1 task 1.18 risk mitigation) surfaces a portable-subset gap. Each entry will state the gap, the remediation (lift into IR vs. document as out-of-scope), and the resolving task ID._

> TODO: link to the JSON Schema reference once `reference/ir-schema.md` is filled in.
