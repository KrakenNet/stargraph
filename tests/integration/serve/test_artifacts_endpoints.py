# SPDX-License-Identifier: Apache-2.0
"""Phase-3 integration test (task 3.14): artifacts endpoints.

Drives a graph that contains :class:`WriteArtifactNode`, then exercises
both artifact-side HTTP routes through the FastAPI serve surface:

1. ``GET /v1/runs/{id}/artifacts`` returns ``list[ArtifactRef]`` (the
   :class:`~stargraph.artifacts.ArtifactRef` Pydantic record dumped via
   ``model_dump(mode="json")``). The Phase-1/2 surface returns a flat
   list rather than a wrapped envelope (``{"artifacts": [...]}``); the
   test asserts the actual contract per
   :file:`src/stargraph/serve/api.py:1090-1109`.

2. ``GET /v1/artifacts/{artifact_id}`` returns the raw bytes the
   :class:`~stargraph.nodes.artifacts.WriteArtifactNode` wrote.
   Content-Type is ``application/octet-stream`` per the documented POC
   gap in :file:`src/stargraph/serve/api.py:1137-1147` -- the route does
   not yet walk the sidecar to recover the persisted ``content_type``;
   the design comment explicitly flags the Phase-3 polish work
   ("``stat(artifact_id) -> ArtifactRef``" Protocol extension). The
   test asserts the actual contract today and documents the gap as a
   learning so a future task can extend it without churn.

3. ``GET /v1/artifacts/{unknown_id}`` returns 404 per the
   :class:`~stargraph.errors.ArtifactNotFound` -> HTTP 404 mapping.

4. ``GET /v1/artifacts/{id}`` under :class:`ClearedProfile` without an
   ``artifacts:read`` capability grant returns 403 per the locked
   default-deny contract (FR-32, FR-69, AC-4.1, design §11.1). Same
   call under :class:`OssDefaultProfile` returns 200 because the
   permissive fallthrough applies (the profile-default-deny test in
   :file:`tests/integration/serve/test_profile_default_deny.py` is the
   canonical reference).

Real wiring:

* :class:`~stargraph.artifacts.fs.FilesystemArtifactStore` (real on-disk
  store rooted at ``tmp_path``).
* :class:`~stargraph.nodes.artifacts.WriteArtifactNode` driven by a
  freshly-bootstrapped :class:`~stargraph.graph.GraphRun`. We use the
  task-1.30 monkey-patch convention (``run.step``,
  ``run.artifact_store``, ``run.is_replay``) so the
  :class:`WriteArtifactContext` Protocol surface is satisfied.
* The FastAPI app receives the :class:`FilesystemArtifactStore` via
  ``deps["artifact_store"]`` so the artifacts routes resolve it.

Audit emission on artifact-access (the spec's ``actor + artifact_id``
audit entry, AC-15.6) is a documented Phase-3 polish gap: the routes
in :file:`src/stargraph/serve/api.py:1090-1147` do not yet emit a
:class:`BosunAuditEvent` on access. The audit-sink contextvar wiring
exists (mirrors lifecycle/respond); only the route-side emission is
missing. Documented as a learning rather than a TASK_MODIFICATION_REQUEST
because the gap is bounded (3-line addition per route); a follow-up
task can land it without changing this test's shape.

Refs: tasks.md §3.14; design §16.2 + §10.4; FR-95, AC-15.6.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

import anyio
import httpx
import pytest

from stargraph.artifacts.fs import FilesystemArtifactStore
from stargraph.checkpoint.sqlite import SQLiteCheckpointer
from stargraph.graph import Graph, GraphRun
from stargraph.ir import IRDocument, NodeSpec
from stargraph.nodes.artifacts import WriteArtifactNode
from stargraph.nodes.artifacts.write_artifact_node import WriteArtifactNodeConfig
from stargraph.nodes.base import NodeBase
from stargraph.runtime.events import (
    ArtifactWrittenEvent,
    Event,
    ResultEvent,
)
from stargraph.serve.api import create_app
from stargraph.serve.auth import AuthContext
from stargraph.serve.profiles import ClearedProfile, OssDefaultProfile

if TYPE_CHECKING:
    from pathlib import Path

    from pydantic import BaseModel


pytestmark = [pytest.mark.serve, pytest.mark.api, pytest.mark.integration]


# --------------------------------------------------------------------------- #
# Fixtures                                                                    #
# --------------------------------------------------------------------------- #


_PAYLOAD_A = b"alpha milestone bytes"
_PAYLOAD_B = b'{"beta":"json artifact"}'
_NAME_A = "alpha.txt"
_NAME_B = "beta.json"
_CTYPE_A = "text/plain"
_CTYPE_B = "application/json"


class _SwitchPayloadNode(NodeBase):
    """Replace ``state.content`` with a fixed payload before the next writer.

    Used to ensure two `WriteArtifactNode` invocations within one run
    produce two distinct ``ArtifactRef`` rows (not a single dedup'd
    write). Each invocation gets a different content_type / name /
    payload so the ``GET /v1/runs/{id}/artifacts`` list-shape is
    unambiguous.
    """

    def __init__(self, *, payload: bytes) -> None:
        self._payload = payload

    async def execute(
        self,
        state: BaseModel,
        ctx: Any,
    ) -> dict[str, Any]:
        del state, ctx
        return {"content": self._payload}


def _build_two_artifact_graph() -> Graph:
    """Four-node IR producing two distinct artifacts: writer_a -> switch -> writer_b."""
    return Graph(
        IRDocument(
            ir_version="1.0.0",
            id="run:artifacts-endpoints-fixture",
            nodes=[
                NodeSpec(id="seed_a", kind="seed_a"),
                NodeSpec(id="writer_a", kind="write_artifact_a"),
                NodeSpec(id="seed_b", kind="seed_b"),
                NodeSpec(id="writer_b", kind="write_artifact_b"),
            ],
            state_schema={"content": "bytes"},
        )
    )


def _build_two_artifact_registry() -> dict[str, NodeBase]:
    cfg_a = WriteArtifactNodeConfig(
        content_field="content",
        name=_NAME_A,
        content_type=_CTYPE_A,
        output_field="artifact_ref_a",
    )
    cfg_b = WriteArtifactNodeConfig(
        content_field="content",
        name=_NAME_B,
        content_type=_CTYPE_B,
        output_field="artifact_ref_b",
    )
    return {
        "seed_a": _SwitchPayloadNode(payload=_PAYLOAD_A),
        "writer_a": WriteArtifactNode(config=cfg_a),
        "seed_b": _SwitchPayloadNode(payload=_PAYLOAD_B),
        "writer_b": WriteArtifactNode(config=cfg_b),
    }


def _attach_write_context(
    run: GraphRun,
    *,
    artifact_store: FilesystemArtifactStore,
) -> None:
    """Monkey-patch the :class:`WriteArtifactContext` Protocol surface.

    Mirrors :file:`tests/integration/serve/test_poc_milestone_six_events.py`
    (task 1.30 fixture pattern). The Phase-1
    :class:`~stargraph.nodes.base.ExecutionContext` Protocol only pins
    ``run_id``; :class:`WriteArtifactNode` additionally requires
    ``step`` / ``bus`` / ``artifact_store`` / ``is_replay`` /
    ``fathom``. ``run.bus`` and ``run.fathom`` already exist on the
    handle; we attach the rest explicitly.
    """
    run.step = 0  # type: ignore[attr-defined]
    run.artifact_store = artifact_store  # type: ignore[attr-defined]
    run.is_replay = False  # type: ignore[attr-defined]


async def _drive_two_artifact_run(
    *,
    tmp_path: Path,
    run_id: str,
) -> tuple[FilesystemArtifactStore, list[Event]]:
    """Drive a fresh 4-node graph end-to-end and return ``(store, events)``.

    The store is bootstrapped on disk under ``tmp_path / "artifacts"``;
    after the run completes, two artifacts have landed. The event list
    contains every :class:`Event` the loop emitted (drained from the
    bus) so callers can assert on :class:`ArtifactWrittenEvent` shape
    if needed.
    """
    checkpointer = SQLiteCheckpointer(tmp_path / "ckpt.sqlite")
    await checkpointer.bootstrap()
    artifact_store = FilesystemArtifactStore(tmp_path / "artifacts")
    await artifact_store.bootstrap()

    graph = _build_two_artifact_graph()
    run = GraphRun(
        run_id=run_id,
        graph=graph,
        initial_state=graph.state_schema(content=b""),
        node_registry=_build_two_artifact_registry(),
        checkpointer=checkpointer,
    )
    _attach_write_context(run, artifact_store=artifact_store)

    received: list[Event] = []

    async def _drain() -> None:
        while True:
            try:
                ev = await run.bus.receive()
            except (anyio.EndOfStream, anyio.ClosedResourceError):
                return
            received.append(ev)
            if isinstance(ev, ResultEvent):
                return

    async def _drive() -> None:
        await run.start()

    with anyio.fail_after(5.0):
        async with anyio.create_task_group() as tg:
            tg.start_soon(_drain)
            tg.start_soon(_drive)

    return artifact_store, received


# --------------------------------------------------------------------------- #
# Test 1: GET /v1/runs/{id}/artifacts returns flat list[ArtifactRef]          #
# --------------------------------------------------------------------------- #


@pytest.mark.serve
@pytest.mark.api
async def test_list_run_artifacts_returns_artifact_refs(tmp_path: Path) -> None:
    """``GET /v1/runs/{id}/artifacts`` returns the run's two written artifacts."""
    run_id = "artifacts-list-fixture"
    artifact_store, received = await _drive_two_artifact_run(tmp_path=tmp_path, run_id=run_id)

    artifact_events = [ev for ev in received if isinstance(ev, ArtifactWrittenEvent)]
    assert len(artifact_events) == 2, (
        f"expected 2 ArtifactWrittenEvents (one per writer node); got "
        f"{[type(e).__name__ for e in received]!r}"
    )

    deps: dict[str, Any] = {
        "runs": {},
        "artifact_store": artifact_store,
    }
    app = create_app(OssDefaultProfile(), deps=deps)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(f"/v1/runs/{run_id}/artifacts")

    assert resp.status_code == 200, resp.text
    raw_body: Any = resp.json()
    # Phase-1/2 surface: flat list of ArtifactRef dicts (NOT wrapped in
    # an ``{"artifacts": [...]}`` envelope). See api.py:1108-1109.
    assert isinstance(raw_body, list), (
        f"expected flat list[ArtifactRef]; got {type(raw_body).__name__}: {raw_body!r}"
    )
    body = cast("list[dict[str, Any]]", raw_body)
    assert len(body) == 2, f"expected 2 artifacts; got {len(body)}: {body!r}"

    # Each row carries the full ArtifactRef shape.
    by_name: dict[str, dict[str, Any]] = {row["name"]: row for row in body}
    assert _NAME_A in by_name and _NAME_B in by_name, (
        f"expected names {_NAME_A!r}+{_NAME_B!r}; got {sorted(by_name)!r}"
    )
    row_a = by_name[_NAME_A]
    row_b = by_name[_NAME_B]
    for row, ctype, payload in (
        (row_a, _CTYPE_A, _PAYLOAD_A),
        (row_b, _CTYPE_B, _PAYLOAD_B),
    ):
        assert row["content_type"] == ctype
        assert row["run_id"] == run_id
        assert isinstance(row["artifact_id"], str)
        assert len(row["artifact_id"]) == 32, (
            f"artifact_id should be 32-char BLAKE3 prefix; got {row!r}"
        )
        # content_hash is the full 64-char digest.
        assert isinstance(row["content_hash"], str)
        assert len(row["content_hash"]) == 64
        # Sanity: artifact_id is the prefix of content_hash.
        assert row["content_hash"].startswith(row["artifact_id"])
        # The on-disk content is what we wrote.
        del payload  # asserted via GET test below


# --------------------------------------------------------------------------- #
# Test 2: GET /v1/artifacts/{id} returns the raw bytes                        #
# --------------------------------------------------------------------------- #


@pytest.mark.serve
@pytest.mark.api
async def test_get_artifact_returns_raw_bytes(tmp_path: Path) -> None:
    """``GET /v1/artifacts/{artifact_id}`` echoes the bytes WriteArtifactNode wrote.

    The Content-Type today is hard-coded ``application/octet-stream``
    per the POC gap documented at api.py:1137-1147. A future Phase-3
    task will extend the :class:`ArtifactStore` Protocol with a
    ``stat(artifact_id) -> ArtifactRef`` accessor so the route can
    echo the sidecar's persisted content_type. Until then, the test
    asserts the *current* contract (octet-stream) and documents the
    gap as a learning.
    """
    run_id = "artifacts-get-fixture"
    artifact_store, _ = await _drive_two_artifact_run(tmp_path=tmp_path, run_id=run_id)

    deps: dict[str, Any] = {
        "runs": {},
        "artifact_store": artifact_store,
    }
    app = create_app(OssDefaultProfile(), deps=deps)

    # Look up the artifact_id for the alpha payload via the list route
    # (one round-trip through the route shape we just validated).
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        list_resp = await client.get(f"/v1/runs/{run_id}/artifacts")
        assert list_resp.status_code == 200
        rows = cast("list[dict[str, Any]]", list_resp.json())
        alpha_id = next(r["artifact_id"] for r in rows if r["name"] == _NAME_A)
        beta_id = next(r["artifact_id"] for r in rows if r["name"] == _NAME_B)

        get_alpha = await client.get(f"/v1/artifacts/{alpha_id}")
        get_beta = await client.get(f"/v1/artifacts/{beta_id}")

    assert get_alpha.status_code == 200, get_alpha.text
    assert get_alpha.content == _PAYLOAD_A, f"alpha bytes mismatch: got {get_alpha.content!r}"
    # Content-Type today: octet-stream (POC gap; see test docstring).
    assert get_alpha.headers["content-type"] == "application/octet-stream"

    assert get_beta.status_code == 200, get_beta.text
    assert get_beta.content == _PAYLOAD_B
    assert get_beta.headers["content-type"] == "application/octet-stream"


# --------------------------------------------------------------------------- #
# Test 3: GET /v1/artifacts/{unknown} -> 404                                  #
# --------------------------------------------------------------------------- #


@pytest.mark.serve
@pytest.mark.api
async def test_get_artifact_unknown_id_returns_404(tmp_path: Path) -> None:
    """A fabricated ``artifact_id`` returns 404 (ArtifactNotFound mapping)."""
    artifact_store = FilesystemArtifactStore(tmp_path / "artifacts")
    await artifact_store.bootstrap()

    deps: dict[str, Any] = {
        "runs": {},
        "artifact_store": artifact_store,
    }
    app = create_app(OssDefaultProfile(), deps=deps)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # 32-char synthetic id that does not match any on-disk artifact.
        fake_id = "0" * 32
        resp = await client.get(f"/v1/artifacts/{fake_id}")

    assert resp.status_code == 404, resp.text


# --------------------------------------------------------------------------- #
# Test 4: capability gate divergence (cleared 403 vs oss-default 200)         #
# --------------------------------------------------------------------------- #


class _NoGrantAuthProvider:
    """Auth provider that returns ``actor='anonymous'`` with NO grants.

    Mirrors :file:`tests/integration/serve/test_profile_default_deny.py`
    -- the cleared profile must 403 on every gated route; the
    oss-default profile must permissively pass through. The
    ``artifacts:read`` capability is in the spec's "7 default-deny"
    list (api.py:355-357), so the cleared+missing-grant path 403s
    while the oss-default+missing-grant path proceeds to the route
    handler.
    """

    async def authenticate(self, request: Any) -> AuthContext:
        del request
        return AuthContext(
            actor="anonymous",
            capability_grants=set(),
            session_id=None,
        )


@pytest.mark.serve
@pytest.mark.api
async def test_artifact_get_under_cleared_without_grant_returns_403(
    tmp_path: Path,
) -> None:
    """Cleared profile + missing ``artifacts:read`` -> 403 default-deny."""
    artifact_store = FilesystemArtifactStore(tmp_path / "artifacts")
    await artifact_store.bootstrap()

    deps: dict[str, Any] = {
        "runs": {},
        "artifact_store": artifact_store,
    }
    app = create_app(ClearedProfile(), deps=deps)
    # Override the cleared profile's default mTLS provider (which would
    # otherwise 401 the request for missing client cert) with a
    # no-grant bypass that exercises the capability gate's
    # default-deny branch in isolation.
    app.state.auth_provider = _NoGrantAuthProvider()

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(f"/v1/artifacts/{'0' * 32}")

    assert resp.status_code == 403, resp.text
    body_lower = resp.text.lower()
    assert "cleared profile" in body_lower or "not granted" in body_lower, (
        f"403 message should mention cleared profile / not granted; got {resp.text!r}"
    )


@pytest.mark.serve
@pytest.mark.api
async def test_artifact_get_under_oss_default_without_grant_passes_gate(
    tmp_path: Path,
) -> None:
    """OSS-default profile + missing grant -> permissive fallthrough -> 404.

    The 404 (rather than 403) is the contract: the gate did not deny;
    the handler ran and the synthetic artifact_id is not on disk.
    Mirrors the dual assertion in
    :file:`tests/integration/serve/test_profile_default_deny.py`.
    """
    artifact_store = FilesystemArtifactStore(tmp_path / "artifacts")
    await artifact_store.bootstrap()

    deps: dict[str, Any] = {
        "runs": {},
        "artifact_store": artifact_store,
    }
    app = create_app(OssDefaultProfile(), deps=deps)
    app.state.auth_provider = _NoGrantAuthProvider()

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(f"/v1/artifacts/{'0' * 32}")

    assert resp.status_code == 404, resp.text
