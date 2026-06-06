# SPDX-License-Identifier: Apache-2.0
"""Reranker entry-point loader (FR-16, AC-4.4, design §3.8).

Resolves a reranker name against the ``stargraph.rerankers`` entry-point
group, falling back to the always-available :class:`RRFReranker` when
no name is supplied. Heavier rerankers (cross-encoder, Cohere, Jina)
ship as opt-in plugins under that group.
"""

from __future__ import annotations

from importlib.metadata import entry_points

from stargraph.stores.rerankers import Reranker, RRFReranker

__all__ = ["load_reranker"]

_GROUP = "stargraph.rerankers"


def load_reranker(name: str | None) -> Reranker:
    """Load a reranker by name from the ``stargraph.rerankers`` entry-point group.

    Returns :class:`RRFReranker` when ``name`` is ``None`` or empty.
    Raises :class:`KeyError` when ``name`` is given but no matching
    entry point is registered.
    """
    if not name:
        return RRFReranker()
    for ep in entry_points(group=_GROUP):
        if ep.name == name:
            factory = ep.load()
            return factory()
    raise KeyError(f"Reranker '{name}' not found")
