# Stargraph — Typed rule `when` (structured conditions)

**Status:** Draft v0.1 — design, not yet implemented.
**Owner:** engine / IR.
**Motivation:** make the highest-stakes, lowest-AI-skill part of a graph —
the rule condition — typed, validated, and authorable without hand-writing
CLIPS.

---

## 1. Problem

`RuleSpec.when` is free-text CLIPS (`when: "(initial-fact)"`,
`when: "?n <- (node-id (id node_b))"`). Consequences:

- **Unchecked.** `validate()` cannot tell a typo'd slot from a valid pattern;
  the error only surfaces when Fathom compiles the defrule at run time.
- **Wrong skill for the author.** LLM agents are strong at Python/JSON and weak
  at CLIPS LHS grammar. The condition is exactly where they guess.
- **High blast radius.** The `when` is the routing brain. A silently-wrong
  pattern routes wrong, deterministically.

The `then` side is already a typed discriminated union (`AssertAction`,
`GotoAction`, `HaltAction`, …) and is checked. Only the `when` side is raw.

## 2. Goal

Offer a **structured, typed alternative** for the common conditions, that
`validate()` can check against the fact vocabulary, while:

- Keeping raw CLIPS `when: str` fully supported (escape hatch for the exotic).
- **Not** changing `structural_hash` / replay semantics.

## 3. Key constraint: graph-hash stability

`structural_hash` keys on the canonical IR. If a structured `when` became part
of the canonical form, every existing graph's hash would change and replay
determinism tests would break.

**Resolution: compile structured → string at load.** The structured form is an
*authoring convenience* that is lowered to the existing `when: str` during
`loads()`/graph construction, **before** canonicalization, hashing, or the
handoff to `engine.reload_rules`. The canonical IR a graph hashes over stays
string-valued. A graph authored with a structured `when` hashes identically to
the same graph authored with the equivalent raw string. Zero replay impact.

## 4. IR model

`RuleSpec.when` accepts either a string or a `WhenSpec`:

```python
class FactMatch(IRBase):
    """Match a fact template, optionally binding a variable and constraining slots."""
    fact: str                       # template name, e.g. "node-id", "stargraph.evidence"
    bind: str | None = None         # CLIPS var to bind, e.g. "n" -> "?n <- ..."
    where: list[SlotPredicate] = Field(default_factory=list)
    negated: bool = False           # compiles to (not (...))

class SlotPredicate(IRBase):
    slot: str                       # slot name on the fact template
    op: Literal["eq", "ne", "gt", "ge", "lt", "le"] = "eq"
    value: str | int | float | bool

class WhenSpec(IRBase):
    """Conjunction of fact matches (POC: all-of; or-groups are future)."""
    all: list[FactMatch]

# RuleSpec.when: str | WhenSpec   (Pydantic union; str stays the default/raw form)
```

YAML:

```yaml
rules:
  - id: r-escalate
    when:
      all:
        - fact: stargraph.evidence
          where:
            - { slot: field, op: eq, value: severity }
            - { slot: value, op: ge, value: 0.8 }
    then:
      - kind: goto
        target: escalate
```

## 5. Compiler (`when` lowering)

`compile_when(WhenSpec) -> str` emits a CLIPS LHS:

- `FactMatch{fact, bind, where}` → `?<bind> <- (<fact> <slot-constraints>)`
  - `eq` on a symbol/ident → positional `(slot value)`
  - comparisons → `(slot ?x&:(>= ?x 0.8))` test form
- `negated` wraps in `(not (...))`.
- `WhenSpec.all` joins matches by juxtaposition (CLIPS implicit AND).

Lowering runs once at load. The emitted string is what gets hashed and what
Fathom compiles — identical to a hand-written equivalent.

## 6. `validate()` integration

When `when` is a `WhenSpec`, `validate()` checks (collecting errors, not
fail-fast, matching the existing IR-validate style with `hint=`/`see=`):

- `fact` names a known template (runtime `stargraph.*` vocab from
  `design-docs/stargraph-facts.md`, plus templates declared in the IR / mounted
  packs). Unknown → `violation="when-unknown-fact"`,
  `hint="declare the template or use a known stargraph.* fact"`.
- `slot` exists on that template; `value` type matches the slot type.
- `op` is allowed for the slot type (no `ge` on a symbol slot).

Raw-string `when` keeps today's behavior: not statically checkable, compiled by
Fathom at run time.

## 7. The YAML / Python parity seam

Related ergonomics fix. YAML is a "compiled subset"; some constructs are
Python-only (custom reducers, `Mirror` reducer attributes, arbitrary state
logic). Today an author discovers the wall by trial. With the IR validator as
the choke point, `validate()` should name the wall explicitly, e.g.
`"<construct> requires Python authoring; YAML cannot express it"` with a
`see=` pointer — rather than a generic failure. Track the Python-only set in
one place (this doc + the validator) so it stays honest as parity grows.

## 8. Migration / compatibility

- Additive. `when: str` graphs are unchanged and unaffected.
- Bump IR minor (`1.1.0 → 1.2.0`); structured `when` requires `>= 1.2.0`.
  Loaders at `1.1.0` reject the dict form with a clear error.
- Regenerate `schemas/ir-v1.json` (`make regen-schemas` / `regen_schemas.py`).

## 9. Test plan

- **Lowering:** `compile_when(spec) == "<expected CLIPS>"` for each op + negation.
- **Hash stability:** a graph with a `WhenSpec` and the same graph with the
  equivalent raw string produce the **same** `structural_hash`.
- **Validate:** unknown fact / unknown slot / type-mismatch / bad-op each yield
  the specific `violation` + a hint.
- **End-to-end:** an `examples/route-on-state.yaml` using a structured `when`
  runs to `done` and routes correctly (golden test), finally unblocking a
  state-routing example the CLI can run.
- **Back-compat:** every existing fixture still loads and hashes unchanged.

## 10. Phasing

1. `FactMatch`/`SlotPredicate`/`WhenSpec` models + `compile_when` + lower-at-load
   + hash-stability test. (No validate yet — pure convenience, fully safe.)
2. `validate()` slot/template/type checks with hints.
3. Parity-seam messaging (§7).
4. `examples/route-on-state.yaml` + golden test; document in `how-to/`.

Phase 1 is the safe, high-value core: it gives AI a typed condition to emit and
provably does not touch determinism.
