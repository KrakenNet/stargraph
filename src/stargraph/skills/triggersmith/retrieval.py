# SPDX-License-Identifier: Apache-2.0
"""RAG retrieval for the trigger smith — corpus = the Trigger Protocol + repo triggers.

The domain-agnostic ranking/formatting lives in ``_smith.retrieval``; this module
supplies the *trigger* corpus: the ``Trigger`` Protocol contract (``__init__.py``,
always), the most relevant sibling trigger modules under ``stargraph.triggers``,
and gate-accepted ``(brief → trigger)`` ledger pairs. Best-effort — an unreadable
file just yields fewer snippets.
"""

from __future__ import annotations

from pathlib import Path

from stargraph.skills._smith.retrieval import Snippet, assemble_context, clip, format_context, rank
from stargraph.skills.triggersmith import _ledger

__all__ = ["Snippet", "format_context", "retrieve_context"]

_SKIP = {"__init__.py"}


def _triggers_dir() -> Path:
    """Directory of the ``stargraph.triggers`` package (where the Protocol + impls live)."""
    import stargraph.triggers

    return Path(stargraph.triggers.__file__).parent


def _repo_snippets(brief: str, k: int) -> list[Snippet]:
    """The Trigger Protocol (always) + the most relevant sibling trigger modules."""
    out: list[Snippet] = []
    triggers_dir = _triggers_dir()
    try:
        text = (triggers_dir / "__init__.py").read_text(encoding="utf-8")
        out.append(Snippet(source="repo:triggers/__init__.py (Trigger Protocol)", text=clip(text)))
    except OSError:
        pass

    items: list[tuple[str, str]] = []
    try:
        files = sorted(triggers_dir.rglob("*.py"))
    except OSError:
        files = []
    for path in files:
        if path.name in _SKIP:
            continue
        try:
            rel = path.relative_to(triggers_dir).as_posix()
            items.append((rel, path.read_text(encoding="utf-8")))
        except OSError:
            continue
    for name, body in rank(brief, items, k):
        out.append(Snippet(source=f"repo:triggers/{name}", text=clip(body)))
    return out


def retrieve_context(brief: str, *, k: int = 4) -> list[Snippet]:
    """Top grounding snippets for ``brief``: repo source + accepted ledger pairs."""
    return assemble_context(
        brief,
        k=k,
        repo_snippets=_repo_snippets,
        recall_examples=_ledger.recall_examples,
        source_field="trigger_source",
    )
