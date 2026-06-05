# SPDX-License-Identifier: Apache-2.0
"""Harbor — orchestration framework for LLMs, ML models, tools, and CLIPS rules.

Public top-level surface (design §2.1, §5.2, task 2.8):

* :class:`Graph` — sync, hashable graph definition (FR-1).
* :class:`GraphRun` — async execution handle returned by ``await Graph(...).start(...)``.
* :func:`tool` — decorator that registers a callable as a Harbor tool (FR-33).
* :class:`Skill` / :class:`SkillKind` — extension point for tool-loop / RAG /
  research subgraphs (FR-21, design §3.6).
* :class:`RetrievalNode` / :class:`MemoryWriteNode` — first-class read/write
  nodes over the Store quintet (FR-26, design §5).
* Store Protocols (:class:`VectorStore`, :class:`GraphStore`, :class:`DocStore`,
  :class:`MemoryStore`, :class:`FactStore`) and their default Providers
  (:class:`LanceDBVectorStore`, :class:`RyuGraphStore`, :class:`SQLiteDocStore`,
  :class:`SQLiteMemoryStore`, :class:`SQLiteFactStore`) — design §5.
* IR helpers (:func:`dumps`, :func:`dumps_canonical`, :func:`loads`, :func:`validate`)
  and schema locators (:func:`schema_path`, :func:`schema_url`) inherited from the
  foundation surface.

``harbor.compare`` (FR-27, JSONPatch ``RunDiff``) and a top-level ``harbor.simulate``
helper are deliberately *not* re-exported here yet — ``compare`` lands in task 3.34
(``harbor.replay.compare``) and ``simulate`` is currently a method on ``Graph``
(stub at :meth:`harbor.graph.Graph.simulate`, full impl in task 3.43). They will
be re-exported from this module once those tasks ship.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

from .graph import Graph, GraphRun
from .ir import dumps, dumps_canonical, loads, validate
from .nodes.memory import MemoryWriteNode
from .nodes.retrieval import RetrievalNode
from .schemas import schema_path, schema_url
from .skills import Skill, SkillKind
from .stores import (
    DocStore,
    FactStore,
    GraphStore,
    MemoryStore,
    SQLiteDocStore,
    SQLiteFactStore,
    SQLiteMemoryStore,
    VectorStore,
)
from .tools import tool

if TYPE_CHECKING:
    from .stores import LanceDBVectorStore, RyuGraphStore


# Optional providers (require the ``stores`` extras). Re-export lazily via
# PEP 562 module-level ``__getattr__`` so ``import harbor`` succeeds without
# the stores-extra wheels (ryugraph / lancedb / pyarrow) installed -- the
# engine + serve subsystems load this module unconditionally.
_OPTIONAL_PROVIDERS: dict[str, str] = {
    "LanceDBVectorStore": "LanceDBVectorStore",
    "RyuGraphStore": "RyuGraphStore",
}


def __getattr__(name: str) -> Any:
    if name in _OPTIONAL_PROVIDERS:
        return getattr(importlib.import_module("harbor.stores"), _OPTIONAL_PROVIDERS[name])
    msg = f"module {__name__!r} has no attribute {name!r}"
    raise AttributeError(msg)

__version__ = "0.3.0"

__all__ = [
    "DocStore",
    "FactStore",
    "Graph",
    "GraphRun",
    "GraphStore",
    "RyuGraphStore",
    "LanceDBVectorStore",
    "MemoryStore",
    "MemoryWriteNode",
    "RetrievalNode",
    "SQLiteDocStore",
    "SQLiteFactStore",
    "SQLiteMemoryStore",
    "Skill",
    "SkillKind",
    "VectorStore",
    "__version__",
    "dumps",
    "dumps_canonical",
    "loads",
    "schema_path",
    "schema_url",
    "tool",
    "validate",
]
