# SPDX-License-Identifier: Apache-2.0
"""stargraph.nodes.interrupt -- bypass-Fathom HITL pause node (FR-82, design §9.2).

Public surface: :class:`InterruptNode` + :class:`InterruptNodeConfig`.
Dispatch raises :class:`stargraph.graph.loop._HitInterrupt` carrying an
:class:`stargraph.ir._models.InterruptAction`; the loop arm at task 1.11
transitions ``state="awaiting-input"`` and emits
:class:`~stargraph.runtime.events.WaitingForInputEvent`.
"""

from __future__ import annotations

from stargraph.nodes.interrupt.interrupt_node import InterruptNode, InterruptNodeConfig

__all__ = [
    "InterruptNode",
    "InterruptNodeConfig",
]
