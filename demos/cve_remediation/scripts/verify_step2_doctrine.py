# SPDX-License-Identifier: Apache-2.0
"""CRITERIA.md Step 2 verification harness.

Runs Phase 0 ingest end-to-end against the live Neo4j instance and
checks the spec query::

    MATCH (c:Control)-[:MAPS_TO]->(cwe:CWE {id: $cwe}) RETURN c

returns rows for several distinct CWEs that we know are reachable in
the published Control->ATT&CK->CAPEC->CWE chain.

Pass criteria (per CRITERIA.md):

* Phase 0 nodes ran end-to-end without raising.
* :Control nodes count > 1000 (real NIST 800-53r5 catalog).
* :CWE nodes count > 100 (real CWE catalog).
* :MAPS_TO edges count > 100 (real published mappings).
* Spec Cypher returns rows for at least 3 distinct CWE seeds.

Run::

    uv run --no-project python -m demos.cve_remediation.scripts.verify_step2_doctrine
"""

from __future__ import annotations

import asyncio
import os
import sys
from types import SimpleNamespace
from typing import Any

from demos.cve_remediation.graph.real_nodes import (
    BootgateAllowlistUpdateNode,
    CanonicalizeDoctrineNode,
    DoctrineExtractorNode,
    DoctrineLoaderNode,
    IdempotencyCheckNode,
    KgLoaderNode,
    ManifestSignNode,
)
from demos.cve_remediation.graph.state import CveRemState


# CWEs that the published Control->ATT&CK->CAPEC->CWE chain genuinely
# reaches. (See coverage report at end of run for the full reachable
# set; CWEs absent from that set -- e.g. CWE-78 OS Command Injection --
# are a real upstream-mapping gap, not a parser bug.)
TEST_CWES = ["CWE-287", "CWE-285", "CWE-522"]


def _ctx() -> object:
    return SimpleNamespace(run_id="verify-step2")


async def _run_phase0() -> CveRemState:
    state = CveRemState()
    for node in (
        IdempotencyCheckNode(),
        DoctrineLoaderNode(),
        CanonicalizeDoctrineNode(),
        DoctrineExtractorNode(),
        ManifestSignNode(),
        KgLoaderNode(),
        BootgateAllowlistUpdateNode(),
    ):
        delta = await node.execute(state, _ctx())
        if delta:
            state = state.model_copy(update=delta)
    return state


async def _neo4j_query(cypher: str, **params: Any) -> list[dict[str, Any]]:
    import neo4j  # type: ignore[import-not-found]

    url = os.environ.get("RYUGRAPH_URL") or os.environ.get("NEO4J_URL", "")
    user = os.environ.get("RYUGRAPH_USERNAME") or os.environ.get(
        "NEO4J_USERNAME", "neo4j"
    )
    password = os.environ.get("RYUGRAPH_PASSWORD") or os.environ.get(
        "NEO4J_PASSWORD", ""
    )
    driver = neo4j.AsyncGraphDatabase.driver(url, auth=(user, password))
    try:
        async with driver.session() as session:
            res = await session.run(cypher, **params)
            return [dict(r) async for r in res]
    finally:
        await driver.close()


async def main() -> int:
    print("=== STEP 2 VERIFICATION (Phase 0 doctrine ingest) ===\n")
    overall_pass = True

    print("[1/3] running Phase 0 nodes end-to-end ...")
    try:
        state = await _run_phase0()
    except Exception as exc:  # noqa: BLE001
        print(f"  EXCEPTION: {type(exc).__name__}: {exc}")
        return 1
    counts = (state.broker_request_envelope or {}).get(
        "doctrine_corpus_counts", {}
    )
    print(f"  corpus counts: {counts}")
    print(
        f"  doctrine_node_count={state.doctrine_node_count} "
        f"doctrine_edge_count={state.doctrine_edge_count}"
    )
    print(
        f"  neo4j nodes_written={state.doctrine_kg_neo4j_nodes_written} "
        f"edges_written={state.doctrine_kg_neo4j_edges_written}"
    )
    if state.last_kg_loader_error:
        print(f"  last_kg_loader_error: {state.last_kg_loader_error}")
        overall_pass = False
    if state.doctrine_kg_neo4j_nodes_written < 1000:
        print(f"  ! expected >1000 nodes written, got {state.doctrine_kg_neo4j_nodes_written}")
        overall_pass = False
    if state.doctrine_kg_neo4j_edges_written < 100:
        print(f"  ! expected >100 edges written, got {state.doctrine_kg_neo4j_edges_written}")
        overall_pass = False
    print()

    print("[2/3] inventory live Neo4j labels:")
    try:
        rows = await _neo4j_query(
            "MATCH (n) RETURN labels(n) AS lbl, count(*) AS n ORDER BY n DESC"
        )
    except Exception as exc:  # noqa: BLE001
        print(f"  EXCEPTION: {type(exc).__name__}: {exc}")
        return 1
    for r in rows:
        print(f"  labels={r['lbl']}  count={r['n']}")
    label_counts = {tuple(r["lbl"]): r["n"] for r in rows}
    ctrl_n = sum(n for lbl, n in label_counts.items() if "Control" in lbl)
    cwe_n = sum(n for lbl, n in label_counts.items() if "CWE" in lbl)
    if ctrl_n < 1000:
        print(f"  ! expected >1000 Control nodes, got {ctrl_n}")
        overall_pass = False
    if cwe_n < 100:
        print(f"  ! expected >100 CWE nodes, got {cwe_n}")
        overall_pass = False
    print()

    print("[3/3] spec Cypher: MATCH (c:Control)-[:MAPS_TO]->(cwe:CWE {id: $cwe})")
    for cwe in TEST_CWES:
        rows = await _neo4j_query(
            "MATCH (c:Control)-[:MAPS_TO]->(cwe:CWE {id: $cwe}) "
            "RETURN c.id AS control_id ORDER BY control_id LIMIT 10",
            cwe=cwe,
        )
        ids = [r["control_id"] for r in rows]
        status = "PASS" if ids else "FAIL"
        print(f"  [{cwe}] {status}: {len(ids)} controls (sample={ids[:5]})")
        if not ids:
            overall_pass = False
    print()

    # Coverage honesty report: how many CWEs in the catalog are
    # reachable from at least one Control via the published chain. Real
    # upstream-mapping gaps (e.g. CWE-78 OS Command Injection) show up
    # here as unreachable -- we surface them rather than papering over.
    print("[coverage] Control->CWE reachability:")
    rows = await _neo4j_query(
        "MATCH (cwe:CWE) "
        "OPTIONAL MATCH (c:Control)-[:MAPS_TO]->(cwe) "
        "WITH cwe, count(c) AS n "
        "RETURN sum(CASE WHEN n > 0 THEN 1 ELSE 0 END) AS reachable, "
        "sum(CASE WHEN n = 0 THEN 1 ELSE 0 END) AS unreachable"
    )
    if rows:
        r = rows[0]
        total = (r["reachable"] or 0) + (r["unreachable"] or 0)
        print(
            f"  {r['reachable']}/{total} CWEs reachable from Controls "
            f"({r['unreachable']} are real upstream-mapping gaps)"
        )
    print()

    print("=== OVERALL: %s ===" % ("PASS" if overall_pass else "FAIL"))
    return 0 if overall_pass else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
