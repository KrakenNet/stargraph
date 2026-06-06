# SPDX-License-Identifier: Apache-2.0
"""Cassette layer for replay-safety (FR-21, FR-28).

Two cassette flavors:

* :class:`ToolCallCassette` keys ``(tool_id, args_hash) -> result`` for
  read-side-effect tool calls (consumed by
  :mod:`stargraph.runtime.tool_exec`; per requirements §amendment-6).
* :class:`NodeCassette` (Protocol) + :class:`InMemoryNodeCassette` key
  ``(node_id, step) -> payload`` for write-side-effect nodes (design
  §10.3). On first run a node records its outputs; on replay it reads
  them back instead of re-issuing the side effect.

Both flavors are checkpoint-resident — the engine snapshots them under
``state_snapshot["__cassette_tools"]`` / ``__cassette_nodes``.

Tool args hash uses canonical JSON (sorted keys, no whitespace) +
SHA-256, consistent with Stargraph's jsonschema-validated tool input.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Protocol, runtime_checkable

__all__ = [
    "InMemoryNodeCassette",
    "NodeCassette",
    "ToolCallCassette",
    "args_hash",
]


def args_hash(args: dict[str, Any]) -> str:
    """Return a stable SHA-256 hash of ``args`` (canonical JSON, sorted keys)."""
    canonical = json.dumps(args, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class ToolCallCassette:
    """In-memory ``(tool_name, args_hash) -> result`` cassette store.

    Implements the :class:`stargraph.runtime.tool_exec.CassetteStore` Protocol
    (structural -- no inheritance needed). Serializes to/from a plain dict
    for checkpoint persistence under ``state_snapshot["__cassette_tools"]``.
    """

    __slots__ = ("_entries",)

    def __init__(self) -> None:
        self._entries: dict[tuple[str, str], dict[str, Any]] = {}

    def record(
        self,
        tool_id: str,
        args: dict[str, Any],
        result: dict[str, Any],
    ) -> None:
        """Persist ``result`` for the ``(tool_id, args)`` pair."""
        self._entries[(tool_id, args_hash(args))] = dict(result)

    def get(
        self,
        tool_id: str,
        args: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Return the recorded result or ``None`` on cache miss."""
        recorded = self._entries.get((tool_id, args_hash(args)))
        return None if recorded is None else dict(recorded)

    def to_state(self) -> list[dict[str, Any]]:
        """Serialize the cassette for checkpoint persistence.

        Emits a list of ``{tool_id, args_hash, result}`` records -- a
        list (not a dict) because JSON object keys cannot be tuples.
        """
        return [
            {"tool_id": tid, "args_hash": ahash, "result": dict(result)}
            for (tid, ahash), result in self._entries.items()
        ]

    @classmethod
    def from_state(cls, state: list[dict[str, Any]]) -> ToolCallCassette:
        """Restore a cassette from :meth:`to_state` output."""
        cassette = cls()
        for entry in state:
            tid = entry["tool_id"]
            ahash = entry["args_hash"]
            result = entry["result"]
            cassette._entries[(tid, ahash)] = dict(result)
        return cassette


@runtime_checkable
class NodeCassette(Protocol):
    """Per-node cassette for write-side-effect nodes (design §10.3).

    Keyed on ``(node_id, step)`` and carrying an opaque JSON-shaped
    payload so any write-class node (artifact, notify, email, broker
    write) can plug in without a per-node cassette type. On first run
    the node calls :meth:`record`; on replay it calls :meth:`get` and
    returns the recorded payload verbatim instead of re-issuing the
    underlying side effect.
    """

    def record(self, node_id: str, step: int, payload: dict[str, Any]) -> None:
        """Persist ``payload`` for the ``(node_id, step)`` execution."""
        ...

    def get(self, node_id: str, step: int) -> dict[str, Any] | None:
        """Return the recorded payload or ``None`` on cache miss."""
        ...


class InMemoryNodeCassette:
    """In-memory ``(node_id, step) -> payload`` cassette store.

    Implements :class:`NodeCassette` structurally; serializes to/from a
    plain list for checkpoint persistence under
    ``state_snapshot["__cassette_nodes"]``.
    """

    __slots__ = ("_entries",)

    def __init__(self) -> None:
        self._entries: dict[tuple[str, int], dict[str, Any]] = {}

    def record(self, node_id: str, step: int, payload: dict[str, Any]) -> None:
        """Persist ``payload`` for the ``(node_id, step)`` execution."""
        self._entries[(node_id, step)] = dict(payload)

    def get(self, node_id: str, step: int) -> dict[str, Any] | None:
        """Return the recorded payload or ``None`` on cache miss."""
        recorded = self._entries.get((node_id, step))
        return None if recorded is None else dict(recorded)

    def to_state(self) -> list[dict[str, Any]]:
        """Serialize the cassette for checkpoint persistence.

        Emits a list of ``{node_id, step, payload}`` records — a list
        (not a dict) because JSON object keys cannot be tuples.
        """
        return [
            {"node_id": nid, "step": step, "payload": dict(payload)}
            for (nid, step), payload in self._entries.items()
        ]

    @classmethod
    def from_state(cls, state: list[dict[str, Any]]) -> InMemoryNodeCassette:
        """Restore a cassette from :meth:`to_state` output."""
        cassette = cls()
        for entry in state:
            nid = entry["node_id"]
            step = int(entry["step"])
            payload = entry["payload"]
            cassette._entries[(nid, step)] = dict(payload)
        return cassette
