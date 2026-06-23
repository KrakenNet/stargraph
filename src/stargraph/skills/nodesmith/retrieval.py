# SPDX-License-Identifier: Apache-2.0
"""RAG retrieval for the node smith — corpus = repo node source + accepted pairs.

The domain-agnostic ranking/formatting lives in ``_smith.retrieval``; this module
supplies the *node* corpus: the ``NodeBase`` contract plus sibling node sources
under ``stargraph.nodes``, and gate-accepted ``(brief → node)`` ledger pairs.
Best-effort throughout — an unreadable file just yields fewer snippets.
"""

from __future__ import annotations

from pathlib import Path

from stargraph.skills._smith.retrieval import Snippet, assemble_context, clip, format_context, rank
from stargraph.skills.nodesmith import _ledger

__all__ = ["Snippet", "format_context", "retrieve_context"]


def _nodes_dir() -> Path:
    """Directory of the ``stargraph.nodes`` package (where NodeBase + examples live)."""
    from stargraph.nodes import base

    return Path(base.__file__).parent


def _repo_snippets(brief: str, k: int) -> list[Snippet]:
    """The NodeBase contract (always) + the most relevant sibling node files."""
    out: list[Snippet] = []
    nodes_dir = _nodes_dir()
    try:
        contract = (nodes_dir / "base.py").read_text(encoding="utf-8")
        out.append(Snippet(source="repo:nodes/base.py (NodeBase contract)", text=clip(contract)))
    except OSError:
        pass

    items: list[tuple[str, str]] = []
    try:
        files = sorted(nodes_dir.glob("*.py"))
    except OSError:
        files = []
    for path in files:
        if path.name in ("__init__.py", "base.py"):
            continue
        try:
            items.append((path.name, path.read_text(encoding="utf-8")))
        except OSError:
            continue
    for name, body in rank(brief, items, k):
        out.append(Snippet(source=f"repo:nodes/{name}", text=clip(body)))
    return out


def retrieve_context(brief: str, *, k: int = 4) -> list[Snippet]:
    """Top grounding snippets for ``brief``: repo source + accepted ledger pairs."""
    return assemble_context(
        brief,
        k=k,
        repo_snippets=_repo_snippets,
        recall_examples=_ledger.recall_examples,
        source_field="node_source",
    )
