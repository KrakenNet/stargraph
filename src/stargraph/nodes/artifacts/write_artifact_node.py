# SPDX-License-Identifier: Apache-2.0
"""stargraph.nodes.artifacts.write_artifact_node -- :class:`WriteArtifactNode` (FR-92, §10.3).

The node reads a state-resident byte payload (or ``str``, coerced to UTF-8
bytes), persists it through a configured
:class:`~stargraph.artifacts.ArtifactStore`, emits an
:class:`~stargraph.runtime.events.ArtifactWrittenEvent` on the run's event
bus, and patches the resulting :class:`~stargraph.artifacts.ArtifactRef`
into state under :attr:`WriteArtifactNodeConfig.output_field`.

Replay determinism (``side_effects = SideEffects.write``, design §10.3):
on replay (``ctx.is_replay=True``) the node returns the recorded
:class:`~stargraph.artifacts.ArtifactRef` from ``ctx.node_cassette``
without calling :meth:`ArtifactStore.put`. Misses raise
:class:`~stargraph.errors.ArtifactStoreError` regardless of
``replay_policy`` -- silently re-writing would double the side effect.

The Phase-1 :class:`~stargraph.nodes.base.ExecutionContext` Protocol only
pins ``run_id``; this module declares :class:`WriteArtifactContext` as
the structural surface :class:`WriteArtifactNode` actually requires
(``run_id``, ``step``, ``bus``, ``artifact_store``, ``is_replay``,
``fathom``). The real :class:`~stargraph.graph.run.GraphRun` satisfies this
surface as later phases land richer context wiring; tests pass
duck-typed contexts. This mirrors the
:class:`~stargraph.nodes.subgraph.SubGraphContext` convention introduced in
task 1.30.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal, Protocol, runtime_checkable

from pydantic import Field

from stargraph.errors import ArtifactStoreError
from stargraph.ir import IRBase
from stargraph.nodes.base import ExecutionContext, NodeBase
from stargraph.runtime.events import ArtifactWrittenEvent
from stargraph.tools.spec import SideEffects

if TYPE_CHECKING:
    from pydantic import BaseModel

    from stargraph.artifacts import ArtifactRef, ArtifactStore
    from stargraph.replay.cassettes import NodeCassette

__all__ = [
    "WriteArtifactContext",
    "WriteArtifactNode",
    "WriteArtifactNodeConfig",
]


def _cassette_and_node_id(ctx: object) -> tuple[NodeCassette | None, str]:
    """Read the optional ``node_cassette`` + ``node_id`` slots off ``ctx``.

    Lifted out of :class:`WriteArtifactNode` so the record / replay
    paths share one duck-typed access pattern. Both slots are optional
    on :class:`WriteArtifactContext`; legacy / test contexts may omit
    them, in which case the cassette path is a no-op.
    """
    cassette: NodeCassette | None = getattr(ctx, "node_cassette", None)
    node_id = getattr(ctx, "node_id", "") or ""
    return cassette, node_id


@runtime_checkable
class WriteArtifactContext(Protocol):
    """Structural surface :class:`WriteArtifactNode` reads from the run context.

    The Phase-1 :class:`~stargraph.nodes.base.ExecutionContext` Protocol
    only pins ``run_id``; the artifact-write node additionally requires:

    * ``step`` -- monotonic per-run step index stamped on every emitted
      event and on the persisted :class:`~stargraph.artifacts.ArtifactRef`
      provenance fields (matches :class:`stargraph.runtime.tool_exec.RunContext`).
    * ``bus`` -- the run's event bus (must expose
      ``async send(event, *, fathom=...)`` matching
      :class:`stargraph.runtime.bus.EventBus`).
    * ``artifact_store`` -- the resolved
      :class:`~stargraph.artifacts.ArtifactStore` provider for this run.
    * ``is_replay`` -- replay-routing flag, mirrors
      :attr:`stargraph.runtime.tool_exec.RunContext.is_replay`. Honored by
      ``replay_policy``.
    * ``node_cassette`` -- :class:`~stargraph.replay.cassettes.NodeCassette`
      for the run, or ``None`` when no cassette is attached.
      Recorded on live runs; consulted on replay so the node returns
      recorded state without re-issuing the write.
    * ``node_id`` -- the IR node id; used as the cassette key with
      ``step``. May be empty on legacy contexts.
    * ``fathom`` -- optional :class:`~stargraph.fathom.FathomAdapter` for
      ``stargraph.transition`` mirroring (parity with the bus-side
      contract in :mod:`stargraph.runtime.parallel`).
    """

    run_id: str
    step: int
    bus: Any
    artifact_store: ArtifactStore
    is_replay: bool
    fathom: Any


class WriteArtifactNodeConfig(IRBase):
    """Pydantic config for :class:`WriteArtifactNode` (design §10.3).

    Inherits ``extra="forbid"`` from :class:`IRBase` so unknown keys in
    YAML/JSON IR fail loudly at validation time (FR-6, AC-9.1).
    """

    content_field: str
    """State attribute holding the artifact payload (``bytes`` or ``str``)."""
    name: str
    """Logical filename hint persisted in :class:`ArtifactRef.name`."""
    content_type: str
    """MIME type persisted in sidecar metadata + :class:`ArtifactRef.content_type`."""
    metadata: dict[str, Any] = Field(default_factory=dict[str, Any])
    """Extra free-form metadata merged into the sidecar (under ``content_type``)."""
    output_field: str = "artifact_ref"
    """State key receiving the resulting :class:`ArtifactRef` (``model_dump`` form)."""
    replay_policy: Literal["must_stub", "fail_loud"] = "must_stub"
    """Replay routing per design §10.3 (``side_effects=write`` is replay-sensitive)."""


class WriteArtifactNode(NodeBase):
    """Built-in node that persists a state-resident payload as an artifact (FR-92).

    Replay-aware (``side_effects = SideEffects.write``, design §10.3):
    on replay the node consults ``ctx.node_cassette`` for the
    ``(node_id, step)`` entry recorded on the live run and returns the
    recorded :class:`ArtifactRef` payload without re-issuing
    :meth:`ArtifactStore.put`. A cassette miss raises
    :class:`ArtifactStoreError` regardless of ``replay_policy`` --
    silent re-writing would double the side effect.

    Configured via :class:`WriteArtifactNodeConfig`; the config is
    attached at construction time (``WriteArtifactNode(config=cfg)``).
    """

    side_effects = SideEffects.write
    """Write-class side effect; replay must stub or fail loud (design §10.3)."""
    config_model = WriteArtifactNodeConfig
    """Pydantic config schema, surfaced for IR validators / registry tooling."""

    def __init__(self, *, config: WriteArtifactNodeConfig) -> None:
        self._config = config

    @property
    def config(self) -> WriteArtifactNodeConfig:
        """Public read-only handle on the validated config (used by tests)."""
        return self._config

    async def execute(
        self,
        state: BaseModel,
        ctx: ExecutionContext,
    ) -> dict[str, Any]:
        """Persist the configured state field as an artifact, emit + patch state.

        Returns a dict patch ``{config.output_field: ref.model_dump()}``
        so the field-merge step (FR-11) writes the
        :class:`ArtifactRef` (JSON-mode dump) into run state under the
        configured key.

        Replay path (``ctx.is_replay=True``): consults the per-node
        cassette via :meth:`_replay_from_cassette`. Hits surface the
        recorded :class:`ArtifactRef` without re-issuing
        :meth:`ArtifactStore.put`. Misses honor ``replay_policy``.
        """
        write_ctx = self._require_write_context(ctx)
        content_bytes = self._coerce_content(getattr(state, self._config.content_field))

        if write_ctx.is_replay:
            return self._replay_from_cassette(write_ctx)

        store = write_ctx.artifact_store
        sidecar_metadata: dict[str, Any] = {
            "content_type": self._config.content_type,
            **self._config.metadata,
        }
        ref: ArtifactRef = await store.put(
            name=self._config.name,
            content=content_bytes,
            metadata=sidecar_metadata,
            run_id=write_ctx.run_id,
            step=write_ctx.step,
        )

        await self._emit_artifact_written(write_ctx, ref)

        ref_payload = ref.model_dump(mode="json")
        self._record_to_cassette(write_ctx, ref_payload)
        return {self._config.output_field: ref_payload}

    # ------------------------------------------------------------------ #
    # internals                                                          #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _require_write_context(ctx: ExecutionContext) -> WriteArtifactContext:
        """Narrow ``ctx`` to :class:`WriteArtifactContext` or raise loudly.

        A missing ``artifact_store``, ``bus``, or ``is_replay`` is a
        wiring bug, not a recoverable runtime condition (mirrors the
        :class:`~stargraph.nodes.subgraph.SubGraphNode` convention). FR-6
        force-loud: surface the missing attributes at the call site.
        """
        if not isinstance(ctx, WriteArtifactContext):
            raise AttributeError(
                "WriteArtifactNode requires an execution context with "
                "`run_id`, `step`, `bus`, `artifact_store`, `is_replay`, "
                "and `fathom`; got " + type(ctx).__name__
            )
        return ctx

    @staticmethod
    def _coerce_content(value: object) -> bytes:
        """Coerce ``str`` payloads to UTF-8 bytes; pass ``bytes`` through.

        ``bytearray`` and ``memoryview`` are accepted (converted via
        ``bytes(...)``) so callers can stream binary data from buffers.
        Anything else raises :class:`TypeError` -- silent coercion of
        arbitrary objects would mask wiring bugs (FR-6).
        """
        if isinstance(value, bytes):
            return value
        if isinstance(value, str):
            return value.encode("utf-8")
        if isinstance(value, bytearray):
            return bytes(value)
        if isinstance(value, memoryview):
            return value.tobytes()
        raise TypeError(
            "WriteArtifactNode content_field must be bytes or str; got " + type(value).__name__
        )

    def _replay_from_cassette(self, ctx: WriteArtifactContext) -> dict[str, Any]:
        """Return the recorded ``ArtifactRef`` payload, or raise loudly on miss.

        Hits return ``{output_field: <ref payload>}`` -- the same shape
        the live path produces. Misses raise :class:`ArtifactStoreError`
        regardless of ``replay_policy``: silently re-writing on replay
        would double the side effect, so both ``fail_loud`` and
        ``must_stub`` map to a loud raise (FR-6).
        """
        cassette, node_id = _cassette_and_node_id(ctx)
        if cassette is not None and node_id:
            recorded = cassette.get(node_id, ctx.step)
            if recorded is not None:
                return {self._config.output_field: dict(recorded)}

        reason = (
            "replay-fail-loud"
            if self._config.replay_policy == "fail_loud"
            else "replay-stub-missing"
        )
        raise ArtifactStoreError(
            f"WriteArtifactNode invoked in replay context (node_id={node_id!r}, "
            f"step={ctx.step}) with replay_policy={self._config.replay_policy!r} "
            "but the node cassette has no recorded ArtifactRef for this step. "
            "Either replay was started without restoring the cassette, or the "
            "live run never executed this node — both are wiring bugs.",
            reason=reason,
            backend="stargraph.nodes.artifacts.WriteArtifactNode",
        )

    @staticmethod
    def _record_to_cassette(
        ctx: WriteArtifactContext,
        ref_payload: dict[str, Any],
    ) -> None:
        """Record ``ref_payload`` on the node cassette; no-op when unwired."""
        cassette, node_id = _cassette_and_node_id(ctx)
        if cassette is None or not node_id:
            return
        cassette.record(node_id, ctx.step, ref_payload)

    async def _emit_artifact_written(
        self,
        ctx: WriteArtifactContext,
        ref: ArtifactRef,
    ) -> None:
        """Publish one :class:`ArtifactWrittenEvent` on the run bus.

        ``provenance`` follows the design §10.3 shape
        (``origin="tool"``, ``source="stargraph.artifacts"``); the typed
        ``Provenance`` model from :mod:`stargraph.runtime.tool_exec` is not
        yet promoted to a public symbol, so we emit the dict shape the
        :class:`ArtifactWrittenEvent` schema accepts (``dict[str, Any]``).
        """
        # ``confidence=1.0`` and ``timestamp`` close out the
        # ProvenanceBundle tuple (origin, source, run_id, step, confidence,
        # timestamp) the JSONL lineage audit (FR-55, AC-11.2) requires on
        # every audited fact -- system-emitted or user-asserted. The
        # artifact write is a deterministic system output, so confidence
        # is always 1.0; the timestamp is the same wall-clock instant
        # stamped into the event envelope.
        now = datetime.now(UTC)
        provenance: dict[str, Any] = {
            "origin": "tool",
            "source": "stargraph.artifacts",
            "run_id": ctx.run_id,
            "step": ctx.step,
            "confidence": 1.0,
            "timestamp": now.isoformat(),
        }
        event = ArtifactWrittenEvent(
            run_id=ctx.run_id,
            step=ctx.step,
            ts=now,
            artifact_ref=ref.model_dump(mode="json"),
            provenance=provenance,
        )
        await ctx.bus.send(event, fathom=ctx.fathom)
