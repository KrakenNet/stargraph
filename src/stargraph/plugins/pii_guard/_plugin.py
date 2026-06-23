# SPDX-License-Identifier: Apache-2.0
"""``pii_guard`` plugin surface — the ``register_tools`` hookimpl.

A real plugin distribution exposes this module under the ``stargraph.tools``
entry-point group; the loader calls :func:`register_tools` during stage 2 to
collect the tools the plugin provides. This example is intentionally NOT wired
into ``pyproject`` (see :mod:`stargraph.plugins.pii_guard`), so it is only
exercised by registering it on a fresh :class:`pluggy.PluginManager` in tests.

The redact tool's :class:`~stargraph.ir._models.ToolSpec` is read off the
``@tool`` wrapper's ``.spec`` attribute (the public Tool surface from
:func:`stargraph.tools.decorator.tool`).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from stargraph.plugin._markers import hookimpl
from stargraph.plugins.pii_guard.redact import redact_pii

if TYPE_CHECKING:
    from stargraph.ir._models import ToolSpec

__all__: list[str] = []


@hookimpl
def register_tools() -> list[ToolSpec]:
    """Surface the ``pii_guard.redact_pii`` tool spec to the loader."""
    return [cast("ToolSpec", redact_pii.spec)]  # pyright: ignore[reportFunctionMemberAccess]
