---
name: deep-research
description: Research a question with web search and page reads, judge the answer for completeness, and keep digging at the named gaps until nothing major is missing.
subgraph: graph.yaml
---

# deep-research

Research → completeness-judge loop. A `react` node works
`std.web_search@1` + `std.fetch_page@1` through the governed tool
pipeline (every call capability-checked and receipted as provenance
facts); a `judge` node checks the answer covers the question. On
`fail`, the judge's named gaps go into the next research brief; on
`pass`, the graph halts.

## State

- Input: `question`.
- Outputs: `answer`, `tool_trace` (every tool call the agent made),
  `verdict`, `score`, `rationale`.

## Use

- Standalone: `stargraph run graph.yaml` (network tools need the
  `tools` extra installed; set the LM via `--lm-model`/`--lm-url` or
  per-node `config.model`).
- As a subgraph: `kind: subgraph`, `spec: <path to graph.yaml>`;
  `config.max_steps` bounds the research loop.

## Tuning

Edit `graph.yaml`: the `check` rubric (what counts as complete), the
tool allowlist, or `max_iters` per research pass.
