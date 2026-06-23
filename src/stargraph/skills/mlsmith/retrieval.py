# SPDX-License-Identifier: Apache-2.0
"""RAG retrieval for the ML smith — corpus = the MLNode + runtime-loader contracts.

The domain-agnostic ranking/formatting lives in ``_smith.retrieval``; this module
supplies the *model node* corpus: the MLNode contract (``nodes/ml.py``, how a model
is wired into a graph — runtime, the sklearn pickle gate, ``expected_sha256``, the
input/output fields) and the runtime loaders (``ml/loaders.py``, what a serialized
model must look like to load: joblib for sklearn, an ONNX session for onnx) — both
always included since each is load-bearing for a correct trainer — interleaved with
gate-accepted ``(brief → trainer)`` ledger pairs. Best-effort: an unreadable contract
file just yields fewer snippets.
"""

from __future__ import annotations

from pathlib import Path

from stargraph.skills._smith.retrieval import Snippet, assemble_context, clip, format_context
from stargraph.skills.mlsmith import _ledger

__all__ = ["Snippet", "format_context", "retrieve_context"]

# (import module, label) for each fixed contract the generator must honor.
_CONTRACTS = (
    ("stargraph.nodes.ml", "repo:nodes/ml.py (the MLNode contract the trainer feeds)"),
    ("stargraph.ml.loaders", "repo:ml/loaders.py (how each runtime loads a serialized model)"),
)


def _repo_snippets(brief: str, k: int) -> list[Snippet]:
    """The model-node contracts the generator must honor — always included, in order."""
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
    """Top grounding snippets for ``brief``: model-node contracts + accepted trainers."""
    return assemble_context(
        brief,
        k=k,
        repo_snippets=_repo_snippets,
        recall_examples=_ledger.recall_examples,
        source_field="trainer_source",
    )
