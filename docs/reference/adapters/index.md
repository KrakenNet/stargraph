# Adapters Reference

Adapters are the seams that wrap an external runtime (an LLM framework, an
MCP server, ...) so Stargraph can call it under the same gates as in-tree tools.
Every adapter is intentionally thin: validate, gate, invoke, validate,
sanitize. No silent fallback, no implicit retries.

Source: `src/stargraph/adapters/`.

## Catalog

| Adapter | Module | Purpose | Key safety property |
| --- | --- | --- | --- |
| [DSPy](dspy.md) | `stargraph.adapters.dspy` | Bind a `dspy.Module` as a `DSPyNode` with the force-loud JSON adapter. | Silent JSONAdapter fallback raises `AdapterFallbackError` (FR-6). |
| [MCP](mcp.md) | `stargraph.adapters.mcp` | Translate an MCP server's catalogue into Stargraph `ToolSpec`s and gate every `call_tool`. | Schema validation (in + out) + capability gate + output sanitization (FR-25). |

## Design constraint

Adapters never accept silent degradation. Each one converts the underlying
framework's "soft fallback" path into a loud error so an operator (or the
Bosun audit trail) sees the seam event explicitly.

For DSPy, the seam is a logging filter that intercepts the
`dspy.adapters.json_adapter` warning. For MCP, the seam is the schema
validator that runs on both the request arguments and the response payload.

## See also

- [Tools reference](../tools.md) — the `ToolSpec` model adapters emit.
- [Concepts: provenance & replay](../../concepts/provenance.md) — how adapter
  outputs flow into the run record.
