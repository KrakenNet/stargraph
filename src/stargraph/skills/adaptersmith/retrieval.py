# SPDX-License-Identifier: Apache-2.0
"""RAG retrieval for the adapter smith — corpus = the MCP adapter + repo adapters.

The domain-agnostic ranking/formatting lives in ``_smith.retrieval``; this module
supplies the *adapter* corpus: the canonical MCP adapter (``adapters/mcp.py``,
always — it is the reference seam every adapter mirrors), the most relevant
sibling adapter modules under ``stargraph.adapters``, and gate-accepted
``(brief → adapter)`` ledger pairs. Best-effort — an unreadable file just yields
fewer snippets.
"""

from __future__ import annotations

from pathlib import Path

from stargraph.skills._smith.retrieval import Snippet, assemble_context, clip, format_context, rank
from stargraph.skills.adaptersmith import _ledger

__all__ = ["Snippet", "format_context", "retrieve_context"]

_SKIP = {"__init__.py"}


def _adapters_dir() -> Path:
    """Directory of the ``stargraph.adapters`` package (where the seams live)."""
    import stargraph.adapters

    return Path(stargraph.adapters.__file__).parent


def _repo_snippets(brief: str, k: int) -> list[Snippet]:
    """The MCP adapter contract (always) + the most relevant sibling adapters."""
    out: list[Snippet] = []
    adapters_dir = _adapters_dir()
    try:
        text = (adapters_dir / "mcp.py").read_text(encoding="utf-8")
        out.append(Snippet(source="repo:adapters/mcp.py (adapter contract)", text=clip(text)))
    except OSError:
        pass

    items: list[tuple[str, str]] = []
    try:
        files = sorted(adapters_dir.rglob("*.py"))
    except OSError:
        files = []
    for path in files:
        if path.name in _SKIP or path.name == "mcp.py":
            continue
        try:
            items.append(
                (path.relative_to(adapters_dir).as_posix(), path.read_text(encoding="utf-8"))
            )
        except OSError:
            continue
    for name, body in rank(brief, items, k):
        out.append(Snippet(source=f"repo:adapters/{name}", text=clip(body)))
    return out


def retrieve_context(brief: str, *, k: int = 4) -> list[Snippet]:
    """Top grounding snippets for ``brief``: repo source + accepted ledger pairs."""
    return assemble_context(
        brief,
        k=k,
        repo_snippets=_repo_snippets,
        recall_examples=_ledger.recall_examples,
        source_field="adapter_source",
    )
