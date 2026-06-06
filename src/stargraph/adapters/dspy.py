# SPDX-License-Identifier: Apache-2.0
"""stargraph.adapters.dspy -- DSPy seam, force-loud per design §3.3.1 (FR-5/FR-6/FR-25).

The seam is intentionally thin: a logging filter installed on the DSPy
``json_adapter`` logger converts the canonical fallback warning into
:class:`stargraph.errors.AdapterFallbackError` so silent ChatAdapter→JSONAdapter
degradation is impossible at the seam. ``bind()`` returns a
:class:`stargraph.nodes.dspy.DSPyNode` wrapping the user's DSPy module with
the force-loud adapter wired in.

Verbatim recipe from design §3.3.1::

    JSONAdapter(use_native_function_calling=True)         # default
    ChatAdapter(use_json_adapter_fallback=False)          # chat-style sigs
    logging.getLogger("dspy.adapters.json_adapter").addFilter(_LoudFallbackFilter())

The needle string (``"Failed to use structured output format, falling back
to JSON mode"``) is the verbatim DSPy ≥3.0.4 fallback warning; mirrored in
``tests/integration/test_dspy_loud_fallback.py``.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import dspy  # type: ignore[import-untyped]

from stargraph.errors import AdapterFallbackError

if TYPE_CHECKING:
    from stargraph.nodes.dspy import DSPyNode

# A ``SignatureMap`` is the user-supplied mapping from stargraph state-field
# names to DSPy signature input/output names. Phase-2 keeps it structurally
# typed (a plain mapping) to avoid premature coupling; the concrete schema
# lands when DSPyNode grows beyond the seam.
SignatureMap = dict[str, str]


_LOGGER_NAME = "dspy.adapters.json_adapter"

# Verbatim DSPy ≥3.0.4 fallback warning text -- the canonical needle
# matched by :class:`_LoudFallbackFilter` to detect silent JSONAdapter
# degradation. Mirrored verbatim in
# ``tests/integration/test_dspy_loud_fallback.py``.
FALLBACK_NEEDLE: str = "Failed to use structured output format, falling back to JSON mode"


class _LoudFallbackFilter(logging.Filter):
    """Convert DSPy's silent JSONAdapter-fallback warning into a loud raise.

    Installed on ``logging.getLogger("dspy.adapters.json_adapter")`` by
    :func:`bind`. When DSPy emits the canonical fallback warning, the
    filter raises :class:`AdapterFallbackError` *from inside* ``filter()``
    -- short-circuiting log emission -- so the warning text never leaks
    through to handlers and the caller is forced to deal with the
    silent-degradation event explicitly (FR-6).
    """

    _NEEDLE: str = FALLBACK_NEEDLE

    def filter(self, record: logging.LogRecord) -> bool:
        if FALLBACK_NEEDLE in record.getMessage():
            raise AdapterFallbackError(
                record.getMessage(),
                adapter="dspy",
                original_adapter="ChatAdapter",
                fallback_adapter="JSONAdapter",
            )
        return True


def _install_filter() -> None:
    """Idempotently install :class:`_LoudFallbackFilter` on the DSPy logger.

    Re-installing across calls would stack duplicate filters, each of
    which would raise on the same record -- harmless but noisy. We
    fingerprint by exact filter type to keep ``bind()`` cheap to call.
    """
    target = logging.getLogger(_LOGGER_NAME)
    if not any(isinstance(f, _LoudFallbackFilter) for f in target.filters):
        target.addFilter(_LoudFallbackFilter())


def bind(module: Any, *, signature_map: Any) -> DSPyNode:
    """Bind a DSPy module as a stargraph :class:`DSPyNode` with force-loud config.

    Per design §3.3.1, the force-loud config is:

    1. ``JSONAdapter(use_native_function_calling=True)`` as default adapter.
    2. ``ChatAdapter(use_json_adapter_fallback=False)`` for chat-style sigs
       -- silences DSPy's silent fallback path at construction time.
    3. ``_LoudFallbackFilter`` installed on ``dspy.adapters.json_adapter``
       so any residual fallback warning raises rather than emits.

    :param module: A ``dspy.Module`` (or compatible callable). Phase-2 keeps
        the parameter ``Any`` so the seam accepts both real DSPy modules and
        the inert fixtures used by the FR-6 integration test.
    :param signature_map: User mapping from stargraph state-field names to
        DSPy signature input/output names. Type kept open at the seam; the
        concrete :class:`DSPyNode` validates shape on use.
    :returns: A :class:`DSPyNode` wrapping ``module`` with the force-loud
        adapter wired in.
    """
    _install_filter()
    json_adapter = dspy.JSONAdapter(use_native_function_calling=True)
    chat_adapter = dspy.ChatAdapter(use_json_adapter_fallback=False)

    # Local import avoids the stargraph.adapters.dspy <-> stargraph.nodes.dspy
    # import cycle: DSPyNode imports SignatureMap from this module, and
    # bind() returns a DSPyNode.
    from stargraph.nodes.dspy import DSPyNode

    return DSPyNode(
        module=module,
        adapter=json_adapter,
        chat_adapter=chat_adapter,
        signature_map=signature_map,
    )


__all__ = [
    "FALLBACK_NEEDLE",
    "SignatureMap",
    "_LoudFallbackFilter",
    "bind",
]
