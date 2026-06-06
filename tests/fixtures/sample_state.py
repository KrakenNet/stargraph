# SPDX-License-Identifier: Apache-2.0
"""Phase 1 VE2: 2-node graph, sqlite checkpoint, expected events: TransitionEvent + ResultEvent.

Pydantic state-schema fixture paired with ``tests/fixtures/sample-graph.yaml``.
The sample graph's IR ``state_schema`` declares a single ``message: str`` field;
this module exposes the matching :class:`SampleState` BaseModel that the engine
will compile/wire when loading the YAML in task 1.29 (Phase 1 POC milestone:
``stargraph run tests/fixtures/sample-graph.yaml`` end-to-end with sqlite
checkpoint + JSONL log). Kept deliberately minimal -- no parallel, no DSPy,
no MCP -- per the Phase 1 VE2 smoke scope.
"""

from __future__ import annotations

from pydantic import BaseModel


class SampleState(BaseModel):
    """Minimal state for the Phase 1 sample graph (one ``message: str`` field)."""

    message: str = ""
