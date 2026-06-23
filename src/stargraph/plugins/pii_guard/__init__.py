# SPDX-License-Identifier: Apache-2.0
"""``pii_guard`` — reference PLUGIN example (redaction tool + governance hooks).

Demonstrates the *plugin* path (not a skill / subgraph): a ``@tool``-decorated
redaction coroutine plus cross-cutting governance hooks
(``authorize_action`` default-deny for PII writes, ``before_tool_call`` /
``after_tool_call`` audit). Deliberately NOT wired into ``pyproject`` entry
points — activating ``authorize_action`` globally would affect every other
graph and test. Tests register :mod:`stargraph.plugins.pii_guard.hooks` on a
fresh :class:`pluggy.PluginManager` instead.
"""

from __future__ import annotations
