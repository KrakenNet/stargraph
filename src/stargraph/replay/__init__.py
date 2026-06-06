# SPDX-License-Identifier: Apache-2.0
"""replay module -- cassette layers + determinism shims (FR-21, FR-28, FR-56, FR-86).

Re-exports the public counterfactual surface
(:class:`~stargraph.replay.counterfactual.CounterfactualMutation`,
:func:`~stargraph.replay.counterfactual.apply_respond_override`,
:func:`~stargraph.replay.counterfactual.derived_graph_hash`) so callers can
``from stargraph.replay import CounterfactualMutation`` per the documented
FR-56 entry-point.

Cf-replay HITL decision rule (FR-86, AC-14.7, design §4.5, §9.6):

* At a step that originally emitted
  :class:`~stargraph.runtime.events.WaitingForInputEvent`, replay calls
  :func:`apply_respond_override` to resolve cassette vs. override.
* Default (no override): the recorded respond fact is the durable
  cassette payload; replay does NOT wait for an ``awaiting-input``
  round-trip (that would deadlock -- the original analyst is not in
  the loop). The replay state transitions through ``"awaiting-input"``
  only as a recorded artifact.
* Override: the helper returns ``(payload, "cf:<actor>")``; replay
  asserts a fresh ``stargraph.evidence`` fact with ``origin="user"`` +
  ``source=<source>`` carrying the override payload (locked Decision
  #2: raw JSON dict in the ``data`` slot, no envelope).

Capability ownership: the cf override path bypasses ``runs:respond``
(the original live respond fact already carried that capability check
at live time, gated at the HTTP API in Phase 1 task 1.23). Counterfactual
mutation requires ``runs:counterfactual`` (Phase-2 ratelimit gate, locked
Decision #6 -- shared per-actor in-memory token bucket).

Replay determinism (NFR-4): same cassette + same mutation -> same
cf-derived ``graph_hash``. The override payload participates in the
cf-hash pre-image, so two cf-mutations differing only on
``respond_payloads[step_n]`` produce distinct cf-hashes.
"""

from __future__ import annotations

from stargraph.replay.cassettes import ToolCallCassette, args_hash
from stargraph.replay.counterfactual import (
    CounterfactualMutation,
    apply_respond_override,
    derived_graph_hash,
)
from stargraph.replay.react_cassette import (
    ReactStepRecord,
    ReactStepReplayCassette,
    input_hash,
)

__all__ = [
    "CounterfactualMutation",
    "ReactStepRecord",
    "ReactStepReplayCassette",
    "ToolCallCassette",
    "apply_respond_override",
    "args_hash",
    "derived_graph_hash",
    "input_hash",
]
