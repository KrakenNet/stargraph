# SPDX-License-Identifier: Apache-2.0
"""Canonical IR serialization -- the single entry point for ``json.dumps``.

This module is the *only* place inside ``src/stargraph/`` that calls
``model.model_dump`` / ``model.model_validate_json`` / ``json.dumps`` on IR
types (FR-15, AC-11.4). Every other caller goes through :func:`dumps`,
:data:`dumps_canonical`, or :func:`loads` so the wire shape stays
deterministic and round-trip-stable (AC-11.1, AC-11.2).

* :func:`dumps` -- ``mode='json'`` + ``exclude_defaults=True`` -> ``json.dumps``
  with ``ensure_ascii=False`` and ``separators=(',', ':')`` (compact form).
  ``hashable=False`` (default) preserves Pydantic v2 declared field order;
  ``hashable=True`` sorts keys for content-addressable hashing.
* :data:`dumps_canonical` -- ``functools.partial(dumps, hashable=True)``,
  the pinned canonical-form name used by ID computation and golden tests.
* :func:`loads` -- ``model.model_validate_json(text)``; ``model`` defaults
  to :class:`IRDocument` but any :class:`IRBase` subclass works.
"""

from __future__ import annotations

import functools
import json
from typing import overload

from ._models import IRBase, IRDocument

__all__ = ["dumps", "dumps_canonical", "loads"]


def dumps(ir: IRBase, *, hashable: bool = False) -> str:
    """Canonically serialize an IR Pydantic model to JSON text.

    Single canonical entry point (FR-15, AC-11.4): the only caller of
    ``model_dump`` / ``json.dumps`` for IR types in ``src/stargraph/``.
    ``exclude_defaults=True`` keeps the round-trip stable (AC-11.1, AC-11.2).

    ``hashable=True`` sorts keys (for content-addressable IDs); the default
    ``hashable=False`` preserves Pydantic v2 declared field order.
    """
    data = ir.model_dump(mode="json", exclude_defaults=True)
    return json.dumps(
        data,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=hashable,
    )


dumps_canonical = functools.partial(dumps, hashable=True)
"""Pinned canonical-form alias (AC-11.4): ``dumps(..., hashable=True)``."""


@overload
def loads(text: str) -> IRDocument: ...
@overload
def loads[T: IRBase](text: str, model: type[T]) -> T: ...
def loads(text: str, model: type[IRBase] = IRDocument) -> IRBase:
    """Parse JSON ``text`` and validate against ``model`` (default :class:`IRDocument`)."""
    return model.model_validate_json(text)
