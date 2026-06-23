# SPDX-License-Identifier: Apache-2.0
"""RAG retrieval for the pack smith — corpus = a real CLIPS pack + the signing contract.

The domain-agnostic ranking/formatting lives in ``_smith.retrieval``; this module
supplies the *rule pack* corpus: a shipped built-in pack's ``rules.clp`` (the
``bosun/budgets`` pack — a real deftemplate + threshold defrule example) and the pack
signing contract (``bosun/signing.py`` — how a pack tree is signed/verified) — both
always included since each grounds a correct, deployable pack — interleaved with
gate-accepted ``(brief → rules.clp)`` ledger pairs. Best-effort: an unreadable file just
yields fewer snippets.
"""

from __future__ import annotations

from pathlib import Path

from stargraph.skills._smith.retrieval import Snippet, assemble_context, clip, format_context
from stargraph.skills.packsmith import _ledger

__all__ = ["Snippet", "format_context", "retrieve_context"]


def _stargraph_root() -> Path | None:
    try:
        import stargraph

        return Path(stargraph.__file__).resolve().parent
    except (ImportError, AttributeError, TypeError):
        return None


# (relative path under the stargraph package, label) for each fixed contract file.
_CONTRACTS = (
    ("bosun/budgets/rules.clp", "repo:bosun/budgets/rules.clp (a real CLIPS governance pack)"),
    ("bosun/signing.py", "repo:bosun/signing.py (how a pack tree is signed + verified)"),
)


def _repo_snippets(brief: str, k: int) -> list[Snippet]:
    """The pack contracts the generator must honor — always included, in order."""
    root = _stargraph_root()
    if root is None:
        return []
    out: list[Snippet] = []
    for relpath, label in _CONTRACTS:
        try:
            text = (root / relpath).read_text(encoding="utf-8")
        except OSError:
            continue
        out.append(Snippet(source=label, text=clip(text)))
    return out


def retrieve_context(brief: str, *, k: int = 4) -> list[Snippet]:
    """Top grounding snippets for ``brief``: a real CLIPS pack + signing + accepted packs."""
    return assemble_context(
        brief,
        k=k,
        repo_snippets=_repo_snippets,
        recall_examples=_ledger.recall_examples,
        source_field="rules_clp",
    )
