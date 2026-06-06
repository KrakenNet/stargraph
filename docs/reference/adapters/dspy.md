# DSPy Adapter

`stargraph.adapters.dspy` is the force-loud DSPy seam (FR-5, FR-6, FR-25,
design §3.3.1). The seam is intentionally thin: a logging filter installed
on `dspy.adapters.json_adapter` converts DSPy's silent
ChatAdapter→JSONAdapter fallback warning into
`stargraph.errors.AdapterFallbackError`.

Source: `src/stargraph/adapters/dspy.py`.

## `bind`

```python
def bind(module: Any, *, signature_map: Any) -> DSPyNode: ...
```

Returns a [`stargraph.nodes.dspy.DSPyNode`](../nodes/index.md) wrapping `module`
with the force-loud config wired in. Both parameters are typed `Any` at the
seam so the adapter accepts:

- a real `dspy.Module` subclass, and
- the inert fixtures the FR-6 integration tests use.

The concrete `DSPyNode` validates `signature_map` shape on use.

## Force-loud config

Verbatim recipe from design §3.3.1:

```python
JSONAdapter(use_native_function_calling=True)         # default adapter
ChatAdapter(use_json_adapter_fallback=False)          # chat-style sigs
logging.getLogger("dspy.adapters.json_adapter").addFilter(_LoudFallbackFilter())
```

`bind` applies all three. The filter install is idempotent (fingerprinted by
exact filter type) so repeated `bind()` calls do not stack duplicate
filters.

!!! warning
    Do not construct `dspy.JSONAdapter` or `dspy.ChatAdapter` outside this
    seam. Bypassing `bind()` reintroduces silent fallback (FR-6 violation)
    and the Bosun audit trail loses the adapter-event provenance.

## `_LoudFallbackFilter`

The filter behavior:

```python
class _LoudFallbackFilter(logging.Filter):
    def filter(self, record):
        if FALLBACK_NEEDLE in record.getMessage():
            raise AdapterFallbackError(
                record.getMessage(),
                adapter="dspy",
                original_adapter="ChatAdapter",
                fallback_adapter="JSONAdapter",
            )
        return True
```

Raising from inside `filter()` short-circuits log emission so the warning
text never leaks through to handlers. The caller is forced to deal with
the silent-degradation event explicitly.

## `FALLBACK_NEEDLE`

The verbatim DSPy ≥3.0.4 fallback warning text:

```python
FALLBACK_NEEDLE: str = "Failed to use structured output format, falling back to JSON mode"
```

Mirrored verbatim in `tests/integration/test_dspy_loud_fallback.py`. If
DSPy upstream changes the wording, both copies must move in lockstep.

<!-- TODO: verify FALLBACK_NEEDLE wording against the next DSPy upgrade. -->

## `SignatureMap`

```python
SignatureMap = dict[str, str]
```

A user-supplied mapping from Stargraph state-field names to DSPy signature
input/output names. Phase-2 keeps the type structurally a plain mapping to
avoid premature coupling; the concrete schema lands when `DSPyNode` grows
beyond the seam.

## Example

```python
import dspy
from stargraph.adapters.dspy import bind

class Summarize(dspy.Signature):
    """Summarize a document."""
    document: str = dspy.InputField()
    summary: str = dspy.OutputField()

class SummarizeModule(dspy.Module):
    def __init__(self):
        super().__init__()
        self.summarize = dspy.Predict(Summarize)

    def forward(self, document: str):
        return self.summarize(document=document)

node = bind(
    SummarizeModule(),
    signature_map={
        "input_doc": "document",   # state.input_doc -> signature.document
        "output_summary": "summary",
    },
)
# `node` is a DSPyNode; mount it in your graph spec.
```

## Errors

| Error | When |
| --- | --- |
| `stargraph.errors.AdapterFallbackError` | DSPy emitted the canonical fallback warning. The warning is suppressed; the error carries the original message plus `adapter="dspy"`, `original_adapter="ChatAdapter"`, `fallback_adapter="JSONAdapter"`. |

## See also

- [Adapters index](index.md)
- [Nodes reference](../nodes/index.md) — `DSPyNode` execution semantics.
- [MCP adapter](mcp.md) — the other v1 adapter seam.
