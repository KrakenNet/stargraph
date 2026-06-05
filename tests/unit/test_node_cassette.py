# SPDX-License-Identifier: Apache-2.0
"""Unit tests for the per-node cassette protocol (design §10.3).

Covers the round-trip contract that
:class:`harbor.nodes.artifacts.WriteArtifactNode` (the first consumer)
relies on:

* Live run records its ``ArtifactRef`` payload on the cassette.
* Replay run reads the recorded payload back without invoking
  :meth:`ArtifactStore.put`.
* Replay miss raises :class:`ArtifactStoreError` loudly, regardless of
  ``replay_policy``.

Also smokes the cassette state-serialization round-trip used by the
checkpointer to persist node-cassette state across restarts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, cast

import pytest
from pydantic import BaseModel

from harbor.errors import ArtifactStoreError
from harbor.nodes.artifacts import WriteArtifactNode, WriteArtifactNodeConfig
from harbor.replay.cassettes import InMemoryNodeCassette

if TYPE_CHECKING:
    from harbor.graph import GraphRun


class _FakeBus:
    """Minimal event bus duck — collects sent events for assertions."""

    def __init__(self) -> None:
        self.events: list[Any] = []

    async def send(self, event: Any, *, fathom: Any = None) -> None:
        del fathom
        self.events.append(event)


class _FakeArtifactStore:
    """In-memory ArtifactStore stub satisfying the put() contract."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def put(
        self,
        *,
        name: str,
        content: bytes,
        metadata: dict[str, Any],
        run_id: str,
        step: int,
    ) -> Any:
        from datetime import UTC, datetime

        from harbor.artifacts import ArtifactRef

        self.calls.append({"name": name, "content": content, "run_id": run_id, "step": step})
        digest = "0" * 64
        return ArtifactRef(
            artifact_id=digest[:32],
            content_hash=digest,
            name=name,
            content_type=metadata.get("content_type", "application/octet-stream"),
            run_id=run_id,
            step=step,
            created_at=datetime.now(UTC),
        )


@dataclass
class _Ctx:
    """Duck-typed WriteArtifactContext for tests."""

    run_id: str
    step: int
    bus: _FakeBus
    artifact_store: _FakeArtifactStore
    is_replay: bool
    fathom: Any = None
    node_cassette: Any | None = None
    node_id: str = ""


class _State(BaseModel):
    payload: str = "hello"
    artifact_ref: dict[str, Any] = field(  # type: ignore[assignment]
        default_factory=dict
    )

    model_config = {"arbitrary_types_allowed": True}


def _make_node() -> WriteArtifactNode:
    return WriteArtifactNode(
        config=WriteArtifactNodeConfig(
            content_field="payload",
            name="result.txt",
            content_type="text/plain",
        )
    )


@pytest.mark.unit
async def test_live_run_records_to_cassette() -> None:
    """First-run path writes the artifact and stamps the cassette."""
    cassette = InMemoryNodeCassette()
    ctx = _Ctx(
        run_id="run-1",
        step=3,
        bus=_FakeBus(),
        artifact_store=_FakeArtifactStore(),
        is_replay=False,
        node_cassette=cassette,
        node_id="write_result",
    )

    out = await _make_node().execute(_State(), ctx)

    assert "artifact_ref" in out
    recorded = cassette.get("write_result", 3)
    assert recorded == out["artifact_ref"]
    assert ctx.artifact_store.calls, "live run must call ArtifactStore.put"


@pytest.mark.unit
async def test_replay_returns_recorded_payload() -> None:
    """Replay reads from the cassette without re-issuing the side effect."""
    cassette = InMemoryNodeCassette()

    # Stage 1: live run populates the cassette.
    live_ctx = _Ctx(
        run_id="run-1",
        step=3,
        bus=_FakeBus(),
        artifact_store=_FakeArtifactStore(),
        is_replay=False,
        node_cassette=cassette,
        node_id="write_result",
    )
    live_out = await _make_node().execute(_State(), live_ctx)

    # Stage 2: replay run reads the cassette; ArtifactStore must not be touched.
    replay_store = _FakeArtifactStore()
    replay_ctx = _Ctx(
        run_id="run-1",
        step=3,
        bus=_FakeBus(),
        artifact_store=replay_store,
        is_replay=True,
        node_cassette=cassette,
        node_id="write_result",
    )
    replay_out = await _make_node().execute(_State(), replay_ctx)

    assert replay_out == live_out
    assert replay_store.calls == [], "replay must not call ArtifactStore.put"


@pytest.mark.unit
async def test_replay_miss_raises_loudly() -> None:
    """Replay with no recorded entry raises :class:`ArtifactStoreError`."""
    cassette = InMemoryNodeCassette()  # empty
    ctx = _Ctx(
        run_id="run-1",
        step=3,
        bus=_FakeBus(),
        artifact_store=_FakeArtifactStore(),
        is_replay=True,
        node_cassette=cassette,
        node_id="write_result",
    )

    with pytest.raises(ArtifactStoreError) as exc_info:
        await _make_node().execute(_State(), ctx)
    # Either fail_loud or must_stub maps to a clear miss reason.
    assert exc_info.value.context["reason"] in {"replay-stub-missing", "replay-fail-loud"}


class _RaisingNode:
    """Node body that always raises -- exercises the dispatch finally arm."""

    async def execute(self, state: Any, run: Any) -> dict[str, Any]:
        del state, run
        raise RuntimeError("boom")


class _OkNode:
    """No-op node body."""

    async def execute(self, state: Any, run: Any) -> dict[str, Any]:
        del state, run
        return {}


class _FakeCheckpointer:
    async def write(self, checkpoint: Any) -> None:
        del checkpoint


class _FakeMirror:
    def schedule(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    def retract_step(self) -> None:
        return None

    async def persist_pinned(self, *_args: Any, **_kwargs: Any) -> None:
        return None


@dataclass
class _FakeGraph:
    graph_hash: str = "h"
    runtime_hash: str = "r"


@dataclass
class _FakeRun:
    run_id: str = "run-1"
    parent_run_id: str | None = None
    fathom: Any = None
    fact_store: Any = None
    node_id: str = ""
    graph: Any = field(default_factory=_FakeGraph)
    checkpointer: Any = field(default_factory=_FakeCheckpointer)
    mirror_scheduler: Any = field(default_factory=_FakeMirror)
    bus: Any = field(default_factory=_FakeBus)
    node_registry: dict[str, Any] = field(default_factory=lambda: {})


@pytest.mark.unit
async def test_dispatch_node_clears_node_id_after_raise() -> None:
    """``dispatch_node`` clears ``run.node_id`` even when the node raises.

    Stamping is in a try/finally so a faulting node cannot leak its id
    onto the next tick's cassette key. Without this, a subsequent
    write-side-effect node would record/lookup against the wrong key.
    """
    from harbor.ir._models import NodeSpec
    from harbor.runtime.dispatch import dispatch_node

    nodes = [NodeSpec(id="will_raise", kind="stub")]
    run = _FakeRun(node_registry={"will_raise": _RaisingNode()})

    class _S(BaseModel):
        x: int = 0

    with pytest.raises(RuntimeError, match="boom"):
        await dispatch_node(cast("GraphRun", run), nodes, nodes[0], _S(), step=0)
    assert run.node_id == "", "node_id must be cleared in the finally arm"


@pytest.mark.unit
async def test_dispatch_node_clears_node_id_on_success() -> None:
    """Successful tick also clears ``run.node_id`` after node body returns."""
    from harbor.ir._models import NodeSpec
    from harbor.runtime.dispatch import dispatch_node

    nodes = [NodeSpec(id="ok", kind="stub")]
    run = _FakeRun(node_registry={"ok": _OkNode()})

    class _S(BaseModel):
        x: int = 0

    await dispatch_node(cast("GraphRun", run), nodes, nodes[0], _S(), step=0)
    assert run.node_id == ""


@pytest.mark.unit
def test_in_memory_cassette_round_trip() -> None:
    """``to_state`` / ``from_state`` round-trips an in-memory cassette."""
    cassette = InMemoryNodeCassette()
    cassette.record("node_a", 1, {"uri": "mem://x", "size": 7})
    cassette.record("node_b", 2, {"uri": "mem://y", "size": 11})

    serialized = cassette.to_state()
    restored = InMemoryNodeCassette.from_state(serialized)

    assert restored.get("node_a", 1) == {"uri": "mem://x", "size": 7}
    assert restored.get("node_b", 2) == {"uri": "mem://y", "size": 11}
    assert restored.get("node_a", 99) is None
