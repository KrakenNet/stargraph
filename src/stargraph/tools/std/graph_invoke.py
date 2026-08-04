# SPDX-License-Identifier: Apache-2.0
"""``std.graph_invoke`` -- run another Stargraph graph as a tool call.

Capability-gated (``tools:std:graph_invoke``): the ``graph`` argument names an
arbitrary YAML file whose node specs may reference arbitrary importable
modules, so invoking a graph is code execution -- never on by default.

The child graph runs in-process on the real engine: authoring-format YAML is
compiled transparently (same detection as ``stargraph run``), full IR YAML is
validated as-is, the builtin tool packs are seeded, and Fathom routes. The
child's checkpoint is an ephemeral SQLite database discarded when the call
returns -- ``graph_invoke`` is a synchronous fire-and-collect primitive, not a
durable-run manager (use ``stargraph run`` / ``serve`` for resumable runs).

A child graph that pauses for human input (``WaitingForInputEvent``) is a
hard error: a tool call has no channel to answer an interrupt.

Model/alias resolution for compiled-weights arms is out of scope here: the
registry exposes no active-alias lookup today, so the child runs whatever its
IR declares. Revisit when an alias-resolution surface exists.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from stargraph.tools.decorator import tool
from stargraph.tools.spec import SideEffects

__all__ = ["graph_invoke"]


@tool(
    name="graph_invoke",
    namespace="std",
    version="1",
    side_effects=SideEffects.external,
    requires_capability="tools:std:graph_invoke",
    description=(
        "Run another Stargraph graph (authoring-format or full-IR YAML) to "
        "completion in-process and return its status and final state. "
        "Requires the tools:std:graph_invoke capability; blocked by default."
    ),
)
async def graph_invoke(graph: str, inputs: dict[str, Any] | None = None) -> dict[str, Any]:
    """Run ``graph``; return ``{status, final_state, run_id, graph_hash}``."""
    # Engine imports stay inside the body: seed_builtin_tools imports this
    # module at registry-seed time, so top-level engine imports would cycle.
    import tempfile

    import anyio
    import yaml

    from stargraph.authoring import compile_authoring, is_authoring_format
    from stargraph.checkpoint.sqlite import SQLiteCheckpointer
    from stargraph.errors import StargraphRuntimeError
    from stargraph.fathom import build_ir_routing
    from stargraph.graph import Graph, GraphRun
    from stargraph.ir import IRDocument
    from stargraph.ir._ids import new_run_id
    from stargraph.nodes.registry import build_node_registry
    from stargraph.registry.tools import ToolRegistry
    from stargraph.runtime.events import ResultEvent, WaitingForInputEvent
    from stargraph.tools.builtin import seed_builtin_tools

    apath = await anyio.Path(graph).resolve()
    if not await apath.is_file():
        raise StargraphRuntimeError(f"std.graph_invoke: graph file not found: {apath}")
    path = Path(apath)

    doc = yaml.safe_load(await apath.read_text(encoding="utf-8"))
    if is_authoring_format(doc):
        ir = compile_authoring(doc, default_id=path.stem)
    else:
        ir = IRDocument.model_validate(doc)

    g = Graph(ir, registry=seed_builtin_tools(ToolRegistry()))
    initial_state = g.state_schema(**(inputs or {}))
    node_registry = build_node_registry(ir.nodes, ir_dir=path.parent)
    run_id = new_run_id()

    final: dict[str, Any] = {}
    paused = False

    async def _consume(run: GraphRun) -> None:
        nonlocal paused
        while True:
            ev = await run.bus.receive()
            if isinstance(ev, WaitingForInputEvent):
                paused = True
                return
            if isinstance(ev, ResultEvent):
                final.update(ev.final_state)
                return

    with tempfile.TemporaryDirectory(prefix="stargraph-graph-invoke-") as tmp:
        checkpointer = SQLiteCheckpointer(Path(tmp) / "run.sqlite")
        await checkpointer.bootstrap()
        try:
            run = GraphRun(
                run_id=run_id,
                graph=g,
                initial_state=initial_state,
                node_registry=node_registry,
                checkpointer=checkpointer,
                fathom=build_ir_routing(ir, g.state_schema),
            )
            summary, _ = await asyncio.gather(run.start(), _consume(run))
        finally:
            await checkpointer.close()

    if paused:
        raise StargraphRuntimeError(
            "std.graph_invoke: child graph paused for human input; a tool call "
            "cannot answer interrupts -- run it via `stargraph run` or serve"
        )
    return {
        "status": summary.status,
        "final_state": final,
        "run_id": run_id,
        "graph_hash": g.graph_hash,
    }
