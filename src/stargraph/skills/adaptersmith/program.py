# SPDX-License-Identifier: Apache-2.0
"""AdapterProgram — the DSPy generator for adapters, bound to the shared SmithProgram.

The generate/forward/demo plumbing lives in :class:`SmithProgram`; this module
supplies the *adapter* signature (the fields an adapter generation emits) and
``coerce`` (Prediction → plain dict). LM construction + ``clarify`` are
re-exported from the shared core so callers import them from here.
"""

from __future__ import annotations

from typing import Any

import dspy  # pyright: ignore[reportMissingTypeStubs]

from stargraph.skills._smith.lm import (
    DEFAULT_OLLAMA_URL,
    clarify,
    configure_lm,
    make_lm,
)
from stargraph.skills._smith.program import INPUT_FIELDS, SmithProgram, as_dict
from stargraph.skills.adaptersmith import _ledger

# Re-exported from the shared core so callers keep importing them from here.
__all__ = [
    "DEFAULT_OLLAMA_URL",
    "INPUT_FIELDS",
    "AdapterProgram",
    "AdapterSignature",
    "clarify",
    "coerce",
    "configure_lm",
    "make_lm",
]


class AdapterSignature(dspy.Signature):  # pyright: ignore[reportUnknownMemberType]
    """Write one Stargraph adapter and a pytest test for it, from a brief.

    A Stargraph adapter is an external-runtime SEAM: a Python MODULE (no base
    class) exposing two module-level ``async`` functions, ``bind`` and
    ``call_tool``. ``bind(session, *, capabilities)`` initializes the session,
    lists its tools, and translates each into a ``stargraph.ir.ToolSpec``.
    ``call_tool(session, tool, arguments)`` gates the call against
    ``capabilities`` BEFORE touching the session, validates ``arguments`` against
    the tool's ``input_schema``, invokes the session, validates the response
    against ``output_schema``, sanitizes every string leaf (control-char strip →
    HTML-escape → marker strip), and returns the cleaned object. Both functions
    MUST be coroutine functions. The adapter must NOT import the real ``mcp``
    package at module top level (lazy-import it only on the real-transport
    branch). Honor every lesson in ``lessons`` and fix every issue in
    ``last_findings``.

    OUTPUT FILE CONTRACT (the two files are written FLAT into one directory and
    gated together — follow this exactly or the gate rejects a correct adapter):

    - ``adapter_source`` is saved as ``adapter.py`` and must define module-level
      ``async def bind(...)`` and ``async def call_tool(...)`` (plus whatever they
      import). Put every import at module top level EXCEPT the optional ``mcp``
      transport, which is lazy-imported inside the real-transport branch.
    - ``test_source`` is saved as ``test_adapter.py`` BESIDE it. It MUST import the
      adapter with ``from adapter import bind, call_tool`` — NOT from its namespace
      or package path (there is no package; the file is literally ``adapter.py``).
      Do NOT import anything you do not use — an unused import fails the static
      gate. Write plain ``def test_*()`` functions and drive ``bind``/``call_tool``
      against an in-test session stub via ``asyncio.run``.
    - ``fixture`` may be ``{}`` — the contract tier embeds its own session stub +
      capability literals and does NOT trust the generated test or fixture.
    """

    brief: str = dspy.InputField(desc="what the adapter should do")  # pyright: ignore[reportUnknownMemberType]
    lessons: list[str] = dspy.InputField(desc="past failures to avoid")  # pyright: ignore[reportUnknownMemberType]
    last_findings: list[dict[str, Any]] = dspy.InputField(desc="prior attempt findings")  # pyright: ignore[reportUnknownMemberType]
    relevant_context: str = dspy.InputField(  # pyright: ignore[reportUnknownMemberType]
        desc="grounding: adapter contract + similar existing adapters + accepted examples + web"
    )

    adapter_name: str = dspy.OutputField(desc="the adapter's snake_case name")  # pyright: ignore[reportUnknownMemberType]
    namespace: str = dspy.OutputField(desc="the adapter's tool namespace (e.g. 'mcp')")  # pyright: ignore[reportUnknownMemberType]
    fixture: dict[str, Any] = dspy.OutputField(desc="advisory sample fixture (may be {})")  # pyright: ignore[reportUnknownMemberType]
    adapter_source: str = dspy.OutputField(  # pyright: ignore[reportUnknownMemberType]
        desc="adapter.py: module-level async bind + call_tool, imports at top level"
    )
    test_source: str = dspy.OutputField(  # pyright: ignore[reportUnknownMemberType]
        desc="test_adapter.py: import via `from adapter import bind, call_tool`; only used imports"
    )


def coerce(pred: Any) -> dict[str, Any]:
    """Normalize a ``dspy.Prediction`` (or any attr-bag) into a plain dict."""
    return {
        "adapter_name": str(getattr(pred, "adapter_name", "")),
        "namespace": str(getattr(pred, "namespace", "")),
        "fixture": as_dict(getattr(pred, "fixture", {})),
        "adapter_source": str(getattr(pred, "adapter_source", "")),
        "test_source": str(getattr(pred, "test_source", "")),
    }


class AdapterProgram(SmithProgram):
    def __init__(self, *, load_compiled: bool = True) -> None:
        super().__init__(
            signature=AdapterSignature,
            coerce=coerce,
            load_compiled_demos=_ledger.load_compiled_demos,
            load_compiled=load_compiled,
        )
