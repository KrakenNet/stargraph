# `Reranker`

Structural contract for rerankers (FR-16, design §3.8). Used by
`RetrievalNode` (and internally by `LanceDBVectorStore` for
`mode="hybrid"`) to fuse per-store hit lists into a single ranked list.

## Protocol surface

```python
from typing import Protocol, runtime_checkable
from stargraph.stores import Hit

@runtime_checkable
class Reranker(Protocol):
    async def fuse(
        self,
        per_store: list[list[Hit]],
        *,
        k: int,
    ) -> list[Hit]: ...
```

`per_store` is one ranked list per source store. Output length is
`<= k`. The Protocol is async by convention so network-bound rerankers
(Cohere, Jina) share the same shape as the pure-Python default.

## `RRFReranker`

Default in-tree reranker (`stargraph.stores.rerankers`). Reciprocal Rank
Fusion: model-free, API-key-free, appropriate for the default
`mode="hybrid"` path.

### Formula

For each `Hit` appearing in any per-store list, the fused score is:

$$
\text{score}(h) = \sum_{\text{lists}} \frac{1}{k_{\text{param}} + \text{rank}(h)}
$$

Or in code:

```python
score = sum(1.0 / (k_param + rank_in_list)
            for list in per_store
            if h in list)
```

`rank_in_list` is **1-based**. Hits sharing an `id` across stores are
de-duplicated -- their RRF contributions are summed; the first-seen
`metadata` is retained. Output is sorted by fused score desc and
truncated to `k`.

### Constructor

```python
from stargraph.stores import RRFReranker

reranker = RRFReranker(k_param=60)   # default
```

| Param | Type | Default | Notes |
|---|---|---|---|
| `k_param` | `int` | `60` | RRF dampening constant. Lower = sharper ranking distinction; higher = flatter fusion. `60` is the canonical Cormack et al. default. |

!!! note "Permutation invariance"
    Float addition is non-associative, so `RRFReranker.fuse` sorts each
    id's contribution list before summing. This makes the fused score
    depend only on the multiset of contributions -- required for the
    permutation-invariance guarantee called out in design §3.8.

## `CrossEncoderReranker`

Stub implementation reserved for Phase 3 -- the placeholder for the
opt-in cross-encoder reranker. Calls to `.fuse()` raise
`NotImplementedError` today.

```python
class CrossEncoderReranker:
    async def fuse(self, per_store, *, k): ...
    # raises NotImplementedError until Phase 3
```

### Opt-in via entry point

Heavier rerankers (cross-encoder, Cohere, Jina) live behind the
`stargraph.rerankers` entry-point group. `stargraph.stores.rerankers` only
ships the always-available `RRFReranker` default; everything else is
loaded by `stargraph.stores._rerank_loader` from the entry-point group at
runtime, so optional dependencies (sentence-transformers, vendor SDKs)
are **not** required for the default install.

```toml
# In a downstream plugin's pyproject.toml:
[project.entry-points."stargraph.rerankers"]
cross-encoder = "my_plugin.rerankers:CrossEncoderReranker"
```

## YAML wiring

Rerankers are wired at the `RetrievalNode` level inside the IR -- see
the [retrieval knowledge page](../../knowledge/retrieval.md). The
default `mode="hybrid"` path inside `LanceDBVectorStore` uses
`RRFReranker(k_param=60)` unconditionally.
<!-- TODO: verify the exact `node` block schema for selecting a non-default reranker -->

## Errors raised

| Error | Raised when |
|---|---|
| `NotImplementedError` | `CrossEncoderReranker.fuse` called before Phase 3. |
| `PluginLoadError` (subclass) | Entry-point lookup failed for a non-default reranker name. |
