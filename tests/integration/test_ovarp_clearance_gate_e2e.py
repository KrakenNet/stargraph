# SPDX-License-Identifier: Apache-2.0
"""Cross-repo end-to-end: a Fathom governance tick → auto-lowered OVARP receipt → verify.

The governance counterpart of ``test_ovarp_receipt_e2e`` (which attests a *routing*
decision against a hand-authored offline pack). Here the offline evaluator is **not
hand-authored**: the receipt's policy pack is produced by lowering the graph's own
Fathom pack with ``ovarp lower-fathom`` (P2.1 in-stack auto-emit). No mocks, no
hand-built receipts:

1. A live ``ClearanceGateNode`` tick is dispatched through
   :func:`stargraph.runtime.dispatch.dispatch_node` with an
   :class:`~stargraph.ovarp.sink.OvarpReceiptSink` wired as ``receipt_sink``.
   Fathom (CLIPS) decides ``deny`` (a *secret* clearance may not read *top-secret*);
   the sink auto-lowers the pack and emits a signed ``replayable`` receipt for it.
2. ``ovarp verify --replay`` drives ``stargraph ovarp-reproduce`` to reproduce the
   tick bit-for-bit → **VALID** at grade ``replayable``. Step 4 re-derives the same
   decision from the *lowered* pack — the two independent evaluators (CLIPS engine,
   OVARP IR) agree (ADR-0010).
3. Offline (no ``--replay``) the same receipt degrades to ``external-replayable``.
4. Two independent tampers each VOID: a lied decision (OVARP's offline re-derivation
   over the auto-lowered pack catches it at step 4) and a corrupted replay bundle
   (the content-address binding catches it at step 6).

Requires the ``ovarp`` binary: ``$OVARP_BIN`` if set, else ``../ovarp/target/release/ovarp``,
else ``ovarp`` on ``PATH``. Skips if absent.
"""

from __future__ import annotations

import asyncio
import json
import os
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

from stargraph.errors import StargraphRuntimeError
from stargraph.ovarp.clearance_gate import (
    CLEARANCE_GRAPH_IR,
    CLEARANCE_RULES,
    ClearanceGateState,
    clearance_gate_attestation_spec,
)
from stargraph.ovarp.harness import (
    CapturingCheckpointer,
    build_attestable_run,
    materialize_pack_dir,
)
from stargraph.ovarp.sink import OvarpReceiptSink
from stargraph.runtime.dispatch import dispatch_node

pytestmark = pytest.mark.integration


def _find_ovarp() -> str | None:
    """Locate the ``ovarp`` verifier binary; ``None`` → the suite skips."""
    env = os.environ.get("OVARP_BIN")
    if env and Path(env).is_file():
        return env
    sibling = Path(__file__).resolve().parents[2].parent / "ovarp" / "target" / "release" / "ovarp"
    if sibling.is_file():
        return str(sibling)
    return shutil.which("ovarp")


def _replayer_cmd(store: Path) -> str:
    """The ``--replayer`` shell command OVARP runs to reproduce the tick."""
    return (
        f"{shlex.quote(sys.executable)} -m stargraph.cli "
        f"ovarp-reproduce --store {shlex.quote(str(store))}"
    )


def _verify(
    ovarp: str, receipt: Path, store: Path, *extra: str
) -> subprocess.CompletedProcess[str]:
    """Run ``ovarp verify <receipt> --store <store> [extra...]`` and capture the result."""
    return subprocess.run(
        [ovarp, "verify", str(receipt), "--store", str(store), *extra],
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize("bad", ["../escape", "a/b", "/etc/x", "..", ".", ""])
def test_materialize_pack_dir_rejects_path_traversal(tmp_path: Path, bad: str) -> None:
    """``pack_name`` is a single path segment; traversal/absolute names fail closed.

    Pure guard test (no ``ovarp`` binary): a crafted ``pack_name`` must never escape
    ``dest`` and drive ``mkdir``/``write_text`` outside the caller's temp dir.
    """
    with pytest.raises(StargraphRuntimeError, match="single path segment"):
        materialize_pack_dir(ClearanceGateState, CLEARANCE_RULES, tmp_path, bad)
    assert list(tmp_path.iterdir()) == [], "guard must reject before writing anything"


@dataclass(frozen=True)
class _Emitted:
    ovarp: str
    store: Path
    out: Path
    receipt: Path
    request: Path


@pytest.fixture(scope="module")
def emitted(tmp_path_factory: pytest.TempPathFactory) -> _Emitted:
    """Dispatch one attested clearance-gate tick and emit its receipt (once per module).

    The request carries operator-facing labels (``"Secret"`` / ``"Top Secret"``) so the
    node's canonicalization is exercised on the attested path.
    """
    ovarp = _find_ovarp()
    if ovarp is None:
        pytest.skip("ovarp binary not found (set $OVARP_BIN or build ../ovarp)")

    base = tmp_path_factory.mktemp("ovarp_clearance_e2e")
    store, out = base / "store", base / "out"
    sink = OvarpReceiptSink(
        clearance_gate_attestation_spec(), store_dir=store, out_dir=out, ovarp_bin=ovarp
    )

    async def _emit_tick() -> None:
        run, nodes = build_attestable_run(
            ir_dict=CLEARANCE_GRAPH_IR,
            state_values={
                "clearance": "Secret",
                "classification": "Top Secret",
                "agent_id": "agent-7",
                "resource": "dossier-9",
            },
            fathom_pack_text=CLEARANCE_RULES,
            run_id="e2e-clearance-gate",
            checkpointer=CapturingCheckpointer(),
        )
        run.receipt_sink = sink
        gate = next(n for n in nodes if n.id == "clearance-gate")
        await dispatch_node(run, nodes, gate, run.initial_state, 0)

    asyncio.run(_emit_tick())
    assert sink.receipts, "receipt sink emitted nothing for the attested tick"
    receipt = sink.receipts[0]
    request = receipt.with_name(receipt.name.replace(".receipt.json", ".request.json"))
    return _Emitted(ovarp=ovarp, store=store, out=out, receipt=receipt, request=request)


def test_replay_reproduces_valid(emitted: _Emitted) -> None:
    """``verify --replay`` reproduces the tick bit-for-bit → VALID at grade replayable."""
    result = _verify(
        emitted.ovarp,
        emitted.receipt,
        emitted.store,
        "--replay",
        "--replayer",
        _replayer_cmd(emitted.store),
        "--min-grade",
        "replayable",
    )
    assert result.returncode == 0, f"expected VALID, got:\n{result.stdout}\n{result.stderr}"
    assert "VALID" in result.stdout
    # The differentiator (step 4, over the AUTO-LOWERED pack) and the reproduction
    # (step 6) both passed.
    assert "outcome MATCHES receipt" in result.stdout
    assert "reproduced result bit-for-bit" in result.stdout


def test_receipt_binds_the_auto_lowered_governance_decision(emitted: _Emitted) -> None:
    """The receipt attests the real Fathom decision over an auto-lowered offline pack."""
    payload = json.loads(emitted.request.read_text())
    # The in-stack Fathom decision — a secret clearance may not read top-secret.
    assert payload["producer_output"]["outcome"] == "deny"
    # Facts are the Mirror FactVector, canonicalized by the node (Secret → secret).
    assert payload["facts"] == {
        "facts": [
            {"template": "clearance", "data": {"value": "secret"}},
            {"template": "classification", "data": {"value": "top-secret"}},
        ]
    }
    # The offline pack is the lowered IR, not an OVARP v0 hand-pack.
    assert payload["pack"]["ovarp_ir"] == "v0.1"
    assert payload["pack"]["id"] == "clearance-gate"
    assert payload["pack"]["default"] == "deny"
    assert payload["replay_trace"]["runtime"] == "stargraph@0.3"


def test_offline_degrades_to_external_replayable(emitted: _Emitted) -> None:
    """Without ``--replay`` the receipt is external-replayable, not replayable (ADR-0012 floor)."""
    ext = _verify(
        emitted.ovarp, emitted.receipt, emitted.store, "--min-grade", "external-replayable"
    )
    assert ext.returncode == 0, f"expected VALID @ external-replayable:\n{ext.stdout}\n{ext.stderr}"
    assert "VALID" in ext.stdout

    strict = _verify(emitted.ovarp, emitted.receipt, emitted.store, "--min-grade", "replayable")
    assert strict.returncode != 0, "replayable floor must VOID without --replay"
    assert "VOID" in strict.stdout


def test_lied_decision_voids_at_policy(emitted: _Emitted, tmp_path: Path) -> None:
    """A receipt claiming ``allow`` over deny facts VOIDs at OVARP's step-4 re-eval.

    This is the ADR-0010 independence proof: OVARP re-runs the *auto-lowered* pack over
    the FactVector and re-derives ``deny``, rejecting the producer's ``allow``.
    """
    request = json.loads(emitted.request.read_text())
    request["receipt_id"] = "e2e-clearance-gate-lie"
    request["producer_output"] = {**request["producer_output"], "outcome": "allow"}
    lie_request = tmp_path / "lie.request.json"
    lie_request.write_text(json.dumps(request))
    lie_receipt = tmp_path / "lie.receipt.json"

    emit = subprocess.run(
        [
            emitted.ovarp,
            "emit",
            str(lie_request),
            "--store",
            str(emitted.store),
            "--out",
            str(lie_receipt),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert emit.returncode == 0, f"emit of the lie failed:\n{emit.stderr}"

    result = _verify(
        emitted.ovarp,
        lie_receipt,
        emitted.store,
        "--replay",
        "--replayer",
        _replayer_cmd(emitted.store),
    )
    assert result.returncode != 0, "a lied decision must VOID"
    assert "VOID" in result.stdout
    assert "policy" in result.stdout


def test_tampered_bundle_voids(emitted: _Emitted, tmp_path: Path) -> None:
    """Corrupting the content-addressed replay bundle VOIDs reproduction at step 6."""
    store_copy = tmp_path / "store"
    shutil.copytree(emitted.store, store_copy)

    bundle_digest: str = json.loads(emitted.request.read_text())["replay_trace"]["bundle"]
    blob = store_copy / "blobs" / bundle_digest.split(":", 1)[-1]
    raw = bytearray(blob.read_bytes())
    raw[0] ^= 0xFF  # any change breaks the content-address
    blob.write_bytes(raw)

    result = _verify(
        emitted.ovarp,
        emitted.receipt,
        store_copy,
        "--replay",
        "--replayer",
        _replayer_cmd(store_copy),
    )
    assert result.returncode != 0, "a corrupted replay bundle must VOID"
    assert "VOID" in result.stdout
