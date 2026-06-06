# `Embedding`

Structural contract for embedders (FR-14, FR-15, design §3.1). Concrete
in-tree provider: [`MiniLMEmbedder`](#minilmembedder).

## Protocol surface

```python
from typing import Literal, Protocol, runtime_checkable

@runtime_checkable
class Embedding(Protocol):
    @property
    def model_id(self) -> str: ...
    @property
    def revision(self) -> str: ...
    @property
    def content_hash(self) -> str: ...
    @property
    def ndims(self) -> int: ...

    async def embed(
        self,
        texts: list[str],
        *,
        kind: Literal["query", "document"],
    ) -> list[list[float]]: ...
```

The four identity properties feed the FR-8 5-tuple drift gate
`(model_id, revision, content_hash, ndims, schema_v)` written into
LanceDB schema metadata at `VectorStore.bootstrap()` time. Mismatch on
re-entry raises `IncompatibleEmbeddingHashError`.

`kind` is required by the Protocol for forward-compat with asymmetric
models (bge / e5 v5+) -- symmetric models (MiniLM) are free to ignore
it, but the parameter is mandatory day one.

## `MiniLMEmbedder`

POC reference implementation backed by
`sentence-transformers/all-MiniLM-L6-v2`. Lives at
`stargraph.stores.embeddings`.

### Constants

| Constant | Value | Purpose |
|---|---|---|
| `MINILM_MODEL_ID` | `sentence-transformers/all-MiniLM-L6-v2` | HF model id pin. |
| `MINILM_NDIMS` | `384` | Embedding dimensionality (from model card). |
| `MINILM_SHA256` | `53aa51172d142c89d9012cce15ae4d6cc0ca6895895114379cacb4fab128d9db` | Pinned safetensors sha256 (FR-15). |
| `MINILM_MAX_TOKENS` | `256` | Positional-window cap; longer inputs are clipped with a structlog warning. |

### Constructor

```python
from pathlib import Path
from stargraph.stores import MiniLMEmbedder

embedder = MiniLMEmbedder(
    model_path=None,                    # mode 1 if set
    model_id="sentence-transformers/all-MiniLM-L6-v2",
    revision="main",
    allow_download=True,                # mode 3 if False -> mode 2 semantics
    expected_sha256=MINILM_SHA256,
)
```

| Param | Type | Default | Notes |
|---|---|---|---|
| `model_path` | `Path \| None` | `None` | Mode 1 if set: explicit local directory. |
| `model_id` | `str` | `MINILM_MODEL_ID` | HF repo id. |
| `revision` | `str` | `"main"` | HF revision / commit sha. |
| `allow_download` | `bool` | `True` | `False` forces cache-only (mode 2 semantics). |
| `expected_sha256` | `str` | `MINILM_SHA256` | Override only if pinning a different revision. |

### Dependencies

Optional extra: `stargraph[skills-rag]`
(`sentence-transformers>=5.0,<6`, `huggingface_hub>=0.25`). The
`sentence-transformers` import is lazy -- it only fires inside
`__init__` after the safetensors hash check passes.

### Three load modes

| Mode | Trigger | Behaviour |
|---|---|---|
| **1. Explicit path** | `model_path=Path(...)` | Load directly from the local directory. Used by tests and fully-offline operators who pre-stage the directory. No HF Hub call. |
| **2. `HF_HUB_OFFLINE=1`** | env var set | Cache-only via `huggingface_hub.snapshot_download(local_files_only=True)`. No network. Raises if cache is empty. |
| **3. `allow_download` flag** | `model_path is None` | `allow_download=False` matches mode 2 semantics; `True` (default) permits a network fetch into the HF cache on first use. |

### SHA-256 verification (FR-15)

After the model directory is resolved, the safetensors weights file
(`model.safetensors`) is hashed and compared against
`expected_sha256`. Drift raises
`stargraph.errors.EmbeddingModelHashMismatch` carrying:

| Context key | Value |
|---|---|
| `model_id` | The configured HF model id. |
| `expected_sha256` | The pinned hash. |
| `actual_sha256` | What we just hashed. |
| `model_path` | The full safetensors file path. |

### Token-clip behaviour (`MINILM_MAX_TOKENS`)

`embed()` clips inputs to 256 tokens before encoding. Tokens are
counted via `self._model.tokenizer.encode(text, add_special_tokens=False)`.
Truncation logs a structlog `minilm.input_clipped` warning with the
original token count -- a long document will not silently degrade
retrieval quality past the model's positional window.

### Async wrapping

`SentenceTransformer.encode` is sync; `MiniLMEmbedder.embed` wraps it
through `asyncio.to_thread` so callers stay non-blocking.
`normalize_embeddings=True` is set so the returned vectors are unit-length.

## `MiniLMEmbedder.fake()` / `FakeEmbedder`

Deterministic, dependency-free POC test embedder. Hashes each input
string with sha256, seeds `numpy.random` with the digest, and emits
an L2-normalised vector. `content_hash` is a stable sentinel
(`"fake-" + "0" * 59`) so the FR-8 drift gate still round-trips through
`bootstrap`. Not for production -- smoke tests only.

```python
embedder = MiniLMEmbedder.fake()  # returns FakeEmbedder
```

## YAML wiring

Embedders are not declared at the IR top level today; the
`LanceDBVectorStore` constructor takes an `Embedding` instance directly.
<!-- TODO: verify the IR-level embedder selector once one lands; currently the embedder is wired in code at `RetrievalNode` construction time -->

## Errors raised

| Error | Raised when |
|---|---|
| `EmbeddingModelHashMismatch` | safetensors sha256 does not match `expected_sha256`. |
| `OSError` / `FileNotFoundError` | mode 2 cache empty, or mode 1 `model_path` missing. |
| (Bubbling) `huggingface_hub` errors | mode 3 network fetch failed. |
