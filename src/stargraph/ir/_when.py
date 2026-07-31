# SPDX-License-Identifier: Apache-2.0
"""Compile the ``RuleSpec.when`` mapping sugar to a CLIPS left-hand side.

``RuleSpec.when`` accepts two shapes:

* **str** -- raw CLIPS patterns, passed to the engine verbatim (the
  power-user escape hatch; unchanged behavior).
* **mapping** -- the sugar for the overwhelmingly common node-state
  rule. The reserved ``node`` key names the node the rule fires on;
  every other key is a Mirror-annotated state field (its resolved
  template name) compared for string equality against the mirrored
  ``value`` slot::

      when: {node: work, phase_verdict: refine}

  compiles to::

      (node-id (id work)) (phase_verdict (value "refine"))

Conditions AND together (CLIPS pattern conjunction). Values are
compared as strings because :meth:`FathomAdapter.mirror_state` asserts
every mirrored field as ``{"value": str(...)}``. Dotted template names
are dot-mangled to the engine's registered spelling (fathom restricts
identifiers to ``[A-Za-z_][A-Za-z0-9_-]*``; see
``stargraph.fathom._ir_builder._clips_name``).

Lives outside :mod:`stargraph.ir._models` for the same reason as
:mod:`stargraph.ir._backfill`: the IR models forbid hidden Python
behavior (FR-7, AC-13.1). Shape errors raise :class:`ValueError` here;
:func:`stargraph.ir.validate` surfaces them as structured
``ValidationError`` rows at ``/rules/<idx>/when``.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = ["NODE_KEY", "compile_when", "when_node_ref"]

#: Reserved sugar key naming the node the rule fires on.
NODE_KEY = "node"

# Node-id charset mirrors ``_backfill.NODE_ID_PATTERN`` (alnum,
# underscore, hyphen, dot -- the stable-id grammar).
_NODE_REF_RE = re.compile(r"^[A-Za-z0-9_.\-]+$")

# CLIPS template identifier, checked after dot-mangling.
_FIELD_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_\-]*$")

_SCALARS = (str, int, float, bool)


def _clips_string(value: str) -> str:
    """Escape ``value`` for interpolation inside a CLIPS string literal."""
    return value.replace("\\", "\\\\").replace('"', '\\"')


def compile_when(when: str | Mapping[str, Any]) -> str:
    """Return the CLIPS LHS for ``when``: strings verbatim, mappings compiled.

    Raises:
        ValueError: on an empty mapping, a malformed ``node`` value, a
            field key that cannot become a CLIPS identifier, or a
            non-scalar condition value.
    """
    if isinstance(when, str):
        return when
    if not when:
        raise ValueError(
            "when mapping is empty; give a node key and/or mirrored "
            "state-field conditions, e.g. {node: work, verdict: pass}"
        )
    patterns: list[str] = []
    node_ref = when.get(NODE_KEY)
    if NODE_KEY in when:
        if not isinstance(node_ref, str) or not _NODE_REF_RE.match(node_ref):
            raise ValueError(f"when.node must be a node id ([A-Za-z0-9_.-]+), got {node_ref!r}")
        patterns.append(f"(node-id (id {node_ref}))")
    for key, value in when.items():
        if key == NODE_KEY:
            continue
        field = key.replace(".", "-")  # engine registers dotted templates mangled
        if not _FIELD_RE.match(field):
            raise ValueError(
                f"when key {key!r} is not a valid fact-template name ([A-Za-z_][A-Za-z0-9_.-]*)"
            )
        if not isinstance(value, _SCALARS):
            raise ValueError(
                f"when.{key} must be a scalar (str/int/float/bool) compared "
                f"as a string against the mirrored value; got {type(value).__name__}"
            )
        patterns.append(f'({field} (value "{_clips_string(str(value))}"))')
    return " ".join(patterns)


def when_node_ref(when: str | Mapping[str, Any]) -> str | None:
    """The sugar's ``node`` value, or ``None`` (raw strings included).

    Companion to :func:`stargraph.ir.backfill_rule_node_ids`, which
    regex-extracts ownership from raw ``when`` strings; mapping sugar
    declares it directly.
    """
    if isinstance(when, str):
        return None
    ref = when.get(NODE_KEY)
    return ref if isinstance(ref, str) else None
