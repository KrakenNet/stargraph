# SPDX-License-Identifier: Apache-2.0
"""Public ``stargraph.tools`` surface (FR-33, design §3.4).

Re-exports the tool spec enums (:class:`SideEffects`, :class:`ReplayPolicy`)
and the foundation's :class:`ToolSpec`, which the engine extends in-place
per FR-33 / interview Q3a.

:class:`ToolSpec` is exposed via PEP 562 module ``__getattr__`` to break the
circular import: ``stargraph.ir._models`` imports the enums from
:mod:`stargraph.tools.spec` at module-load time, so re-exporting ``ToolSpec``
eagerly here would re-enter ``stargraph.ir._models`` before its class body has
finished executing. Lazy access is sufficient because callers reach for
``stargraph.tools.ToolSpec`` only after import time.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from stargraph.tools.decorator import tool
from stargraph.tools.spec import ReplayPolicy, SideEffects

if TYPE_CHECKING:
    from stargraph.ir._models import ToolSpec

__all__ = ["ReplayPolicy", "SideEffects", "ToolSpec", "tool"]


def __getattr__(name: str) -> Any:
    if name == "ToolSpec":
        from stargraph.ir._models import ToolSpec as _ToolSpec

        return _ToolSpec
    raise AttributeError(f"module 'stargraph.tools' has no attribute {name!r}")
