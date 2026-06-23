# SPDX-License-Identifier: Apache-2.0
"""RAG retrieval for the store smith — corpus = the DocStore contract + repo stores.

The domain-agnostic ranking/formatting lives in ``_smith.retrieval``; this module
supplies the *store* corpus: the ``DocStore`` protocol + ``StoreHealth`` /
``MigrationPlan`` contract (always), the most relevant sibling store modules under
``stargraph.stores``, and gate-accepted ``(brief → store)`` ledger pairs.
Best-effort — an unreadable file just yields fewer snippets.
"""

from __future__ import annotations

from pathlib import Path

from stargraph.skills._smith.retrieval import Snippet, assemble_context, clip, format_context, rank
from stargraph.skills.storesmith import _ledger

__all__ = ["Snippet", "format_context", "retrieve_context"]

_SKIP = {"__init__.py", "doc.py", "_common.py", "_sqlite_base.py"}


def _stores_dir() -> Path:
    """Directory of the ``stargraph.stores`` package (where the protocol + impls live)."""
    import stargraph.stores

    return Path(stargraph.stores.__file__).parent


def _repo_snippets(brief: str, k: int) -> list[Snippet]:
    """The DocStore contract (always) + the most relevant sibling store modules."""
    out: list[Snippet] = []
    stores_dir = _stores_dir()
    for rel in ("doc.py", "_common.py"):
        try:
            text = (stores_dir / rel).read_text(encoding="utf-8")
            out.append(Snippet(source=f"repo:stores/{rel} (DocStore contract)", text=clip(text)))
        except OSError:
            continue

    items: list[tuple[str, str]] = []
    try:
        files = sorted(stores_dir.rglob("*.py"))
    except OSError:
        files = []
    for path in files:
        if path.name in _SKIP:
            continue
        try:
            rel = path.relative_to(stores_dir).as_posix()
            items.append((rel, path.read_text(encoding="utf-8")))
        except OSError:
            continue
    for name, body in rank(brief, items, k):
        out.append(Snippet(source=f"repo:stores/{name}", text=clip(body)))
    return out


def retrieve_context(brief: str, *, k: int = 4) -> list[Snippet]:
    """Top grounding snippets for ``brief``: repo source + accepted ledger pairs."""
    return assemble_context(
        brief,
        k=k,
        repo_snippets=_repo_snippets,
        recall_examples=_ledger.recall_examples,
        source_field="store_source",
    )
