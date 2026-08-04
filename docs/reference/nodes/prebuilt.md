# Prebuilt nodes

Batteries-included node kinds: six signature-preset LLM nodes, two
composition nodes (`react`, `code`), store-bound retrieval (`rag`), and
the pure `template` renderer. All are configured entirely from YAML —
no Python required.

The contract that matters for routing: **the LLM never picks the next
node**. Prebuilt nodes emit standardized state fields (`verdict`,
`confidence`, `score`, …); Fathom rules route on the mirrored facts.
Mark the fields you route on with `Mirror` (or `route: true` in the
[authoring format](../../how-to/authoring-format.md)).

## Catalog

| Kind | Reads (`input` default) | Emits | Needs |
| --- | --- | --- | --- |
| `reason` | `question` | `answer`, `rationale` | LM |
| `summarize` | `text` | `summary` | LM |
| `classify` | `text` | `verdict` (one of `labels`), `confidence` | LM |
| `extract` | `text` | the declared `fields` keys, typed | LM |
| `judge` | `content` | `verdict` (`pass`/`fail`), `score`, `rationale` | LM |
| `plan` | `goal` | `tasks` (list of str) | LM |
| `react` | `question` | `answer`, `tool_trace` | LM + registry tools |
| `code` | `task` | `code`, `run_result`, `verdict` | LM + `tools:std:exec` |
| `rag` | `query` | `answer`, `citations` | LM + a bound store |
| `template` | any state fields | one field (`out`) | nothing (pure) |

`confidence` and `score` are emitted as `str(float)` — Mirror facts
assert string values, so a `str` state field routes cleanly. Outputs are
whitelisted per kind: anything else the underlying module produces is
dropped instead of silently polluting state.

## Shared LM config

Every LM-backed kind accepts (extra keys rejected):

| Key | Meaning |
| --- | --- |
| `input` | State field read (per-kind default above). |
| `instructions` | Overrides the preset instructions. |
| `model` / `api_base` / `api_key_env` | Per-node LM override; otherwise the process LM (`stargraph run --lm-url/--lm-model`) is used. No LM anywhere → loud `IRValidationError` at build. |

## Kind-specific config

```yaml
nodes:
  - id: triage
    kind: classify
    config: {labels: [urgent, routine, irrelevant], input: text}

  - id: check
    kind: judge
    config: {rubric: "Answer is complete and cites sources.", input: answer}

  - id: pull
    kind: extract
    config: {fields: {who: str, amount: float, approved: bool}, input: text}

  - id: research
    kind: react
    config:
      tools: [std.web_search@1, std.fetch_page@1]   # registry allowlist, required
      max_iters: 8                                   # 1-64
      input: brief

  - id: fix
    kind: code
    config: {input: task, timeout_s: 30}   # runs via std.python_exec, capability tools:std:exec

  - id: qa
    kind: rag
    config:
      stores:                       # >=1 binding, built (fail-fast) at graph load
        - {provider: sqlite_doc, path: ./kb.db}                     # deterministic lexical scan
        - {provider: lancedb, path: ./kb.lance, table_name: vectors} # native FTS
      query: brief
      k: 5

  - id: brief
    kind: template
    config:
      template: "{question}\n\nGaps found by the judge:\n{rationale}"
      out: brief
```

Notes:

- **`classify` is the router-that-isn't**: it emits `verdict` as a fact;
  a rule like `when: {node: triage, verdict: urgent}` does the routing.
- **`react`** drives registry tools through the `execute_tool` pipeline —
  capability gates apply, and every call asserts
  `stargraph.tool-call` / `stargraph.tool-result` facts. `tool_trace`
  records `{tool, args, observation}` per step.
- **`code`** generates Python with the LM, executes it through
  `std.python_exec` (subprocess sandbox, default-deny capability
  `tools:std:exec`), and emits `verdict` `pass`/`fail` from the run.
- **`rag`** with zero hits returns `{"answer": "", "citations": []}`
  without an LM call. `sqlite_doc` is a deterministic lexical
  scan-and-rank (no embedder); `lancedb` uses native FTS. Multiple
  bindings are RRF-fused.
- **`template`** is pure `str.format` over state fields — the feedback
  re-injection primitive for judge loops (render the judge's
  `rationale` back into the generator's input, `goto` the template
  node). Bare `{field}` placeholders only (format specs allowed,
  no attribute/index access); unknown placeholders fail at graph load,
  missing state fields fail loudly at run time.

## Errors

- Bad config (unknown key, missing `labels`/`rubric`/`fields`/`tools`,
  out-of-range `max_iters`/`timeout_s`/`k`) → `IRValidationError` at
  graph load.
- `judge` verdict not `pass`/`fail`, `classify` verdict outside
  `labels` → `StargraphRuntimeError` (force-loud, no silent coercion).

## See also

- [Prebuilt subgraph bundles](../../how-to/authoring-format.md#start-from-a-template-or-bundle) —
  shipped loops composed from these kinds.
- [`DSPyNode`](dspy.md) — the underlying LLM node the LM kinds preset.
- [Tools reference](../tools.md) — the `std` pack `react`/`code` draw from.
