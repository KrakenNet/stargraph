# Stargraph examples

Minimal, **runnable** starting points. Every `.yaml` here is executed by a
golden test (`tests/integration/test_examples.py`) and must reach
`status=done` — so nothing in this directory rots.

| File | What it shows | Run |
|---|---|---|
| `hello.yaml` | Smallest graph: 2 nodes, 2 rules, 1 state field, no LLM | `stargraph run examples/hello.yaml --inputs message=hello` |
| `pipeline.yaml` | Three-step linear routing, one Fathom rule per hop | `stargraph run examples/pipeline.yaml --inputs message=hello` |
| `research-bot.yaml` | Authoring format (no `ir_version`): react + judge + verdict-routed feedback loop in ~20 lines | `stargraph run examples/research-bot.yaml --lm-url <url> --lm-model <model> --inputs question="..."` |

Trace rule firings without executing nodes:

```bash
stargraph run examples/hello.yaml --inspect
```

## What's intentionally *not* here

Most examples use `echo`/`halt` nodes, so they stay self-contained and fast
(`research-bot.yaml` is the exception: it demonstrates LLM nodes and needs
`--lm-url`/`--lm-model`; its golden test drives it with a scripted stub LM).
For the features that need more wiring, read the full graphs under
[`demos/`](../demos/):

- **ML nodes** (sklearn/xgboost/onnx), **retrieval**
- **Human-in-the-loop**, **serve / triggers**, **counterfactual replay**

## Adding an example

1. Write `examples/<name>.yaml`. Keep it self-contained (`state_schema:` inline,
   `echo`/`halt` nodes) unless you're deliberately demonstrating more.
2. Run it: `stargraph run examples/<name>.yaml --inputs message=hello`.
3. The golden test picks it up automatically via glob — confirm it passes:
   `uv run pytest tests/integration/test_examples.py`.
