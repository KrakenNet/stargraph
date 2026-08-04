# SPDX-License-Identifier: Apache-2.0
"""Cross-repo end-to-end: a real StarGraph Fathom-routed tick → OVARP receipt → verify (ADR-0012).

This is the integration proof that StarGraph's OVARP receipt sink and reproducer
compose with the OVARP verifier end to end, with **no mocks and no hand-built
receipts**:

1. A live ``MergeGateNode`` tick is dispatched through
   :func:`stargraph.runtime.dispatch.dispatch_node` with an
   :class:`~stargraph.ovarp.sink.OvarpReceiptSink` wired as ``receipt_sink``.
   Fathom (CLIPS) routes the merge gate to ``escalate`` over the mirrored facts;
   the sink emits a signed producer-runtime ``replayable`` receipt for it.
2. ``ovarp verify --replay`` drives ``stargraph ovarp-reproduce`` to reproduce the
   tick bit-for-bit → **VALID** at grade ``replayable``.
3. Offline (no ``--replay``) the same receipt degrades to ``external-replayable``
   assurance (ADR-0012 grade floor).
4. Two independent tampers each VOID: a lied routing outcome (OVARP's offline
   re-derivation catches it at step 4) and a corrupted replay bundle (the
   content-address binding catches it at step 6).

Requires the ``ovarp`` binary: ``$OVARP_BIN`` if set, else the sibling repo's
``../ovarp/target/release/ovarp``, else ``ovarp`` on ``PATH``. Skips if absent.
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

from stargraph.ovarp.example import (
    GOV_GRAPH_IR,
    GOV_ROUTING_PACK,
    OVARP_FACTS,
    merge_gate_attestation_spec,
)
from stargraph.ovarp.harness import CapturingCheckpointer, build_attestable_run
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


@dataclass(frozen=True)
class _Emitted:
    ovarp: str
    store: Path
    out: Path
    receipt: Path
    request: Path


@pytest.fixture(scope="module")
def emitted(tmp_path_factory: pytest.TempPathFactory) -> _Emitted:
    """Dispatch one attested merge-gate tick and emit its receipt (once per module)."""
    ovarp = _find_ovarp()
    if ovarp is None:
        pytest.skip("ovarp binary not found (set $OVARP_BIN or build ../ovarp)")

    base = tmp_path_factory.mktemp("ovarp_e2e")
    store, out = base / "store", base / "out"
    sink = OvarpReceiptSink(
        merge_gate_attestation_spec(), store_dir=store, out_dir=out, ovarp_bin=ovarp
    )

    async def _emit_tick() -> None:
        run, nodes = build_attestable_run(
            ir_dict=GOV_GRAPH_IR,
            state_values=OVARP_FACTS,
            fathom_pack_text=GOV_ROUTING_PACK,
            run_id="e2e-merge-gate",
            checkpointer=CapturingCheckpointer(),
        )
        run.receipt_sink = sink
        review = next(n for n in nodes if n.id == "review")
        await dispatch_node(run, nodes, review, run.initial_state, 0)

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
    # The differentiator (step 4) and the reproduction (step 6) both passed.
    assert "outcome MATCHES receipt" in result.stdout
    assert "reproduced result bit-for-bit" in result.stdout


def test_receipt_binds_the_fathom_routed_escalation(emitted: _Emitted) -> None:
    """The signed receipt attests the real Fathom decision: route the merge gate to escalate."""
    payload = json.loads(emitted.request.read_text())
    assert payload["producer_output"]["outcome"] == "goto:escalate"
    assert payload["facts"] == {"tests_passed": False, "reviewers": 1}
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


def test_lied_outcome_voids_at_policy(emitted: _Emitted, tmp_path: Path) -> None:
    """A receipt claiming ``goto:merge`` over escalate facts VOIDs at OVARP's step-4 re-eval."""
    request = json.loads(emitted.request.read_text())
    request["receipt_id"] = "e2e-merge-gate-lie"
    request["producer_output"] = {**request["producer_output"], "outcome": "goto:merge"}
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
    assert result.returncode != 0, "a lied outcome must VOID"
    assert "VOID" in result.stdout
    # OVARP re-derived goto:escalate from the facts and rejected the receipt's goto:merge.
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
