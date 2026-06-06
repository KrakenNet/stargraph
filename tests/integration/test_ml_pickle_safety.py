# SPDX-License-Identifier: Apache-2.0
"""TDD-RED: sklearn pickle gating + version-skew detection (FR-30,
design §3.9.2; antipattern guard #4: pickle-as-arbitrary-code).

Pins the ``allow_unsafe_pickle`` default-deny gate and the
``__sklearn_version__`` sidecar mismatch contract *before* the GREEN
partner ships in task 3.37. Currently RED because :mod:`stargraph.nodes.ml`
and :mod:`stargraph.ml.loaders` do not yet exist.

Two cases:

5. Constructing :class:`MLNode` with a sklearn pickle file URI but
   ``allow_unsafe_pickle=False`` (default) raises ``MLNodeError`` with
   message matching ``r'pickle disabled.*set allow_unsafe_pickle=True'``.
6. Loading a pickle whose ``__sklearn_version__`` sidecar is mismatched
   raises :class:`stargraph.errors.IncompatibleSklearnVersion`.
"""

from __future__ import annotations

import importlib
import pickle
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

# pyright: reportMissingImports=false, reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportAttributeAccessIssue=false


def _write_sklearn_pickle(
    pickle_path: Path,
    *,
    sidecar_version: str,
) -> None:
    """Write a minimal sklearn-shaped pickle + sidecar at ``pickle_path``.

    Uses a real sklearn estimator if available (preferred, exercises
    actual joblib path); otherwise falls back to a dict carrying a
    ``__sklearn_version__`` attribute (still satisfies the gate-test
    contract because the gate fires *before* unpickling).
    """
    try:
        sklearn = importlib.import_module("sklearn")
        linear = importlib.import_module("sklearn.linear_model")
        est = linear.LogisticRegression()  # type: ignore[attr-defined]
        # tag the estimator with the sklearn version for sidecar parity
        actual_version = sklearn.__version__  # type: ignore[attr-defined]
        with pickle_path.open("wb") as fh:
            pickle.dump(est, fh)
    except ImportError:  # pragma: no cover -- sklearn is in [ml] extras
        # Fallback: opaque object (gate-test path doesn't unpickle)
        actual_version = "0.0.0"
        with pickle_path.open("wb") as fh:
            pickle.dump({"_kind": "stub-estimator"}, fh)

    sidecar = pickle_path.with_suffix(pickle_path.suffix + ".sklearn_version")
    sidecar.write_text(sidecar_version, encoding="utf-8")
    # also stash the actual version for tests that need it
    (pickle_path.with_suffix(pickle_path.suffix + ".actual_version")).write_text(
        actual_version, encoding="utf-8"
    )


def _ml_node_error_cls() -> type[Exception]:
    """Return the configured MLNodeError class.

    Per task 3.36 spec, ``MLNodeError`` may not yet exist as a top-level
    error symbol. Probe ``stargraph.errors`` first; if absent, fall back to
    ``StargraphRuntimeError`` (the documented base for engine-side runtime
    failures). The GREEN partner (3.37) is free to add ``MLNodeError``
    as a more specific subclass; the regex-match assertion still pins
    the message contract either way.
    """
    errors = importlib.import_module("stargraph.errors")
    cls = getattr(errors, "MLNodeError", None)
    if cls is None:
        cls = errors.StargraphRuntimeError  # type: ignore[attr-defined]
    return cls  # type: ignore[no-any-return]


def test_sklearn_pickle_blocked_by_default(tmp_path: Path) -> None:
    """Default-deny pickle gate: constructing MLNode with a sklearn
    pickle URI but ``allow_unsafe_pickle=False`` (the default) must
    raise ``MLNodeError`` with the pickle-disabled message."""
    pickle_path = tmp_path / "model.pkl"
    _write_sklearn_pickle(pickle_path, sidecar_version="1.8.0")

    ml = importlib.import_module("stargraph.nodes.ml")
    err_cls = _ml_node_error_cls()

    with pytest.raises(err_cls, match=r"pickle disabled.*set allow_unsafe_pickle=True"):
        node = ml.MLNode(  # type: ignore[attr-defined]
            model_id="logreg",
            version="v1",
            runtime="sklearn",
            file_uri=pickle_path.as_uri(),
            # allow_unsafe_pickle defaults to False -- gate fires
        )
        # If construction lazily defers, force load to trigger the gate.
        loaders = importlib.import_module("stargraph.ml.loaders")
        loaders.load_sklearn_model(  # type: ignore[attr-defined]
            model_id="logreg",
            version="v1",
            file_uri=pickle_path.as_uri(),
            allow_unsafe_pickle=False,
        )
        _ = node  # silence unused


def test_sklearn_version_skew_detection(tmp_path: Path) -> None:
    """Sidecar ``__sklearn_version__`` mismatch raises
    :class:`IncompatibleSklearnVersion` (FR-30 verbatim amendment 8)."""
    pickle_path = tmp_path / "model.pkl"
    # Deliberately wrong sidecar version (way in the past).
    _write_sklearn_pickle(pickle_path, sidecar_version="0.18.0")

    loaders = importlib.import_module("stargraph.ml.loaders")
    errors = importlib.import_module("stargraph.errors")

    with pytest.raises(errors.IncompatibleSklearnVersion):  # type: ignore[attr-defined]
        loaders.load_sklearn_model(  # type: ignore[attr-defined]
            model_id="logreg",
            version="v1",
            file_uri=pickle_path.as_uri(),
            allow_unsafe_pickle=True,  # opted in -- skew check still fires
        )
