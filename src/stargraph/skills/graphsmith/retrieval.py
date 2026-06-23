# SPDX-License-Identifier: Apache-2.0
"""RAG retrieval for the graph smith — corpus = the NodeBase contract + repo nodes.

The domain-agnostic ranking/formatting lives in ``_smith.retrieval``; this module
supplies the *graph* corpus: the ``NodeBase`` contract (``nodes/base.py``, always)
plus the most relevant sibling node modules under ``stargraph.nodes`` (real
``execute`` implementations the generator can pattern-match), and gate-accepted
``(brief → bundle)`` ledger pairs. Best-effort — an unreadable file just yields
fewer snippets.
"""

from __future__ import annotations

from pathlib import Path

from stargraph.skills._smith.retrieval import Snippet, assemble_context, clip, format_context, rank
from stargraph.skills.graphsmith import _ledger

__all__ = ["Snippet", "format_context", "retrieve_context"]

_SKIP = {"__init__.py", "base.py"}


def _nodes_dir() -> Path:
    """Directory of the ``stargraph.nodes`` package (the NodeBase contract + impls)."""
    import stargraph.nodes

    return Path(stargraph.nodes.__file__).parent


def _repo_snippets(brief: str, k: int) -> list[Snippet]:
    """The NodeBase contract (always) + the most relevant sibling node modules."""
    out: list[Snippet] = []
    nodes_dir = _nodes_dir()
    try:
        text = (nodes_dir / "base.py").read_text(encoding="utf-8")
        out.append(Snippet(source="repo:nodes/base.py (NodeBase contract)", text=clip(text)))
    except OSError:
        pass

    items: list[tuple[str, str]] = []
    try:
        files = sorted(nodes_dir.rglob("*.py"))
    except OSError:
        files = []
    for path in files:
        if path.name in _SKIP:
            continue
        try:
            rel = path.relative_to(nodes_dir).as_posix()
            items.append((rel, path.read_text(encoding="utf-8")))
        except OSError:
            continue
    for name, body in rank(brief, items, k):
        out.append(Snippet(source=f"repo:nodes/{name}", text=clip(body)))
    return out


def retrieve_context(brief: str, *, k: int = 4) -> list[Snippet]:
    """Top grounding snippets for ``brief``: repo nodes + accepted ledger bundles."""
    return assemble_context(
        brief,
        k=k,
        repo_snippets=_repo_snippets,
        recall_examples=_ledger.recall_examples,
        source_field="nodes_source",
    )
