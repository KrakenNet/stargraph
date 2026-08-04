---
name: rag-qa
description: Answer questions from a document store with citations, then verify groundedness with a judge before accepting -- retrieval-augmented QA that refuses to hallucinate.
subgraph: graph.yaml
---

# rag-qa

Retrieve → answer → groundedness check (benchmark T6). A `rag` node
retrieves from the bound store and answers with citations; a `judge`
node verifies every claim is grounded in the cited documents. Rejected
answers loop with the judge's feedback; grounded answers halt.

## State

- Input: `question`.
- Outputs: `answer`, `citations` (ranked retrieved doc ids), `verdict`,
  `score`, `rationale`.

## Setup

Point the `qa` node's store binding at your corpus: edit
`stores[0].path` in `graph.yaml` (a `SQLiteDocStore` database; ingest
with `SQLiteDocStore.put`). A `lancedb` binding (FTS, `stores` extra)
can be added alongside for native ranking.

## Use

- Standalone: `stargraph run graph.yaml` (set the LM via
  `--lm-model`/`--lm-url` or per-node `config.model`).
- As a subgraph: `kind: subgraph`, `spec: <path to graph.yaml>`;
  `config.max_steps` bounds the retry loop.
