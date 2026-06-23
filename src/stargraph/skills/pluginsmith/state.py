# SPDX-License-Identifier: Apache-2.0
"""Pluginsmith run state — the shared spine plus the plugin's domain output fields.

The generic fields (brief/model_id/output_dir, recalled grounding, build outputs,
landed path) live in :class:`stargraph.skills._smith.state.SmithState`; this adds
only what a *plugin* contributes: its name, the ``(namespace, tool_name)`` identity
of the tool it advertises, the ``tool_attr`` (the ``@tool`` callable's name in
``plugin.py`` the gate invokes), and the ``fixture`` (tool args + expected output +
the deny/allow action kinds) the contract tier drives the registered plugin
against. Linear graph (triage → recall → build → record); the bounded repair loop
lives inside ``build``.
"""

from __future__ import annotations

from typing import Any

from pydantic import Field

from stargraph.skills._smith.state import SmithState, VerifierResult

__all__ = ["State", "VerifierResult"]


class State(SmithState):
    plugin_name: str = ""
    namespace: str = ""
    tool_name: str = ""
    tool_attr: str = ""
    fixture: dict[str, Any] = Field(default_factory=dict)
