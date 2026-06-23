# SPDX-License-Identifier: Apache-2.0
"""Hand-authored, gate-verified seed skills — the trainset cold start.

Each entry is a verified ``(brief → skill)`` pair: a subgraph bundle (``state.py``
+ ``nodes.py`` + ``test_nodes.py``) plus the manifest domain fields (kind,
description, requires, system_prompt) and the ``fixture`` the contract tier runs
the assembled subgraph against. Seed 1 is a ``workflow`` skill (normalize →
classify); seed 2 is a ``utility`` skill (tokenize → count). Both are two-node
pipelines whose second node reads a channel the first wrote, so the contract
``expects`` only holds if the nodes wired end-to-end — and both manifests construct
a valid :class:`~stargraph.skills.base.Skill`. ``id`` is a fixed literal so
``seed_trainset`` is idempotent across runs.

``tests/integration/skillsmith/test_seeds.py`` runs every pair through
``gate.verify_sources`` — if a seed stops passing, that test fails.
"""

from __future__ import annotations

from typing import Any

# --- Seed 1: normalize → classify (workflow) --------------------------------- #
_TRIAGE_STATE = """\
from __future__ import annotations

from pydantic import BaseModel


class State(BaseModel):
    raw: str = ""
    normalized: str = ""
    label: str = ""
"""

_TRIAGE_NODES = '''\
from __future__ import annotations

from typing import Any

from stargraph.nodes.base import NodeBase


class Normalize(NodeBase):
    """Lower-case and strip the raw input into the ``normalized`` channel."""

    async def execute(self, state: Any, ctx: Any) -> dict[str, Any]:
        return {"normalized": str(state.raw).strip().lower()}


class Classify(NodeBase):
    """Read the ``normalized`` channel Normalize wrote and label the alert."""

    async def execute(self, state: Any, ctx: Any) -> dict[str, Any]:
        text = state.normalized
        return {"label": "alert" if ("error" in text or "fail" in text) else "ok"}
'''

_TRIAGE_TEST = """\
import asyncio
from types import SimpleNamespace

from nodes import Classify, Normalize


def test_normalize_strips_and_lowercases() -> None:
    out = asyncio.run(Normalize().execute(SimpleNamespace(raw="  ERROR Detected "), None))
    assert out["normalized"] == "error detected"


def test_classify_flags_alert() -> None:
    out = asyncio.run(Classify().execute(SimpleNamespace(normalized="error detected"), None))
    assert out["label"] == "alert"


def test_classify_ok_when_clean() -> None:
    out = asyncio.run(Classify().execute(SimpleNamespace(normalized="all good"), None))
    assert out["label"] == "ok"
"""

_TRIAGE_FIXTURE: dict[str, Any] = {
    "inputs": {"raw": "  ERROR on prod DB  "},
    "expects": {"normalized": "error on prod db", "label": "alert"},
}

# --- Seed 2: tokenize → count (utility) -------------------------------------- #
_COUNT_STATE = """\
from __future__ import annotations

from pydantic import BaseModel, Field


class State(BaseModel):
    text: str = ""
    tokens: list[str] = Field(default_factory=list)
    count: int = 0
"""

_COUNT_NODES = '''\
from __future__ import annotations

from typing import Any

from stargraph.nodes.base import NodeBase


class Split(NodeBase):
    """Whitespace-tokenize ``text`` into the ``tokens`` channel."""

    async def execute(self, state: Any, ctx: Any) -> dict[str, Any]:
        return {"tokens": str(state.text).split()}


class Count(NodeBase):
    """Read the ``tokens`` channel Split wrote and count them."""

    async def execute(self, state: Any, ctx: Any) -> dict[str, Any]:
        return {"count": len(state.tokens)}
'''

_COUNT_TEST = """\
import asyncio
from types import SimpleNamespace

from nodes import Count, Split


def test_split_tokenizes() -> None:
    out = asyncio.run(Split().execute(SimpleNamespace(text="a b c"), None))
    assert out["tokens"] == ["a", "b", "c"]


def test_count_counts_tokens() -> None:
    out = asyncio.run(Count().execute(SimpleNamespace(tokens=["a", "b", "c"]), None))
    assert out["count"] == 3
"""

_COUNT_FIXTURE: dict[str, Any] = {
    "inputs": {"text": "the quick brown fox"},
    "expects": {"tokens": ["the", "quick", "brown", "fox"], "count": 4},
}


def _pair(
    seed_id: str,
    brief: str,
    skill_name: str,
    kind: str,
    description: str,
    node_classes: list[str],
    state_source: str,
    nodes_source: str,
    test_source: str,
    requires: list[str],
    system_prompt: str,
    fixture: dict[str, Any],
) -> dict[str, Any]:
    return {
        "id": seed_id,
        "brief": brief,
        "skill_name": skill_name,
        "kind": kind,
        "description": description,
        "node_classes": node_classes,
        "state_source": state_source,
        "nodes_source": nodes_source,
        "test_source": test_source,
        "requires": requires,
        "system_prompt": system_prompt,
        "fixture": fixture,
        "attempts": 1,
        "passed": True,
        "verdict": "accept",
    }


SEEDS: list[dict[str, Any]] = [
    _pair(
        "a0010000001",
        "a workflow skill that normalizes an alert string then labels it alert or ok",
        "alert-triage",
        "workflow",
        "Normalize an alert string and label it alert or ok.",
        ["Normalize", "Classify"],
        _TRIAGE_STATE,
        _TRIAGE_NODES,
        _TRIAGE_TEST,
        [],
        "",
        _TRIAGE_FIXTURE,
    ),
    _pair(
        "a0010000002",
        "a utility skill that tokenizes text then counts the tokens",
        "token-counter",
        "utility",
        "Tokenize text and count the tokens.",
        ["Split", "Count"],
        _COUNT_STATE,
        _COUNT_NODES,
        _COUNT_TEST,
        [],
        "",
        _COUNT_FIXTURE,
    ),
]
