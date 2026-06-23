# SPDX-License-Identifier: Apache-2.0
"""StoreProgram — the DSPy generator for stores, bound to the shared SmithProgram.

The generate/forward/demo plumbing lives in :class:`SmithProgram`; this module
supplies the *store* signature (the fields a store generation emits) and
``coerce`` (Prediction → plain dict). LM construction + ``clarify`` are
re-exported from the shared core so callers import them from here.
"""

from __future__ import annotations

from typing import Any

import dspy  # pyright: ignore[reportMissingTypeStubs]

from stargraph.skills._smith.lm import (
    DEFAULT_OLLAMA_URL,
    clarify,
    configure_lm,
    make_lm,
)
from stargraph.skills._smith.program import INPUT_FIELDS, SmithProgram, as_dict
from stargraph.skills.storesmith import _ledger

# Re-exported from the shared core so callers keep importing them from here.
__all__ = [
    "DEFAULT_OLLAMA_URL",
    "INPUT_FIELDS",
    "StoreProgram",
    "StoreSignature",
    "clarify",
    "coerce",
    "configure_lm",
    "make_lm",
]


class StoreSignature(dspy.Signature):  # pyright: ignore[reportUnknownMemberType]
    """Write one Stargraph DocStore and a pytest test for it, from a brief.

    A Stargraph store is a Python class implementing the ``DocStore`` protocol
    (``stargraph.stores.doc.DocStore``): an ``__init__(self, path: Path)`` plus the
    async methods ``bootstrap``, ``health``, ``migrate``, ``put``, ``get``, and
    ``query``. Persistence is pure sqlite (use ``aiosqlite``) so the store is fully
    offline-gateable. ``health()`` MUST return a
    ``stargraph.stores._common.StoreHealth`` with ``ok=True`` and an int
    ``version``; ``get()`` MUST return ``None`` for an absent id and a
    ``stargraph.stores.doc.Document`` for a present one; ``put()`` MUST use
    INSERT OR REPLACE semantics so a second put on the same id overwrites; and
    ``migrate()`` MUST raise ``stargraph.errors.MigrationNotSupported`` for any
    operation other than ``add_column`` (delegate to
    ``stargraph.stores._common._validate_migration_plan``). Honor every lesson in
    ``lessons`` and fix every issue in ``last_findings``.

    OUTPUT FILE CONTRACT (the two files are written FLAT into one directory and
    gated together — follow this exactly or the gate rejects a correct store):

    - ``store_source`` is saved as ``store.py`` and must define exactly ONE class
      implementing ``DocStore`` (plus whatever it imports). Put every import the
      class needs at module top level.
    - ``test_source`` is saved as ``test_store.py`` BESIDE it. It MUST import the
      store with ``from store import <class_name>`` — NOT from its package path
      (there is no package; the file is literally ``store.py``). Drive the async
      methods with ``asyncio.run``. Do NOT ``import pytest`` or import anything you
      do not use — an unused import fails the static gate. Write plain
      ``def test_*()`` functions with ``assert``.
    - ``fixture`` supplies the values the contract tier exercises the store with:
      ``doc_id`` (str), ``content`` (str), ``content2`` (a DIFFERENT str), and
      ``metadata`` (a mixed-type dict, e.g. {"k": "v", "n": 1}).
    """

    brief: str = dspy.InputField(desc="what the store should do")  # pyright: ignore[reportUnknownMemberType]
    lessons: list[str] = dspy.InputField(desc="past failures to avoid")  # pyright: ignore[reportUnknownMemberType]
    last_findings: list[dict[str, Any]] = dspy.InputField(desc="prior attempt findings")  # pyright: ignore[reportUnknownMemberType]
    relevant_context: str = dspy.InputField(  # pyright: ignore[reportUnknownMemberType]
        desc="grounding: store protocol + similar existing stores + accepted examples + web"
    )

    class_name: str = dspy.OutputField(desc="the store class name (PascalCase)")  # pyright: ignore[reportUnknownMemberType]
    fixture: dict[str, Any] = dspy.OutputField(  # pyright: ignore[reportUnknownMemberType]
        desc="doc_id, content, content2 (distinct), metadata (mixed-type dict)"
    )
    store_source: str = dspy.OutputField(  # pyright: ignore[reportUnknownMemberType]
        desc="store.py: one DocStore class, all imports at top level"
    )
    test_source: str = dspy.OutputField(  # pyright: ignore[reportUnknownMemberType]
        desc="test_store.py: import via `from store import <class_name>`; only used imports"
    )


def coerce(pred: Any) -> dict[str, Any]:
    """Normalize a ``dspy.Prediction`` (or any attr-bag) into a plain dict."""
    return {
        "class_name": str(getattr(pred, "class_name", "")),
        "fixture": as_dict(getattr(pred, "fixture", {})),
        "store_source": str(getattr(pred, "store_source", "")),
        "test_source": str(getattr(pred, "test_source", "")),
    }


class StoreProgram(SmithProgram):
    def __init__(self, *, load_compiled: bool = True) -> None:
        super().__init__(
            signature=StoreSignature,
            coerce=coerce,
            load_compiled_demos=_ledger.load_compiled_demos,
            load_compiled=load_compiled,
        )
