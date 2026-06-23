# SPDX-License-Identifier: Apache-2.0
"""RAG retrieval for the tool smith — corpus = the @tool contract + repo tools.

The domain-agnostic ranking/formatting lives in ``_smith.retrieval``; this module
supplies the *tool* corpus: the ``@tool`` decorator + side-effect enums (always),
the most relevant sibling tool modules under ``stargraph.tools``, and gate-accepted
``(brief → tool)`` ledger pairs. Best-effort — an unreadable file just yields
fewer snippets.
"""

from __future__ import annotations

from pathlib import Path

from stargraph.skills._smith.retrieval import Snippet, assemble_context, clip, format_context, rank
from stargraph.skills.toolsmith import _ledger

__all__ = ["Snippet", "format_context", "retrieve_context"]

_SKIP = {"__init__.py", "decorator.py", "spec.py", "_auth.py"}


def _tools_dir() -> Path:
    """Directory of the ``stargraph.tools`` package (where @tool + examples live)."""
    import stargraph.tools

    return Path(stargraph.tools.__file__).parent


def _repo_snippets(brief: str, k: int) -> list[Snippet]:
    """The @tool contract (always) + the most relevant sibling tool modules."""
    out: list[Snippet] = []
    tools_dir = _tools_dir()
    for rel in ("decorator.py", "spec.py"):
        try:
            text = (tools_dir / rel).read_text(encoding="utf-8")
            out.append(Snippet(source=f"repo:tools/{rel} (@tool contract)", text=clip(text)))
        except OSError:
            continue

    items: list[tuple[str, str]] = []
    try:
        files = sorted(tools_dir.rglob("*.py"))
    except OSError:
        files = []
    for path in files:
        if path.name in _SKIP:
            continue
        try:
            items.append((path.relative_to(tools_dir).as_posix(), path.read_text(encoding="utf-8")))
        except OSError:
            continue
    for name, body in rank(brief, items, k):
        out.append(Snippet(source=f"repo:tools/{name}", text=clip(body)))
    return out


def retrieve_context(brief: str, *, k: int = 4) -> list[Snippet]:
    """Top grounding snippets for ``brief``: repo source + accepted ledger pairs."""
    return assemble_context(
        brief,
        k=k,
        repo_snippets=_repo_snippets,
        recall_examples=_ledger.recall_examples,
        source_field="tool_source",
    )
