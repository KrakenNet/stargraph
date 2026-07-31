# Author a graph in simple YAML

The authoring format is the shortest path to a full graph — state,
nodes, LLM steps, tools, and rule-based routing in ~12 lines. It is a
**compile-down layer**: `stargraph run` detects it (no `ir_version`
key) and lowers it to the strict [IR](../reference/ir-schema.md)
transparently. The IR stays frozen; the sugar is all front-end.

## The format

```yaml
id: research-bot
state:
  question: str
  brief: str
  answer: str
  rationale: str
  verdict: {type: str, route: true}    # route: true => Fathom can see it
nodes:
  brief:
    kind: template
    template: "{question}\n\nGaps found by the judge:\n{rationale}"
    out: brief
  research: {kind: react, input: brief, tools: [std.web_search, std.fetch_page]}
  judge: {kind: judge, input: answer, rubric: "Complete and cited."}
routes:
  judge: {fail: brief, pass: done}     # verdict value -> target; done => halt
```

Run it like any graph — the compile is invisible:

```bash
stargraph run research-bot.yaml --lm-url <url> --lm-model <model> \
  --inputs question="what routes stargraph?"
```

### `state`

`field: type` (one of `str`, `int`, `float`, `bool`, `list`, `dict`;
zero defaults) or the mapping form `{type: ..., route: ..., default: ...}`.
`route: true` compiles to a `Mirror` annotation — the field crosses into
CLIPS on node exit and rules can branch on it. Everything else stays
Python-side.

### `nodes`

`name: {kind: ..., <config>}` — every key except `kind` (and `spec`, for
`kind: subgraph`) becomes the node's `config`. Any registered kind
works: the [prebuilt kinds](../reference/nodes/prebuilt.md), `tool`,
`interrupt`, `subgraph`, or a `module:Class` reference to your own
`NodeBase`. Bare tool ids get `@1` appended (`std.web_search` →
`std.web_search@1`).

### `routes`

Declaration order is the default flow: with no rule firing, execution
falls through to the next declared node. Routes add the decisions:

| Form | Compiles to |
| --- | --- |
| `research: judge` | rule `r-research`: after `research`, goto `judge` |
| `judge: {fail: brief, pass: done}` | one rule per verdict value, matching `{node: judge, verdict: <value>}` |
| `... : done` | halt the run |

Value routes branch on the standard `verdict` field (what `classify` and
`judge` emit) and require it declared with `route: true` — a missing
declaration is a loud error naming the exact fix. Every generated rule
uses the `when`-mapping sugar; there is no CLIPS in an authored file,
and no LLM ever picks a route — the lowered rules run in Fathom like any
hand-written pack.

Every shape error is an `IRValidationError` prefixed `authoring:` that
names the offending key and the fix.

## See what it becomes

```bash
stargraph compile research-bot.yaml --show-clips
```

Prints the lowered IR document (synthesized `state_class`, `NodeSpec`s,
`RuleSpec`s), and with `--show-clips` each generated rule as
`rule-id: <CLIPS LHS> => goto X / halt`. Use it to learn the IR or debug
a route; graduate to hand-written IR whenever you outgrow the sugar.

## Start from a template or bundle

```bash
stargraph new research-bot        # writes research-bot.yaml (the loop above)
stargraph new rag-qa              # copies the bundle: rag-qa/graph.yaml + SKILL.md
```

Bundle names: `coding-agent`, `deep-research`, `evaluator-optimizer`,
`hitl-approval`, `orchestrator-workers`, `rag-qa`, `triage-router`.
Each bundle is a full IR graph plus a `SKILL.md` — a working agent
pattern (plan → work → judge loops, triage routing, HITL approval) to
run as-is, mount as a subgraph, or edit. Existing targets are never
overwritten.

## Limits (v1)

- Value routes branch on `verdict` only — standardize on it (both
  `classify` and `judge` already emit it).
- One graph per file; `state` fields are flat primitives/containers.
- For anything the sugar can't say (custom fact templates, multi-field
  `when` conditions, verifiers), write IR — see
  [Build a graph](build-graph.md).

## See also

- `examples/research-bot.yaml` in the repo — the golden-tested authored
  loop.
- [Prebuilt nodes](../reference/nodes/prebuilt.md) — the kinds you'll
  compose.
- [Fathom rules tutorial](../tutorials/fathom-rules.md) — what routes
  compile into.
