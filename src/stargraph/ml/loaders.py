# SPDX-License-Identifier: Apache-2.0
"""stargraph.ml.loaders -- per-runtime model loaders for :class:`MLNode` (FR-30).

Implements design §3.9.2 verbatim amendment 8 dispatch:

* ``sklearn`` -- ``joblib.load`` gated behind ``allow_unsafe_pickle``
  default-deny, plus ``__sklearn_version__`` sidecar mismatch detection
  (``InconsistentVersionWarning`` re-raised as
  :class:`stargraph.errors.IncompatibleSklearnVersion`).
* ``xgboost`` -- ``Booster.load_model`` accepts only ``.ubj`` / ``.json``;
  the legacy binary ``.bin`` format is rejected (removed in xgboost 3.1).
* ``onnx`` -- ``InferenceSession`` opened with explicit
  ``providers=["CPUExecutionProvider"]`` (defeats #25145 silent GPU
  fallback), cached one-per-``(model_id, version)`` per design §3.9.2.

The session cache lives at module scope keyed by
``(model_id, version)`` -- ONNX Runtime's CPU EP is documented
thread-safe (#114), so a single shared session across concurrent
inferences is the right tradeoff for FR-30 (one session per
``(model_id, version)``, NOT per call).
"""

from __future__ import annotations

import hashlib
import logging
import threading
import warnings
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from stargraph.errors import (
    IncompatibleModelHashError,
    IncompatibleSklearnVersion,
    MLNodeError,
)

if TYPE_CHECKING:
    import onnxruntime  # pyright: ignore[reportMissingTypeStubs]
    import xgboost  # pyright: ignore[reportMissingTypeStubs]


_LOG = logging.getLogger("stargraph.ml.loaders")

# Module-scope ONNX session cache keyed by (model_id, version) per
# design §3.9.2. Guarded by a Lock so two threads racing the cold-cache
# path emit a single InferenceSession (the dictionary write is atomic
# in CPython, but the cold load is expensive and we don't want to do
# it twice).
_ONNX_SESSION_CACHE: dict[tuple[str, str], Any] = {}
_ONNX_CACHE_LOCK = threading.Lock()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _file_uri_to_path(file_uri: str) -> Path:
    """Resolve a v1 ``file://`` URI to a local :class:`Path`.

    v1 supports ``file://`` only; ``s3://`` / ``gs://`` are deferred to
    a later release per FR-30.
    """
    parsed = urlparse(file_uri)
    if parsed.scheme != "file":
        raise MLNodeError(
            f"unsupported model URI scheme {parsed.scheme!r}; v1 supports file:// only",
            file_uri=file_uri,
            scheme=parsed.scheme,
        )
    return Path(parsed.path)


def _sha256_of(path: Path) -> str:
    """Stream-hash ``path`` with SHA-256 and return the hex digest."""
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# sklearn
# ---------------------------------------------------------------------------


def load_sklearn_model(
    *,
    model_id: str,
    version: str,
    file_uri: str,
    allow_unsafe_pickle: bool = False,
    expected_sha256: str | None = None,
) -> Any:
    """Load a pickled sklearn estimator with the FR-30 safety contract.

    Order of operations (each step gates the next):

    1. **Default-deny pickle gate** -- if ``allow_unsafe_pickle`` is
       ``False``, raise :class:`stargraph.errors.MLNodeError` with the
       exact ``"pickle disabled; set allow_unsafe_pickle=True"`` message
       *before* opening the file (no ``joblib.load`` call ever happens).
    2. **SHA-256 verification** -- if a pinned digest is supplied,
       re-hash the file bytes and raise
       :class:`stargraph.errors.IncompatibleModelHashError` on mismatch.
    3. **Sklearn version sidecar check** -- the file
       ``<model>.pkl.sklearn_version`` records the sklearn version the
       pickle was written under; if it differs from the current
       interpreter's ``sklearn.__version__`` we raise
       :class:`stargraph.errors.IncompatibleSklearnVersion` *before*
       unpickling so a skewed estimator never reaches user code.
    4. **joblib.load with InconsistentVersionWarning -> error** --
       finally call ``joblib.load`` with sklearn's
       ``InconsistentVersionWarning`` filtered to ``error`` so any
       residual skew slipped past the sidecar still fails loud.

    :raises MLNodeError: if ``allow_unsafe_pickle`` is ``False``.
    :raises IncompatibleModelHashError: on SHA-256 mismatch.
    :raises IncompatibleSklearnVersion: on sidecar or runtime version
        skew.
    """
    if not allow_unsafe_pickle:
        raise MLNodeError(
            "pickle disabled; set allow_unsafe_pickle=True to opt in",
            model_id=model_id,
            version=version,
            file_uri=file_uri,
            runtime="sklearn",
        )

    path = _file_uri_to_path(file_uri)

    if expected_sha256 is not None:
        actual = _sha256_of(path)
        if actual != expected_sha256:
            raise IncompatibleModelHashError(
                "model file content hash does not match registry",
                model_id=model_id,
                expected_sha256=expected_sha256,
                actual_sha256=actual,
                model_path=str(path),
            )

    # Sidecar check -- compare recorded sklearn version against current.
    sidecar = path.with_suffix(path.suffix + ".sklearn_version")
    current_sklearn_version: str | None = None
    try:
        import sklearn  # pyright: ignore[reportMissingTypeStubs]

        current_sklearn_version = str(sklearn.__version__)  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]
    except ImportError:  # pragma: no cover -- sklearn is in [ml] extras
        current_sklearn_version = None

    if sidecar.is_file() and current_sklearn_version is not None:
        recorded = sidecar.read_text(encoding="utf-8").strip()
        if recorded != current_sklearn_version:
            raise IncompatibleSklearnVersion(
                "sklearn version recorded in sidecar does not match runtime",
                model_id=model_id,
                expected_version=recorded,
                actual_version=current_sklearn_version,
                model_path=str(path),
            )

    # Re-raise sklearn's InconsistentVersionWarning as an error per
    # design §3.9.2. The warning filter is scoped to this call so we
    # don't leak the conversion into the caller's warning state.
    import joblib  # pyright: ignore[reportMissingTypeStubs]

    with warnings.catch_warnings():
        try:
            from sklearn.exceptions import (  # pyright: ignore[reportMissingTypeStubs]
                InconsistentVersionWarning,
            )

            warnings.filterwarnings("error", category=InconsistentVersionWarning)
        except ImportError:  # pragma: no cover -- sklearn is in [ml] extras
            InconsistentVersionWarning = None  # type: ignore[assignment]  # noqa: N806
        try:
            return joblib.load(str(path))  # pyright: ignore[reportUnknownMemberType]
        except Exception as exc:
            # Re-raise sklearn's version-skew warning (now an error) as
            # the typed Stargraph exception so callers' ``except
            # IncompatibleSklearnVersion`` works regardless of which
            # path tripped the gate.
            if InconsistentVersionWarning is not None and isinstance(
                exc, InconsistentVersionWarning
            ):
                raise IncompatibleSklearnVersion(
                    str(exc),
                    model_id=model_id,
                    model_path=str(path),
                ) from exc
            raise


# ---------------------------------------------------------------------------
# xgboost
# ---------------------------------------------------------------------------


_XGBOOST_ALLOWED_SUFFIXES: frozenset[str] = frozenset({".ubj", ".json"})


def load_xgboost_model(
    *,
    model_id: str,
    version: str,
    file_uri: str,
) -> xgboost.Booster:
    """Load an XGBoost model from ``.ubj`` (preferred) or ``.json``.

    The legacy binary ``.bin`` format was removed in xgboost 3.1 (FR-30
    verbatim amendment 8) -- attempting to load it raises
    :class:`stargraph.errors.MLNodeError` *before* touching the file so
    operators see a clear "format unsupported" message rather than a
    cryptic xgboost C-extension error.
    """
    path = _file_uri_to_path(file_uri)
    suffix = path.suffix.lower()
    if suffix not in _XGBOOST_ALLOWED_SUFFIXES:
        raise MLNodeError(
            f"xgboost model must be .ubj or .json (got {suffix!r}); "
            "the legacy .bin format was removed in xgboost 3.1",
            model_id=model_id,
            version=version,
            file_uri=file_uri,
            runtime="xgboost",
            suffix=suffix,
        )

    import xgboost as xgb  # pyright: ignore[reportMissingTypeStubs]

    booster = xgb.Booster()
    booster.load_model(str(path))  # pyright: ignore[reportUnknownMemberType]
    return booster


# ---------------------------------------------------------------------------
# onnx
# ---------------------------------------------------------------------------


def get_onnx_session(
    *,
    model_id: str,
    version: str,
    file_uri: str,
) -> onnxruntime.InferenceSession:
    """Return the cached :class:`onnxruntime.InferenceSession` for ``(model_id, version)``.

    Per design §3.9.2: one session per ``(model_id, version)``, opened
    with explicit ``providers=["CPUExecutionProvider"]`` (no silent GPU
    fallback per onnxruntime#25145). The CPU EP is thread-safe
    (onnxruntime#114) so the cached session can be shared across
    concurrent inferences.

    The effective provider is logged at INFO on session create
    (collection-side telemetry hook for FR-30 verification).
    """
    key = (model_id, version)
    cached = _ONNX_SESSION_CACHE.get(key)
    if cached is not None:
        return cached  # type: ignore[no-any-return]

    with _ONNX_CACHE_LOCK:
        cached = _ONNX_SESSION_CACHE.get(key)
        if cached is not None:
            return cached  # type: ignore[no-any-return]

        import onnxruntime as ort  # pyright: ignore[reportMissingTypeStubs]

        path = _file_uri_to_path(file_uri)
        sess = ort.InferenceSession(
            str(path),
            providers=["CPUExecutionProvider"],
        )
        effective = list(sess.get_providers())
        _LOG.info(
            "onnx session created for (%s, %s) with providers=%s",
            model_id,
            version,
            effective,
        )
        _ONNX_SESSION_CACHE[key] = sess
        return sess


def _clear_onnx_session_cache() -> None:  # pyright: ignore[reportUnusedFunction]
    """Drop every cached ONNX session.

    Test-only escape hatch -- production code should never need to evict
    a session because ``(model_id, version)`` is immutable in the
    registry. Tests use this to force cache misses between cases.
    """
    with _ONNX_CACHE_LOCK:
        _ONNX_SESSION_CACHE.clear()
