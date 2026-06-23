# SPDX-License-Identifier: Apache-2.0
"""RAG retrieval for the plugin smith — corpus = the plugin + @tool contracts.

The domain-agnostic ranking/formatting lives in ``_smith.retrieval``; this module
supplies the *plugin* corpus: the pluggy hookspec contract (``plugin/hookspecs.py``,
the hooks a plugin implements), the ``@tool`` decorator (``tools/decorator.py``, how
the callable gets its ``.spec``), and the plugin payload types (``plugin/types.py``,
``BosunAction``/``ToolCall``) — all always included since each is load-bearing for a
correct plugin — interleaved with gate-accepted ``(brief → plugin)`` ledger pairs.
Best-effort: an unreadable contract file just yields fewer snippets.
"""

from __future__ import annotations

from pathlib import Path

from stargraph.skills._smith.retrieval import Snippet, assemble_context, clip, format_context
from stargraph.skills.pluginsmith import _ledger

__all__ = ["Snippet", "format_context", "retrieve_context"]

# (import module, label) for each fixed contract the generator must honor.
_CONTRACTS = (
    ("stargraph.plugin.hookspecs", "repo:plugin/hookspecs.py (the hooks a plugin implements)"),
    ("stargraph.tools.decorator", "repo:tools/decorator.py (the @tool decorator → .spec)"),
    ("stargraph.plugin.types", "repo:plugin/types.py (BosunAction / ToolCall payloads)"),
)


def _repo_snippets(brief: str, k: int) -> list[Snippet]:
    """The plugin contracts the generator must honor — always included, in order."""
    out: list[Snippet] = []
    for module_name, label in _CONTRACTS:
        try:
            module = __import__(module_name, fromlist=["__file__"])
            text = Path(module.__file__).read_text(encoding="utf-8")  # pyright: ignore[reportArgumentType]
        except (OSError, ImportError, AttributeError):
            continue
        out.append(Snippet(source=label, text=clip(text)))
    return out


def retrieve_context(brief: str, *, k: int = 4) -> list[Snippet]:
    """Top grounding snippets for ``brief``: plugin contracts + accepted ledger plugins."""
    return assemble_context(
        brief,
        k=k,
        repo_snippets=_repo_snippets,
        recall_examples=_ledger.recall_examples,
        source_field="plugin_source",
    )
