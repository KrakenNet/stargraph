# SPDX-License-Identifier: Apache-2.0
"""Pytest fixtures + import wiring for the SOC Triage++ graph tests.

The demo dir is ``demos/soc-triage`` (hyphen → not importable as a Python
package), so the IR's ``module:Class`` refs use ``graph.state`` / ``graph.nodes``
as a *top-level* package and ``serve_soc.py`` inserts ``demos/soc-triage`` on
``sys.path`` at boot (task 1.28 / 1.33). Pytest collects the tests without that
boot, so this conftest reproduces the same path insertion and then exposes a
``soc_graph`` fixture that builds the real :class:`~stargraph.graph.definition.Graph`
the way ``serve_soc.py`` does (loads ``graph/stargraph.yaml`` + injects the absolute
``file://`` URI of the sha256-pinned ONNX model into the ``risk_score`` node).

Tests build a real :class:`~stargraph.graph.run.GraphRun` from this graph (no
mocking of the graph under test) and drive it in-process — exactly the
construction the serve scheduler uses (``stargraph.serve.scheduler._drive_real_run``)
minus the HTTP layer.
"""

from __future__ import annotations

import asyncio
import contextlib
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator

# Demo package root (…/demos/soc-triage). Insert on sys.path BEFORE any
# ``import graph.*`` so the IR's ``graph.nodes:IngestAlert`` /
# ``graph.state:RunState`` refs resolve, mirroring serve_soc.py's boot.
DEMO_ROOT = Path(__file__).resolve().parents[1]
if str(DEMO_ROOT) not in sys.path:
    sys.path.insert(0, str(DEMO_ROOT))

GRAPH_PATH = DEMO_ROOT / "graph" / "stargraph.yaml"
ONNX_MODEL = DEMO_ROOT / "models" / "severity_classifier.onnx"

# Pinned label the committed ONNX classifier returns for the hero alert
# (case_8821: prod ransomware) — high. The benign dev counterfactual
# (alrt-1004) returns low. These come from the real model, not a fixture.
RISK_HIGH = 2
RISK_LOW = 0


@dataclass
class FakeCtx:
    """Minimal :class:`~stargraph.nodes.base.ExecutionContext` stand-in.

    Custom nodes only read ``run_id`` off the context (AuditChain keys its
    JSONL file by it); the real driver passes the concrete ``GraphRun``.
    """

    run_id: str = "soc-test-run"


def build_graph() -> Any:
    """Build the real soc-triage++ Graph exactly as ``serve_soc.py`` does.

    Loads ``graph/stargraph.yaml`` into an :class:`IRDocument`, injects the
    absolute ``file://`` URI of the sha256-pinned ONNX model into the
    ``risk_score`` node config (the committed IR keeps ``file_uri: null`` so it
    validates portably), and returns the constructed
    :class:`~stargraph.graph.definition.Graph`.
    """
    import yaml

    from stargraph.graph.definition import Graph
    from stargraph.ir._models import IRDocument

    ir = IRDocument.model_validate(yaml.safe_load(GRAPH_PATH.read_text(encoding="utf-8")))
    model_uri = ONNX_MODEL.resolve().as_uri()
    for node in ir.nodes:
        if node.id == "risk_score":
            node.config["file_uri"] = model_uri
    return Graph(ir=ir)


@pytest.fixture()
def soc_graph() -> Any:
    """The real soc-triage++ :class:`Graph` (ONNX URI injected)."""
    return build_graph()


@pytest.fixture()
def node_registry(soc_graph: Any) -> dict[str, Any]:
    """The per-graph node registry (loads + sha256-pins the ONNX model).

    Built via the same ``stargraph.cli.run._build_node_registry`` the serve path
    uses; constructing ``risk_score`` here is what verifies the ``expected_sha256``
    pin against the model bytes (raises on mismatch).
    """
    from stargraph.cli.run import _build_node_registry

    return _build_node_registry(soc_graph.ir.nodes, ir_dir=GRAPH_PATH.parent.resolve())


@pytest.fixture()
async def run_to_pause(soc_graph: Any, node_registry: dict[str, Any]) -> Any:
    """Factory: drive a fresh in-process run for ``alert`` to its pause/terminal.

    Returns an async callable ``run_to_pause(run_id, **state_params)`` →
    ``(GraphRun, SQLiteCheckpointer)``. Builds a real
    :class:`~stargraph.graph.run.GraphRun` (no graph mocking) with a real SQLite
    checkpointer — the same construction as
    ``stargraph.serve.scheduler._drive_real_run`` — and drives ``.start()`` on a
    background task, returning once the run reaches ``awaiting-input`` (the HITL
    gate) or otherwise terminates.

    The ``analyst_gate`` interrupt carries a finite ``timeout`` (so the live
    serve loop takes its hot-resume path: it blocks on ``_respond_event`` while
    awaiting-input rather than exiting cold — see spec 5.3 fix). Awaiting
    ``start()`` to completion would therefore block until the timeout expires;
    instead the fixture polls ``run.state`` until the pause boundary, mirroring
    the live scheduler driving the loop as a task. ``triage_decide`` resolves
    to the deterministic stub DSPy node, so no LLM is required. The caller
    closes the checkpointer; the fixture cancels any still-running drive tasks
    on teardown.
    """
    from stargraph.checkpoint.sqlite import SQLiteCheckpointer
    from stargraph.graph.run import GraphRun

    tasks: list[asyncio.Task[Any]] = []

    async def _run(run_id: str, **state_params: Any) -> tuple[Any, Any]:
        tmp = Path(tempfile.mkdtemp(prefix="soc-test-"))
        checkpointer = SQLiteCheckpointer(tmp / "checkpoint.sqlite")
        await checkpointer.bootstrap()
        initial_state = soc_graph.state_schema(**state_params)
        run = GraphRun(
            run_id=run_id,
            graph=soc_graph,
            initial_state=initial_state,
            node_registry=node_registry,
            checkpointer=checkpointer,
            capabilities=None,
            fathom=None,
        )
        task = asyncio.create_task(run.start())
        tasks.append(task)
        # Poll until the run reaches the HITL pause (awaiting-input) or
        # otherwise terminates. The interrupt's finite timeout means the loop
        # stays alive at the gate (hot-resume contract), so we observe the
        # state transition instead of awaiting start() to return.
        for _ in range(1000):
            if run.state == "awaiting-input" or task.done():
                break
            await asyncio.sleep(0.005)
        if task.done():
            # Surface a terminal-path failure (e.g. an exception in a node)
            # rather than returning a half-driven run.
            task.result()
        return run, checkpointer

    yield _run

    for task in tasks:
        if not task.done():
            task.cancel()
    for task in tasks:
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task


async def collect_checkpoints(checkpointer: Any, run_id: str) -> list[Any]:
    """Read every persisted checkpoint for ``run_id`` in step order."""
    out: list[Any] = []
    step = 0
    while True:
        cp = await checkpointer.read_at_step(run_id, step)
        if cp is None:
            break
        out.append(cp)
        step += 1
    return out


# Re-export so tests can ``from conftest import ...`` without re-deriving paths.
__all__ = [
    "DEMO_ROOT",
    "GRAPH_PATH",
    "ONNX_MODEL",
    "RISK_HIGH",
    "RISK_LOW",
    "FakeCtx",
    "build_graph",
    "collect_checkpoints",
]


# Silence unused-import lint for the TYPE_CHECKING-only iterators (kept for
# readers of the fixture signatures; not referenced at runtime).
if TYPE_CHECKING:  # pragma: no cover
    _ = (AsyncIterator, Iterator)
