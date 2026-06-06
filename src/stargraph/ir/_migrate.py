# SPDX-License-Identifier: Apache-2.0
"""Foundation v0.1 -> engine v0.2 IR migration shim (FR-33).

The foundation v0.1 :class:`~stargraph.ir._models.ToolSpec` carried
``side_effects: bool``; the engine v0.2 surface (FR-33, design 3.4.2)
replaces that with the :class:`~stargraph.tools.spec.SideEffects` enum and
adds a :class:`~stargraph.tools.spec.ReplayPolicy` field defaulting to
``must_stub``. Legacy IR documents authored against v0.1 must still
load -- with a one-shot :class:`DeprecationWarning` per ``side_effects``
occurrence -- so downstream pipelines have a sane upgrade path.

This module is *not* imported from ``stargraph.ir._models`` (FR-7 / AC-13.1
forbid validator decorators on the IR models to keep JSON-Schema
round-trip pure). Instead it is a standalone preprocessing helper:
callers run the dict through :func:`coerce_legacy_tool_spec` *before*
:meth:`ToolSpec.model_validate`.

Mapping (v0.1 -> v0.2):

* ``side_effects: True``  -> ``SideEffects.write``  (writes are the
  conservative interpretation of "has side effects").
* ``side_effects: False`` -> ``SideEffects.none``.
* missing ``replay_policy`` -> left absent (Pydantic default of
  ``ReplayPolicy.must_stub`` applies on validate).
"""

from __future__ import annotations

import warnings
from typing import Any

from stargraph.tools.spec import SideEffects

__all__ = ["coerce_legacy_tool_spec"]


def coerce_legacy_tool_spec(data: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of ``data`` with v0.1 fields up-converted to v0.2.

    Emits :class:`DeprecationWarning` exactly when ``side_effects`` is a
    bool. The dict is shallow-copied so callers' inputs are not mutated.
    """
    out = dict(data)
    raw = out.get("side_effects")
    if isinstance(raw, bool):
        warnings.warn(
            (
                "ToolSpec.side_effects=bool is foundation v0.1 syntax; "
                "use SideEffects enum (write/none/read/external) -- "
                "engine v0.2 (FR-33)."
            ),
            DeprecationWarning,
            stacklevel=2,
        )
        out["side_effects"] = SideEffects.write if raw else SideEffects.none
    return out
