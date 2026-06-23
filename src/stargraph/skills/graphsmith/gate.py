# SPDX-License-Identifier: Apache-2.0
"""The graph smith verify gate — the "always works" contract for graph bundles.

The three-tier shape + subprocess isolation live in :mod:`stargraph.skills._smith.gate`;
this module supplies the *graph* contract: assemble the bundle (``state.py`` +
``nodes.py`` + an auto-wired ``graph.yaml`` + ``test_nodes.py``), then in a
subprocess LOAD it into a real :class:`stargraph.graph.Graph`, build the node
registry (resolving every ``kind`` to a ``NodeBase``), and RUN the graph to a
terminal :class:`~stargraph.runtime.events.ResultEvent`, asserting it reached
``status="done"`` and that the nodes wired end-to-end (the declared ``expects``
fields appear in the final state). Because the assert is on a real run's observable
output, a trivially-passing generated unit test cannot land a bundle whose nodes
do not actually connect.

``graph.yaml`` is auto-assembled here (not LLM-emitted) so the wiring — the
``state_class`` ref and the per-node ``kind`` paths — is correct by construction;
the model only writes the state model, the node logic, and the fixture.

TRUST BOUNDARY: see :mod:`stargraph.skills._smith.gate` - tiers 2-3 execute
LLM-generated code in a subprocess (process isolation, not a sandbox).
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from stargraph.skills._smith.gate import (
    RUN_GRAPH_PRELUDE,
    VerifierResult,
    all_passed,
    make_contract_tier,
    run_tiered_gate,
)
from stargraph.skills._smith.nodes import snake

__all__ = [
    "GRAPH_FILE",
    "NODES_FILE",
    "STATE_FILE",
    "TEST_FILE",
    "VerifierResult",
    "all_passed",
    "assemble_graph_yaml",
    "run_full_gate",
    "verify_sources",
]

STATE_FILE = "state.py"
NODES_FILE = "nodes.py"
GRAPH_FILE = "graph.yaml"
TEST_FILE = "test_nodes.py"


def assemble_graph_yaml(graph_id: str, node_classes: list[str]) -> str:
    """Wire ``node_classes`` (ordered) into a linear ``graph.yaml`` IR string.

    The state model is fixed to the bundle's ``state.py`` (``state:State``) and each
    node ``kind`` points at the bundle's ``nodes.py`` (``nodes:<ClassName>``); node
    ids are unique snake-case slugs. ``rules: []`` → the loop runs the nodes in
    listed order and halts at end-of-nodes, so the bundle runs under plain
    ``stargraph run``.
    """
    gid = snake(graph_id, default="graph")
    lines = [
        'ir_version: "1.0.0"',
        f'id: "graph:{gid}"',
        'state_class: "state:State"',
        "nodes:",
    ]
    seen: dict[str, int] = {}
    for cls in node_classes:
        nid = snake(cls, default="node")
        if nid in seen:
            seen[nid] += 1
            nid = f"{nid}_{seen[nid]}"
        else:
            seen[nid] = 0
        lines.append(f"  - id: {nid}")
        lines.append(f'    kind: "nodes:{cls}"')
    lines.append("rules: []")
    return "\n".join(lines) + "\n"


# The contract driver: the shared run-graph prelude (load + run the assembled
# bundle to a terminal ``done`` with the fixture's ``expects`` produced) plus the
# graph verdict line. The prelude is reused verbatim by every graph-running smith;
# the only graph-specific part is the success payload (graph_hash + node ids).
_CONTRACT_DRIVER = RUN_GRAPH_PRELUDE + (
    'print(json.dumps({"ok": True, "graph_hash": g.graph_hash,'
    ' "nodes": [n.id for n in ir.nodes]}))\n'
)


def run_full_gate(
    work_dir: Path,
    files: dict[str, str],
    *,
    fixture: dict[str, Any],
) -> list[VerifierResult]:
    """static → contract → tests in ``work_dir``, short-circuiting on first failure.

    Shared verbatim by the build node and the offline optimizer's metric. The
    contract tier loads the assembled bundle into a real ``Graph`` and runs it to
    ``done`` against ``fixture`` (``inputs`` + ``expects``); see ``_CONTRACT_DRIVER``.
    """
    return run_tiered_gate(
        work_dir,
        files,
        contract_tier=make_contract_tier(
            _CONTRACT_DRIVER,
            {"fixture": fixture, "meta": {"run_id": "graphsmith-contract", "noun": "graph"}},
        ),
        test_file=TEST_FILE,
    )


def verify_sources(
    *,
    graph_id: str,
    node_classes: list[str],
    state_source: str,
    nodes_source: str,
    test_source: str,
    fixture: dict[str, Any],
) -> tuple[bool, list[VerifierResult]]:
    """Run the full gate on a raw bundle in a throwaway temp dir.

    The convenience entry point for callers that hold source strings rather than a
    work dir — ``graphsmith make``, the doctor preflight, and seed verification.
    Assembles ``graph.yaml`` from ``graph_id`` + ``node_classes``. Returns
    ``(passed, results)``.
    """
    files = {
        STATE_FILE: state_source,
        NODES_FILE: nodes_source,
        GRAPH_FILE: assemble_graph_yaml(graph_id, node_classes),
        TEST_FILE: test_source,
    }
    with tempfile.TemporaryDirectory(prefix="graphsmith-verify-") as d:
        results = run_full_gate(Path(d), files, fixture=fixture)
    return all_passed(results), results
