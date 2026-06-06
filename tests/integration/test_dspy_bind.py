# SPDX-License-Identifier: Apache-2.0
"""FR-5/FR-25 basic test for ``stargraph.adapters.dspy.bind`` (Task 3.55).

Asserts the seam contract documented in design §3.3.1:

1. :func:`stargraph.adapters.dspy.bind` returns a :class:`DSPyNode` instance.
2. The returned node, when invoked, calls the wrapped DSPy module with the
   *mapped* signature input names (not the stargraph-side state-field names) --
   i.e. ``signature_map`` is honoured at the input projection step.

The wrapped "DSPy module" here is an inert callable fixture so the test
never touches a real LM; the seam contract under test is purely the bind
+ projection plumbing, not DSPy's structured-output behaviour (which is
covered by ``test_dspy_loud_fallback.py``).
"""

from __future__ import annotations

from typing import Any

import pytest

# Skip cleanly if dspy isn't installed (matches loud-fallback test pattern).
pytest.importorskip("dspy", reason="dspy required for FR-5/FR-25 bind seam tests")

from stargraph.adapters.dspy import bind
from stargraph.nodes.dspy import DSPyNode


class _RecordingModule:
    """Inert stand-in for a ``dspy.Module`` -- records the kwargs it was called with.

    Mirrors inputs back as a dict so :meth:`DSPyNode._project_outputs` has
    something dict-shaped to return without invoking any LM.
    """

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def __call__(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(dict(kwargs))
        return dict(kwargs)


def test_bind_returns_dspy_node() -> None:
    """``bind()`` returns a :class:`DSPyNode` (FR-5 seam contract)."""
    node = bind(module=_RecordingModule(), signature_map={"question": "question"})
    assert isinstance(node, DSPyNode)


def test_bind_invokes_module_with_mapped_field_names() -> None:
    """Module receives kwargs renamed via ``signature_map`` (FR-25 projection)."""
    module = _RecordingModule()
    # Stargraph state-field "user_query" -> DSPy signature input "question".
    node = bind(module=module, signature_map={"user_query": "question"})

    node.acall({"user_query": "what is stargraph?"})

    assert module.calls == [{"question": "what is stargraph?"}], (
        "bind() must rename stargraph state-field keys to DSPy signature input names"
    )
