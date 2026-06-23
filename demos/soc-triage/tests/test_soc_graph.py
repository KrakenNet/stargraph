# SPDX-License-Identifier: Apache-2.0
"""SOC Triage++ graph tests (task 3.7).

Real, in-process exercise of the soc-triage++ Stargraph graph — no mocking of the
graph under test. Each test builds the actual
:class:`~stargraph.graph.definition.Graph` from ``graph/stargraph.yaml`` (with the
sha256-pinned ONNX model URI injected, exactly as ``serve_soc.py`` does) and,
where a run is needed, drives a real :class:`~stargraph.graph.run.GraphRun` the way
``stargraph.serve.scheduler._drive_real_run`` builds it — minus the HTTP layer. No
LLM is required: ``triage_decide`` (``kind: dspy``) resolves to stargraph's
deterministic stub DSPy node, so the run is reproducible offline.

Coverage (tasks.md 3.7 + Phase-2 reviewer suggestion):

1. **graph loads** — IR validates, stable ``graph_hash``, all 9 nodes + the
   HITL ``analyst_gate`` interrupt present, node registry builds (the ML node
   constructs carrying the pin).
2. **ML sha256 matches pin** — the ``expected_sha256`` in the committed IR
   equals the actual SHA-256 of the committed ONNX model bytes (supply-chain
   integrity), cross-checked against stargraph's own ``_sha256_of`` hasher; the
   constructed :class:`~stargraph.nodes.ml.MLNode` carries that pin.
3. **run reaches analyst_gate interrupt** — a real run on the hero alert
   ``case_8821`` (prod ransomware) pauses awaiting HITL input, having written a
   checkpoint per upstream node (ingest → soc_policy), with the real ONNX
   classifier scoring ``risk=2`` (high).
4. **counterfactual replay diff observable** — re-running the same graph with a
   mutated input (the benign dev alert ``alrt-1004``) flips the real ONNX risk
   label 2 → 0 and the one-hot tier features change; the diff is observable in
   the checkpointed state.
5. **audit chain CHAIN_VALID + TAMPER_EVIDENT** — the ``AuditChain`` terminal
   node seals the run's provenance into a hash-chained JSONL record that
   re-verifies, and editing any sealed fact breaks the next line's hash.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

from conftest import (
    GRAPH_PATH,
    ONNX_MODEL,
    RISK_HIGH,
    RISK_LOW,
    FakeCtx,
    build_graph,
    collect_checkpoints,
)

if TYPE_CHECKING:
    from collections.abc import Callable

# Upstream nodes that run before the HITL gate — each writes one checkpoint.
_PRE_GATE_NODES = ["ingest", "retrieval", "risk_score", "triage_decide", "soc_policy"]


# ---------------------------------------------------------------------------
# 1. Graph loads
# ---------------------------------------------------------------------------


def test_graph_loads_with_pinned_ml_node() -> None:
    """The IR validates into a real Graph with the expected topology + hash."""
    graph = build_graph()

    assert graph.ir.id == "graph:soc-triage"
    # graph_hash is stable + structural (the pin is in the IR; node-kind
    # strings do not change it). The 12-char prefix matches the value recorded
    # across tasks 1.29 / 1.35 / 1.39.
    assert graph.graph_hash[:12] == "366df32814b6"

    node_ids = [n.id for n in graph.ir.nodes]
    assert node_ids == [
        "ingest",
        "retrieval",
        "risk_score",
        "triage_decide",
        "soc_policy",
        "analyst_gate",
        "write_artifact",
        "audit",
        "halt",
    ]

    # The HITL gate is a real interrupt node.
    gate = next(n for n in graph.ir.nodes if n.id == "analyst_gate")
    assert gate.kind == "interrupt"


def test_node_registry_builds(node_registry: dict[str, Any]) -> None:
    """Every node id resolves to a constructed NodeBase (registry build)."""
    from stargraph.nodes.ml import MLNode

    assert set(node_registry) == {
        "ingest",
        "retrieval",
        "risk_score",
        "triage_decide",
        "soc_policy",
        "analyst_gate",
        "write_artifact",
        "audit",
        "halt",
    }
    # risk_score is the ONNX MLNode carrying the sha256 pin (not dropped).
    assert isinstance(node_registry["risk_score"], MLNode)
    assert node_registry["risk_score"].expected_sha256 == _ir_pinned_sha256()


# ---------------------------------------------------------------------------
# 2. ML sha256 matches pin
# ---------------------------------------------------------------------------


def _ir_pinned_sha256() -> str:
    """The ``expected_sha256`` pinned on the ``risk_score`` node in the IR."""
    import yaml

    ir = yaml.safe_load(GRAPH_PATH.read_text(encoding="utf-8"))
    risk_node = next(n for n in ir["nodes"] if n["id"] == "risk_score")
    return str(risk_node["config"]["expected_sha256"])


def test_ml_model_sha256_matches_pin() -> None:
    """The committed ONNX bytes hash to exactly the pinned ``expected_sha256``.

    Supply-chain integrity: the artifact shipped in ``models/`` must match the
    digest declared in the graph IR. Cross-checked against stargraph's own
    ``stargraph.ml.loaders._sha256_of`` so this asserts the same hasher the loader
    uses, not just hashlib in isolation.
    """
    from stargraph.ml.loaders import _sha256_of

    assert ONNX_MODEL.is_file()
    pinned = _ir_pinned_sha256()

    actual = hashlib.sha256(ONNX_MODEL.read_bytes()).hexdigest()
    assert actual == pinned, f"model bytes {actual} != IR pin {pinned}"

    # Same digest via stargraph's loader-side hasher (the production code path).
    assert _sha256_of(ONNX_MODEL) == pinned


# ---------------------------------------------------------------------------
# 3. Run reaches the analyst_gate interrupt
# ---------------------------------------------------------------------------


async def test_run_reaches_analyst_gate_interrupt(
    run_to_pause: Callable[..., Any],
) -> None:
    """A real run on case_8821 pauses at the HITL gate after ML scoring.

    The ONNX classifier (loaded under the sha256 pin) scores the prod
    ransomware alert as high risk (``risk == 2``); the run then halts
    ``awaiting-input`` at ``analyst_gate``, having checkpointed each upstream
    node. No LLM is configured — ``triage_decide`` is the deterministic stub.
    """
    run, checkpointer = await run_to_pause("soc-gate-run", alert_id="case_8821")
    try:
        # Interrupt pause surfaces as the awaiting-input lifecycle state.
        assert run.state == "awaiting-input"

        checkpoints = await collect_checkpoints(checkpointer, "soc-gate-run")
        # One checkpoint per upstream node, in order; the interrupt node itself
        # raises before checkpointing, so the trail ends at soc_policy.
        assert [cp.last_node for cp in checkpoints] == _PRE_GATE_NODES

        latest = checkpoints[-1]
        # Real ONNX inference ran: case_8821 (prod ransomware) → high risk.
        assert latest.state["risk"] == RISK_HIGH
        assert latest.state["asset_tier"] == "prod"
        # IngestAlert built the 7-float feature vector (load-bearing order).
        assert latest.state["features"] == [9.6, 0.0, 0.0, 1.0, 0.1, 3.0, 8.0]
        # Provenance accumulated across the custom nodes (not last-write-wins).
        prov_nodes = [ev["node"] for ev in latest.state["provenance"]]
        assert prov_nodes == ["ingest", "retrieval"]
    finally:
        await checkpointer.close()


# ---------------------------------------------------------------------------
# 3b. HITL respond → resume → halt (post-respond completion, spec 5.3 fix)
# ---------------------------------------------------------------------------


def test_respond_resumes_run_to_completion_with_valid_audit_chain(
    soc_graph: Any,
    node_registry: dict[str, Any],
) -> None:
    """A paused HITL run resumes to ``done`` after respond, sealing a valid audit chain.

    Reproduces the spec ``.progress.md`` 5.3 blocker end-to-end on the real
    graph (no graph mocking): drive case_8821 to the ``analyst_gate`` pause,
    ``POST``-equivalent ``respond()``, and confirm the *same* live loop advances
    past the gate through ``write_artifact → audit → halt`` to ``done`` — then
    that the ``AuditChain`` node wrote a ``.audit/<run_id>.jsonl`` record whose
    hash chain re-verifies link-by-link (AC-8.2).

    The fix under test is pure demo-owned config: the ``analyst_gate`` interrupt
    carries a finite ``timeout`` so ``execute()`` takes its hot-resume path
    (blocks on ``_respond_event`` while awaiting-input) instead of exiting cold,
    and ``write_artifact`` is the custom ``graph.nodes:SocWriteArtifact`` node
    (needs only ``run_id``) rather than the builtin whose context protocol the
    serve-path ``GraphRun`` cannot satisfy. No stargraph core is touched.

    Sync test driving its own ``asyncio.run`` (repo convention, e.g.
    ``tests/integration/test_counterfactual_e2e.py``): the loop's hot-resume
    arm races ``anyio.move_on_after`` against ``GraphRun._respond_event`` and
    deadlocks under pytest-asyncio's shared function-loop when mixed with
    ``asyncio.wait_for``-driven cancellation, so the run is driven on a private
    event loop exactly as ``serve_soc.py``'s scheduler does at runtime.
    """
    run_id = "soc-resume-run"
    demo_root = Path(GRAPH_PATH).parents[1]
    safe_run = run_id.replace("/", "_").replace(":", "_")
    audit_path = demo_root / ".audit" / f"{safe_run}.jsonl"
    artifact_path = demo_root / ".artifacts" / f"{safe_run}-soc-case-note.md"

    async def _drive() -> Any:
        from stargraph.checkpoint.sqlite import SQLiteCheckpointer
        from stargraph.graph.run import GraphRun

        tmp = Path(tempfile.mkdtemp(prefix="soc-resume-"))
        checkpointer = SQLiteCheckpointer(tmp / "checkpoint.sqlite")
        await checkpointer.bootstrap()
        run = GraphRun(
            run_id=run_id,
            graph=soc_graph,
            initial_state=soc_graph.state_schema(alert_id="case_8821"),
            node_registry=node_registry,
            checkpointer=checkpointer,
            capabilities=None,
            fathom=None,
        )
        # Drive as a background task — exactly the live serve shape: the loop
        # blocks awaiting-input on the interrupt's finite timeout; respond
        # wakes the *same* live loop (no cold restart).
        task = asyncio.create_task(run.start())
        try:
            for _ in range(500):
                if run.state == "awaiting-input":
                    break
                await asyncio.sleep(0.01)
            assert run.state == "awaiting-input", f"never reached gate (state={run.state!r})"

            await run.respond(
                {"decision": "approve", "analyst_decision": "approve", "note": "confirmed"},
                actor="analyst",
            )
            summary = await asyncio.wait_for(task, timeout=30)
            return run.state, summary.status
        finally:
            if not task.done():
                task.cancel()
            await checkpointer.close()

    try:
        final_state, final_status = asyncio.run(_drive())

        # The run completed — NOT hung at running, NOT failed.
        assert final_state == "done"
        assert final_status == "done"

        # write_artifact ran: a case-note artifact was persisted + the path
        # patched into state.
        assert artifact_path.is_file(), "SocWriteArtifact did not persist the case note"

        # The audit chain JSONL exists and re-verifies (CHAIN_VALID).
        assert audit_path.is_file(), "AuditChain did not write the JSONL record"
        lines = [json.loads(line) for line in audit_path.read_text().splitlines() if line.strip()]
        # The full pre-audit provenance trail is sealed, ending at write_artifact.
        assert [line["event"]["node"] for line in lines] == [
            "ingest",
            "retrieval",
            "write_artifact",
        ]
        assert _verify_chain(lines) is True
    finally:
        # Clean the demo-local artifacts this test wrote (keep the tree clean).
        audit_path.unlink(missing_ok=True)
        artifact_path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# 4. Counterfactual replay — observable diff
# ---------------------------------------------------------------------------


async def test_counterfactual_replay_diff_observable(
    run_to_pause: Callable[..., Any],
) -> None:
    """Mutating the input flips the real ONNX risk label — an observable diff.

    The auditor's what-if: re-run the same graph (byte-identical model, same
    deterministic stub triage) against a mutated alert. The hero case_8821
    (prod, severity 9.6) scores high (2); the benign dev counterfactual
    alrt-1004 (dev, severity 1.5) scores low (0). The risk label and the
    one-hot tier features diverge in the checkpointed terminal state — the
    diff the ``replay_drill.sh`` walkthrough surfaces.
    """
    orig, orig_cp = await run_to_pause("soc-cf-orig", alert_id="case_8821")
    cf, cf_cp = await run_to_pause("soc-cf-mut", alert_id="alrt-1004")
    try:
        orig_state = (await orig_cp.read_latest("soc-cf-orig")).state
        cf_state = (await cf_cp.read_latest("soc-cf-mut")).state

        # Both runs reach the same gate (static IR route under Fathom-less serve).
        assert orig.state == cf.state == "awaiting-input"

        # The OBSERVABLE diff is in the state, driven by the real model:
        assert orig_state["risk"] == RISK_HIGH
        assert cf_state["risk"] == RISK_LOW
        assert orig_state["risk"] != cf_state["risk"]

        # Tier one-hot flips prod → dev (feature-vector columns 1..3).
        assert orig_state["asset_tier"] == "prod"
        assert cf_state["asset_tier"] == "dev"
        assert orig_state["features"][1:4] == [0.0, 0.0, 1.0]  # prod one-hot
        assert cf_state["features"][1:4] == [1.0, 0.0, 0.0]  # dev one-hot

        # Determinism: the original run is byte-stable when re-driven.
        _rerun, rerun_cp = await run_to_pause("soc-cf-rerun", alert_id="case_8821")
        try:
            rerun_state = (await rerun_cp.read_latest("soc-cf-rerun")).state
            assert rerun_state["risk"] == orig_state["risk"]
            assert rerun_state["features"] == orig_state["features"]
        finally:
            await rerun_cp.close()
    finally:
        await orig_cp.close()
        await cf_cp.close()


# ---------------------------------------------------------------------------
# 5. Audit chain — CHAIN_VALID + TAMPER_EVIDENT
# ---------------------------------------------------------------------------


def _canonical(record: dict[str, Any]) -> bytes:
    """Mirror ``graph.nodes._canonical`` — stable hashing bytes for a record."""
    return json.dumps(record, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _verify_chain(lines: list[dict[str, Any]]) -> bool:
    """Re-verify the hash-chain links in a list of audit JSONL records."""
    prev = "0" * 64  # genesis link
    for rec in lines:
        record = dict(rec)
        sha = record.pop("sha256")
        if record["prev_sha256"] != prev:
            return False
        digest = hashlib.sha256(prev.encode("utf-8") + _canonical(record)).hexdigest()
        if digest != sha:
            return False
        prev = sha
    return True


async def test_audit_chain_valid_and_tamper_evident(tmp_path: Any) -> None:
    """AuditChain seals provenance into a tamper-evident hash chain.

    Runs the real ``graph.nodes.AuditChain`` over an accumulated provenance
    trail, re-verifies the emitted ``.audit/<run_id>.jsonl`` chain
    (CHAIN_VALID), then edits a sealed fact and confirms re-verification fails
    (TAMPER_EVIDENT). This is the demo-local hash-chained audit contract from
    .progress.md tasks 1.33-1.35 / 2.5.
    """
    import graph.nodes as soc_nodes
    from graph.state import ProvenanceEvent, RunState

    # Redirect the audit dir into tmp_path so the test writes no repo files.
    monkeyed_dir = tmp_path / ".audit"
    original_dir = soc_nodes._AUDIT_DIR
    soc_nodes._AUDIT_DIR = monkeyed_dir
    try:
        run_id = "audit-chain-test"
        state = RunState(
            run_id=run_id,
            provenance=[
                ProvenanceEvent(
                    node="ingest",
                    kind="ingest",
                    summary="ingested case_8821",
                    detail={"alert_id": "case_8821"},
                ),
                ProvenanceEvent(
                    node="retrieval",
                    kind="retrieval",
                    summary="fused 4 prior(s)",
                    detail={"prior_count": 4},
                ),
            ],
        )

        out = await soc_nodes.AuditChain().execute(state, FakeCtx(run_id=run_id))
        # AuditChain appends its own seal event to the accumulating trail.
        assert [ev.node for ev in out["provenance"]] == ["ingest", "retrieval", "audit"]

        audit_path = monkeyed_dir / f"{run_id}.jsonl"
        assert audit_path.is_file(), "AuditChain did not write the JSONL record"
        lines = [json.loads(line) for line in audit_path.read_text().splitlines() if line.strip()]
        # One sealed line per pre-seal provenance fact (ingest + retrieval).
        assert len(lines) == 2

        # CHAIN_VALID — the emitted chain re-verifies link-by-link.
        assert _verify_chain(lines) is True

        # TAMPER_EVIDENT — editing any sealed fact breaks the chain.
        tampered = [dict(line) for line in lines]
        tampered[0]["event"]["summary"] = "TAMPERED"
        assert _verify_chain(tampered) is False
    finally:
        soc_nodes._AUDIT_DIR = original_dir
