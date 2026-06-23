# SPDX-License-Identifier: Apache-2.0
"""PluginProgram — the DSPy generator for plugins, bound to the shared SmithProgram.

The generate/forward/demo plumbing lives in :class:`SmithProgram`; this module
supplies the *plugin* signature (the fields a plugin generation emits) and
``coerce`` (Prediction → plain dict). LM construction + ``clarify`` are re-exported
from the shared core so callers import them from here.
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
from stargraph.skills.pluginsmith import _ledger

# Re-exported from the shared core so callers keep importing them from here.
__all__ = [
    "DEFAULT_OLLAMA_URL",
    "INPUT_FIELDS",
    "PluginProgram",
    "PluginSignature",
    "clarify",
    "coerce",
    "configure_lm",
    "make_lm",
]


class PluginSignature(dspy.Signature):  # pyright: ignore[reportUnknownMemberType]
    """Write one Stargraph plugin (a single ``plugin.py`` module) from a brief.

    A Stargraph plugin contributes a tool plus governance/audit hooks through
    pluggy. You emit ONE module; the gate registers it on an isolated
    ``PluginManager`` and drives it for real, so every piece must actually work.
    Honor every lesson in ``lessons`` and fix every issue in ``last_findings``.

    ``plugin_source`` is saved as ``plugin.py`` and MUST define, all at module level
    (no classes needed):

    - One ``@tool``-decorated function — the tool the plugin provides. Decorate with
      ``from stargraph.tools import SideEffects, tool`` then
      ``@tool(name=<tool_name>, namespace=<namespace>, version="1.0.0",
      side_effects=SideEffects.none)``. The function takes simple typed args and
      RETURNS its result directly (keep it SYNCHRONOUS — not ``async``). Its name is
      ``tool_attr``.
    - ``@hookimpl`` functions (import ``from stargraph.plugin import hookimpl``),
      with these EXACT names and parameter names:
      - ``register_tools()`` → ``return [<tool_attr>.spec]`` (the decorated function
        exposes a ``.spec`` attribute).
      - ``authorize_action(action)`` → return ``False`` to DENY when
        ``action.action_kind`` is the dangerous kind this plugin guards, else return
        ``None`` to abstain (NEVER return ``True`` blanket-allow). ``action`` is a
        ``BosunAction`` with ``.action_kind`` / ``.target`` / ``.payload``.
      - ``before_tool_call(call)`` and ``after_tool_call(call, result)`` → audit
        hooks; record to a module-level list and return ``None``. ``call`` is a
        ``ToolCall`` (``.tool_name`` / ``.namespace`` / ``.args``).

    Put every import at module top level. Do NOT import anything you do not use (an
    unused import fails the static gate).

    ``test_source`` is saved as ``test_plugin.py`` BESIDE it. It MUST import from the
    module by bare name (``from plugin import <tool_attr>``) and unit-test the tool
    with plain ``def test_*()`` + ``assert``. Do NOT ``import pytest`` or import
    anything unused.

    ``fixture`` drives the contract run:
    - ``tool_args``: the kwargs to call ``<tool_attr>`` with.
    - ``tool_expects``: the exact value ``<tool_attr>(**tool_args)`` must return.
    - ``deny_kind``: an ``action_kind`` string ``authorize_action`` must DENY.
    - ``allow_kind``: a DIFFERENT ``action_kind`` string it must abstain on.
    """

    brief: str = dspy.InputField(desc="what the plugin should do")  # pyright: ignore[reportUnknownMemberType]
    lessons: list[str] = dspy.InputField(desc="past failures to avoid")  # pyright: ignore[reportUnknownMemberType]
    last_findings: list[dict[str, Any]] = dspy.InputField(desc="prior attempt findings")  # pyright: ignore[reportUnknownMemberType]
    relevant_context: str = dspy.InputField(  # pyright: ignore[reportUnknownMemberType]
        desc="grounding: hookspecs + @tool decorator + plugin types + accepted examples + web"
    )

    plugin_name: str = dspy.OutputField(desc="a short kebab-case id for the plugin")  # pyright: ignore[reportUnknownMemberType]
    namespace: str = dspy.OutputField(desc="the tool namespace (slug)")  # pyright: ignore[reportUnknownMemberType]
    tool_name: str = dspy.OutputField(desc="the tool name within the namespace (slug)")  # pyright: ignore[reportUnknownMemberType]
    tool_attr: str = dspy.OutputField(  # pyright: ignore[reportUnknownMemberType]
        desc="the @tool function's python name in plugin.py"
    )
    plugin_source: str = dspy.OutputField(  # pyright: ignore[reportUnknownMemberType]
        desc="plugin.py: the @tool callable + register_tools/authorize_action/audit @hookimpls"
    )
    fixture: dict[str, Any] = dspy.OutputField(  # pyright: ignore[reportUnknownMemberType]
        desc="{tool_args: {...}, tool_expects: value, deny_kind: str, allow_kind: str}"
    )
    test_source: str = dspy.OutputField(  # pyright: ignore[reportUnknownMemberType]
        desc="test_plugin.py: import via `from plugin import <tool_attr>`; only used imports"
    )


def coerce(pred: Any) -> dict[str, Any]:
    """Normalize a ``dspy.Prediction`` (or any attr-bag) into a plain dict."""
    return {
        "plugin_name": str(getattr(pred, "plugin_name", "")),
        "namespace": str(getattr(pred, "namespace", "")),
        "tool_name": str(getattr(pred, "tool_name", "")),
        "tool_attr": str(getattr(pred, "tool_attr", "")),
        "plugin_source": str(getattr(pred, "plugin_source", "")),
        "fixture": as_dict(getattr(pred, "fixture", {})),
        "test_source": str(getattr(pred, "test_source", "")),
    }


class PluginProgram(SmithProgram):
    def __init__(self, *, load_compiled: bool = True) -> None:
        super().__init__(
            signature=PluginSignature,
            coerce=coerce,
            load_compiled_demos=_ledger.load_compiled_demos,
            load_compiled=load_compiled,
        )
