# SPDX-License-Identifier: Apache-2.0
"""Per-step ReAct replay cassette (FR-35, AC-10.1, AC-10.2, NFR-4).

Mirrors the per-step record schema design §3.9 / FR-35 pins:

    (step_id, node_name, input_hash, output, model_id, prompt_hash,
     tool_name, tool_args_hash, wall_clock_ts)

Distinct from :class:`stargraph.replay.cassettes.ToolCallCassette` --
the latter matches by ``(tool_name, args_hash)`` (engine-level FR-21
tool replay). This module matches by ``(node_name, step_id)`` because a
ReAct loop fires the same tool repeatedly with different arguments and
the determinism contract is positional: step ``i`` of node ``X`` must
replay the recorded tuple at step ``i`` regardless of what arguments
the replayed LLM chooses.

Loud-fail mandatory (NFR-4): if the replay-side ``input_hash`` does not
match the recorded one for that ``(step_id, node_name)``, raise
:class:`stargraph.errors.ReplayError` -- never silently fall through.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from pydantic import BaseModel, ConfigDict

from stargraph.errors import ReplayError

__all__ = [
    "ReactStepRecord",
    "ReactStepReplayCassette",
    "input_hash",
]


def input_hash(payload: dict[str, Any]) -> str:
    """SHA-256 of canonical-JSON ``payload`` (sorted keys, no whitespace)."""
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class ReactStepRecord(BaseModel):
    """Per-step ReAct replay record (FR-35, AC-10.1).

    Every field on this model is part of the on-disk cassette schema --
    ``model_config(extra="forbid")`` rejects unknown keys so a stale
    cassette format trips at load time, not silently mid-replay.
    """

    model_config = ConfigDict(extra="forbid")

    step_id: int
    node_name: str
    input_hash: str
    output: dict[str, Any]
    model_id: str
    prompt_hash: str
    tool_name: str | None
    tool_args_hash: str | None
    wall_clock_ts: float


class ReactStepReplayCassette:
    """Per-step ``(node_name, step_id) -> ReactStepRecord`` cassette.

    Tool stub matching is positional: the loop pulls the recorded tuple
    for ``(node_name, step_id)`` and the recorded ``output`` is what the
    replayed step returns. Mutating any field of the record (in
    particular ``input_hash``) causes :meth:`replay` to raise
    :class:`ReplayError` -- AC-10.2 loud-fail contract.
    """

    __slots__ = ("_entries",)

    def __init__(self) -> None:
        self._entries: dict[tuple[str, int], ReactStepRecord] = {}

    def record(self, rec: ReactStepRecord) -> None:
        """Persist ``rec`` keyed by ``(node_name, step_id)``."""
        self._entries[(rec.node_name, rec.step_id)] = rec

    def get(self, node_name: str, step_id: int) -> ReactStepRecord | None:
        """Return the recorded entry, or ``None`` on miss."""
        return self._entries.get((node_name, step_id))

    def replay(
        self,
        *,
        node_name: str,
        step_id: int,
        input_payload: dict[str, Any],
    ) -> ReactStepRecord:
        """Resolve the recorded record for ``(node_name, step_id)``.

        Verifies ``input_hash(input_payload)`` matches the recorded
        ``input_hash``; raises :class:`ReplayError` on mismatch or miss
        (NFR-4 loud-fail).
        """
        rec = self._entries.get((node_name, step_id))
        if rec is None:
            raise ReplayError(
                f"no recorded ReAct step for node={node_name!r} step={step_id}",
                node_name=node_name,
                step_id=step_id,
            )
        actual = input_hash(input_payload)
        if actual != rec.input_hash:
            raise ReplayError(
                f"ReAct input_hash mismatch at node={node_name!r} step={step_id}: "
                f"recorded={rec.input_hash} actual={actual}",
                node_name=node_name,
                step_id=step_id,
                expected_hash=rec.input_hash,
                actual_hash=actual,
            )
        return rec

    def to_state(self) -> list[dict[str, Any]]:
        """Serialize for checkpoint persistence (list of record dicts)."""
        return [rec.model_dump(mode="json") for rec in self._entries.values()]

    @classmethod
    def from_state(cls, state: list[dict[str, Any]]) -> ReactStepReplayCassette:
        """Restore from :meth:`to_state` output."""
        cassette = cls()
        for entry in state:
            rec = ReactStepRecord.model_validate(entry)
            cassette._entries[(rec.node_name, rec.step_id)] = rec
        return cassette
