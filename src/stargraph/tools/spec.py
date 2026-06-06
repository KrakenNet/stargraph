# SPDX-License-Identifier: Apache-2.0
"""Tool spec enums (FR-33, design 3.4.2).

These enums are the engine's in-place foundation extension to
:class:`stargraph.ir._models.ToolSpec`: ``side_effects: bool`` becomes
``side_effects: SideEffects`` and a new ``replay_policy: ReplayPolicy``
field is added (defaulting to ``must_stub``).

Both are ``StrEnum``\\ s so IR YAML/JSON carries plain strings on the wire.
``SideEffects`` values are the lowercase member names; ``ReplayPolicy``
values are kebab-cased per design 3.4.2. ``StrEnum`` is the modern
spelling of design 3.4.2's ``class X(str, Enum)`` (UP042); behavior is
identical for our wire-format needs (``SideEffects.write == "write"`` is
still ``True``).

This module imports nothing from :mod:`stargraph.ir` to keep the dependency
arrow one-way: ``stargraph.ir._models`` imports from ``stargraph.tools.spec``,
never the reverse.
"""

from __future__ import annotations

from enum import StrEnum


class SideEffects(StrEnum):
    """Tool side-effect classification (FR-33, design 3.4.2)."""

    none = "none"
    read = "read"
    write = "write"
    external = "external"


class ReplayPolicy(StrEnum):
    """Tool replay policy (FR-33, FR-21, NFR-8, design 3.4.2)."""

    must_stub = "must-stub"
    fail_loud = "fail-loud"
    recorded_result = "recorded-result"
