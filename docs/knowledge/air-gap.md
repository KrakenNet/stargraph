# Air-gap deployment

Stargraph's knowledge stack is designed to run with **no outbound network at run
time**. The only optional reach-out is the first-load fetch of an embedding
model into the local HuggingFace cache; once staged, every subsequent run
operates fully offline. This page is the operator playbook for that staging.

The reference embedder is `MiniLMEmbedder` at
[`src/stargraph/stores/embeddings.py`](https://github.com/KrakenNet/stargraph/blob/main/src/stargraph/stores/embeddings.py)
— `sentence-transformers/all-MiniLM-L6-v2`, 384 dims, symmetric. The contract
generalizes to any HF model, but the shipped POC pins MiniLM by sha256.

## The three load modes

`MiniLMEmbedder` resolves the model directory via a strict priority order. Pick
the mode that matches your deployment posture:

| Mode | Trigger | Network | Best for |
|---|---|---|---|
| 1. Explicit local path | `MiniLMEmbedder(model_path=...)` | none | fully-offline operators, tests |
| 2. Pre-staged HF cache | `HF_HUB_OFFLINE=1` (env) | none | air-gapped prod with shared cache |
| 3. Cache-on-first-use | default (`allow_download=True`) | first run only | dev / interactive shells |

```python
from stargraph.stores.embeddings import MiniLMEmbedder

# Mode 1 -- explicit directory
embedder = MiniLMEmbedder(
    model_path="/srv/stargraph/models/all-MiniLM-L6-v2",
)

# Mode 2 -- HF cache, offline-only
# Set HF_HUB_OFFLINE=1 before process start.
embedder = MiniLMEmbedder()  # snapshot_download(local_files_only=True)

# Mode 3 -- default; downloads on first call into HF cache, offline thereafter
embedder = MiniLMEmbedder(allow_download=True)
```

After the directory is resolved, `MiniLMEmbedder.__init__` hashes the
`model.safetensors` file and compares it against `MINILM_SHA256`. Drift raises
`EmbeddingModelHashMismatch` (subclass of `StargraphError`) — loud-fail, not a
warning. This catches:

- Pulling a different revision than the one Stargraph pinned.
- Mid-flight cache corruption.
- An operator swapping safetensors files in place.

## `HF_HUB_OFFLINE` and the cache

Mode 2 leans on HuggingFace Hub's `local_files_only=True` plumbing.
`MiniLMEmbedder` calls `huggingface_hub.snapshot_download(...,
local_files_only=local_only)` where `local_only` is true if either:

- `HF_HUB_OFFLINE=1` is set in the process env, **or**
- the constructor is called with `allow_download=False`.

When `local_only` is true and the cache is empty, `snapshot_download` raises
immediately — no silent network attempt, no fallback. Operators see the error
synchronously at `Embedding` construction, not buried inside the first
`embed()` call.

The cache root follows HF defaults (`HF_HOME` → `XDG_CACHE_HOME/huggingface`
→ `~/.cache/huggingface`). Two operationally-relevant points:

- **The cache is content-addressable.** Multiple Stargraph processes can share a
  single read-only cache directory; embedding loads do not need write
  permission once staged.
- **Avoid `fastembed`.** Issue [#615](https://github.com/qdrant/fastembed/issues/615)
  documents that `fastembed` bypasses `HF_HUB_OFFLINE`. Stargraph uses
  `sentence-transformers` directly via `huggingface_hub.snapshot_download` so
  that the offline contract holds.

## Safetensors sha256 pin

The pin lives at `stargraph.stores.embeddings.MINILM_SHA256`:

```python
MINILM_SHA256 = "53aa51172d142c89d9012cce15ae4d6cc0ca6895895114379cacb4fab128d9db"
```

`MiniLMEmbedder._hash_safetensors` streams the weights file in 1 MiB chunks so
the verification stays bound on the largest models. The hash is computed on
**every** instantiation — there is no "skip if already verified" cache. The cost
(~50 ms for a 90 MB file on commodity SSD) is paid once per process and pays
back the first time a tampered file would have shipped a bad vector to a
downstream store.

If you operate a different embedder, the contract is identical: `Embedding`
implementations expose `content_hash` as a required property, and the engine's
embed-hash drift gate compares the writer-time `content_hash` against the
reader-time value at every `VectorStore.bootstrap()` re-entry. See
[Stores → Embed-hash drift gate](stores.md#embed-hash-drift-gate) for the gate
mechanics.

## Staging recipe (Mode 2)

The recommended air-gap recipe for production:

```bash
# 1. On a connected build host, pre-stage the cache:
export HF_HOME=/build/stargraph/hf-cache
python -c "
from huggingface_hub import snapshot_download
snapshot_download('sentence-transformers/all-MiniLM-L6-v2')
"

# 2. Verify the safetensors hash matches the pin:
sha256sum /build/stargraph/hf-cache/hub/models--sentence-transformers--all-MiniLM-L6-v2/snapshots/*/model.safetensors
# expect: 53aa51172d142c89d9012cce15ae4d6cc0ca6895895114379cacb4fab128d9db

# 3. Ship the cache directory to the air-gapped host (read-only mount fine).

# 4. On the air-gapped host:
export HF_HOME=/srv/stargraph/hf-cache
export HF_HUB_OFFLINE=1
# Stargraph processes started in this env will refuse to reach out for any
# missing artifact and will fail loud if the cache is incomplete.
```

The same recipe works for any HF embedder; swap `all-MiniLM-L6-v2` for the
target model and update the sha256 pin in the `Embedding` implementation.

## Stores and air-gap

The five default Providers are all embeddable Python with no outbound network:

- **LanceDB** — local FS only; the SDK never reaches out unless you opt into
  remote tables (Stargraph v1 does not).
- **RyuGraph** — single-file embedded; pure local.
- **SQLite trio** — stdlib; pure local.

If you wire a remote backend (S3-backed Lance, Neo4j 5 server), the air-gap
boundary is your responsibility — Stargraph enforces it for the in-tree defaults
only.

## Operator checklist

- [ ] HF cache staged on connected host.
- [ ] `model.safetensors` sha256 verified against `MINILM_SHA256` (or the pin
      for your custom embedder).
- [ ] `HF_HUB_OFFLINE=1` in the air-gapped process env.
- [ ] No outbound connectivity test passes from the air-gapped host (verify
      with `curl --max-time 2 https://huggingface.co; echo $?` returning
      non-zero).
- [ ] First Stargraph process start succeeds with the offline env; first
      `embed()` call returns vectors without timeout.

See [design §3.1](https://github.com/KrakenNet/stargraph/blob/main/specs/stargraph-knowledge/design.md)
for the full `Embedding` Protocol and POC implementation notes.
