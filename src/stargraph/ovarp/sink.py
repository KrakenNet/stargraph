# SPDX-License-Identifier: Apache-2.0
"""OVARP receipt sink -- emit an offline-verifiable receipt per attested tick (ADR-0012).

Wired into a :class:`~stargraph.graph.run.GraphRun` as ``receipt_sink``,
:class:`OvarpReceiptSink` turns each committed governance tick into a signed
OVARP producer-runtime ``replayable`` receipt. Per tick it:

1. projects the authoritative checkpoint into ``producer_output`` (the shared
   :func:`stargraph.ovarp.harness.producer_output_from_checkpoint`);
2. assembles the ``ovarp_replay_bundle`` v1 the reproducer needs (graph IR, the
   tick's ``pre_state``, node id + step, and the Fathom routing pack);
3. stashes the bundle via ``ovarp put`` to get its content-address;
4. emits the receipt via ``ovarp emit`` — OVARP binds ``producer_output`` under
   ``result.digest`` and re-derives the routing outcome offline from the same
   facts (verify step 4).

The offline re-evaluation pack, the facts projection, the authority envelope, and
the signing seeds are all supplied by the caller as an :class:`AttestationSpec` —
the sink owns the *mechanism*, not the governance specifics. Two modes (see
:class:`AttestationSpec`): a **routing** attestation carries a hand-authored offline
pack (``example.merge_gate_attestation_spec``); a **governance** attestation
auto-lowers the graph's Fathom pack via ``ovarp lower-fathom`` and binds the in-stack
Fathom decision (``clearance_gate.clearance_gate_attestation_spec``).

OVARP is invoked as a subprocess: StarGraph gains no build-time dependency on the
verifier, and the sink is opt-in (a run without ``receipt_sink`` never touches it).
The offline verifier is never run here — this is emit-time (network/tooling
allowed); verification stays a separate, offline step.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from stargraph.errors import StargraphRuntimeError
from stargraph.ovarp.harness import (
    factvector_from_state,
    governance_decision,
    materialize_pack_dir,
    producer_output_from_checkpoint,
)

if TYPE_CHECKING:
    from stargraph.checkpoint.protocol import Checkpoint
    from stargraph.graph.run import GraphRun

__all__ = ["AttestationSpec", "OvarpReceiptSink"]


@dataclass(frozen=True)
class AttestationSpec:
    """The per-integration inputs OVARP needs that the runtime cannot itself derive.

    Everything here is stable for a given attested graph: the offline policy pack,
    the authority envelope, the pinned RATS reference values, and the signing seeds.
    The sink combines it with the per-tick ``pre_state``/node/step to build a receipt.

    Two modes, discriminated by ``ovarp_pack``:

    * **routing** (``ovarp_pack`` set) — the offline evaluator is a hand-authored
      OVARP v0 pack (``ovarp_pack`` + ``facts_fields``); the attested outcome is the
      routing decision (``goto:<node>``). See ``example.merge_gate_attestation_spec``.
    * **governance** (``ovarp_pack is None``) — the offline evaluator is auto-derived
      by lowering ``fathom_pack`` (``ovarp lower-fathom``); facts are the FactVector
      projected from the Mirror state; the attested outcome is the in-stack Fathom
      decision (``allow``/``deny``/…). No hand-authored offline pack. See
      ``clearance_gate.clearance_gate_attestation_spec``.
    """

    runtime: str
    """Runtime tag bound in the receipt + bundle (e.g. ``"stargraph@0.3"``)."""
    ir_dict: dict[str, Any]
    """Graph IR (its ``state_class`` resolves the Mirror state model)."""
    fathom_pack: str
    """Fathom pack YAML text — the in-stack evaluator, carried in the bundle. Routing
    rules (routing mode) or governance ``action`` rules (governance mode; also the
    ``lower-fathom`` source)."""
    policy_id: str
    """Receipt ``policy.id`` (names the governance rule)."""
    authority: dict[str, Any]
    """Receipt ``authority`` grant envelope."""
    verb: str
    resource: str
    context: dict[str, Any]
    seeds: dict[str, str]
    """Pinned 32-byte hex signing seeds (``agent``/``attester``/``beacon``)."""
    nonce: str
    """Per-action nonce (hex); a ``replayable`` receipt requires it."""
    issued_at: str
    """Receipt ``issued_at`` (fixed for a reproducible tick; wall-clock-free)."""
    model_digest: str
    """Pinned RATS model reference value (``sha256:...``)."""
    decoding_digest: str
    """Pinned RATS decoding reference value (``sha256:...``)."""
    ovarp_pack: dict[str, Any] | None = None
    """Routing mode: the OFFLINE OVARP v0 pack. ``None`` selects governance mode
    (the offline pack is auto-lowered from ``fathom_pack``)."""
    facts_fields: tuple[str, ...] = ()
    """Routing mode only: state keys projected from ``pre_state`` into the OVARP
    ``facts`` object. Ignored in governance mode (facts are the Mirror FactVector)."""
    pack_name: str = ""
    """Governance mode only: stable lowered-pack id (the materialized pack-dir leaf),
    so the auto-lowered IR is byte-stable across emits (reproducible receipt)."""
    round: int = 1
    """Beacon round bound by the replayable seed."""


def _safe(token: str) -> str:
    """Filesystem-safe slug for a run id used in per-tick artifact filenames."""
    return "".join(ch if ch.isalnum() or ch in "-." else "_" for ch in token) or "run"


class OvarpReceiptSink:
    """Emit one OVARP producer-runtime receipt per committed tick via the ``ovarp`` binary."""

    def __init__(
        self,
        spec: AttestationSpec,
        *,
        store_dir: str | Path,
        out_dir: str | Path,
        ovarp_bin: str | Path = "ovarp",
    ) -> None:
        self.spec = spec
        self.store_dir = str(store_dir)
        self.out_dir = Path(out_dir)
        self.ovarp_bin = str(ovarp_bin)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        #: Receipt paths written so far, in emission order (inspection / tests).
        self.receipts: list[Path] = []
        #: Memoized auto-lowered governance pack. The lowered IR is a pure function of
        #: the (frozen) spec, so it is computed once on the first governance tick and
        #: reused — no per-tick tempdir / file writes / ``lower-fathom`` subprocess.
        self._lowered_pack: dict[str, Any] | None = None

    async def record(
        self,
        run: GraphRun,
        pre_state: Any,
        checkpoint: Checkpoint,
    ) -> Path:
        """Emit a receipt for one committed tick; return the written receipt path.

        The tick's node id and step are read from the authoritative
        ``checkpoint`` (``last_node``/``step``) rather than passed in, so the
        receipt can never bind a node/step that disagrees with what was committed.
        """
        spec = self.spec
        node_id = checkpoint.last_node
        step = checkpoint.step
        pre_state_dict: dict[str, Any] = pre_state.model_dump(mode="json")

        if spec.ovarp_pack is None:
            pack, facts, producer_output = await self._governance_receipt_inputs(run, checkpoint)
        else:
            missing = [k for k in spec.facts_fields if k not in pre_state_dict]
            if missing:
                raise StargraphRuntimeError(
                    f"ovarp sink: state is missing fact field(s) {missing} for node {node_id!r}"
                )
            pack = spec.ovarp_pack
            facts = {k: pre_state_dict[k] for k in spec.facts_fields}
            producer_output = producer_output_from_checkpoint(checkpoint)

        bundle = {
            "ovarp_replay_bundle": "v1",
            "runtime": spec.runtime,
            "governance": spec.ovarp_pack is None,
            "producer": {
                "graph": spec.ir_dict,
                "pre_state": pre_state_dict,
                "node_id": node_id,
                "step": step,
                "fathom_pack": spec.fathom_pack,
            },
        }

        slug = f"{_safe(run.run_id)}-s{step}"
        bundle_path = self.out_dir / f"{slug}.bundle.json"
        request_path = self.out_dir / f"{slug}.request.json"
        receipt_path = self.out_dir / f"{slug}.receipt.json"
        bundle_path.write_text(json.dumps(bundle, indent=2), encoding="utf-8")

        bundle_digest = (
            await self._ovarp("put", str(bundle_path), "--store", self.store_dir)
        ).strip()

        request = {
            "receipt_id": slug,
            "seeds": spec.seeds,
            "authority": spec.authority,
            "verb": spec.verb,
            "resource": spec.resource,
            "context": spec.context,
            "issued_at": spec.issued_at,
            "grade": "replayable",
            "policy_id": spec.policy_id,
            "pack": pack,
            "facts": facts,
            "round": spec.round,
            "nonce": spec.nonce,
            "replay_trace": {
                "runtime": spec.runtime,
                "bundle": bundle_digest,
                "model_digest": spec.model_digest,
                "decoding_digest": spec.decoding_digest,
            },
            "producer_output": producer_output,
        }
        request_path.write_text(json.dumps(request, indent=2), encoding="utf-8")

        await self._ovarp(
            "emit", str(request_path), "--store", self.store_dir, "--out", str(receipt_path)
        )
        self.receipts.append(receipt_path)
        return receipt_path

    async def _governance_receipt_inputs(
        self, run: GraphRun, checkpoint: Checkpoint
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        """Build (pack, facts, producer_output) for a governance-mode tick.

        The offline pack is auto-lowered from the Fathom governance pack (no
        hand-authoring); the facts are the Mirror FactVector CLIPS asserted; the
        outcome is the in-stack Fathom decision. OVARP re-derives that decision offline
        (verify step 4) from the lowered pack + FactVector, so a lied decision VOIDs.
        """
        spec = self.spec
        if not spec.pack_name:
            raise StargraphRuntimeError(
                "ovarp sink: governance AttestationSpec requires a pack_name "
                "(the stable lowered-pack id)"
            )
        decision = governance_decision(run)
        facts = factvector_from_state(run, checkpoint.state)
        pack = await self._lower_pack(run.graph.state_schema, spec.fathom_pack, spec.pack_name)
        producer_output = producer_output_from_checkpoint(checkpoint, outcome=decision)
        return pack, facts, producer_output

    async def _lower_pack(
        self, state_cls: type[Any], rules_text: str, pack_name: str
    ) -> dict[str, Any]:
        """Auto-derive (and memoize) the offline OVARP pack by lowering the Fathom pack.

        Materializes a ``lower-fathom`` pack dir (Mirror templates + module + rules)
        and shells ``ovarp lower-fathom`` **once** — the lowered IR is invariant for
        this sink's (frozen) spec, so it is cached on :attr:`_lowered_pack` and reused
        by every later tick. The lowered IR is the receipt's offline evaluator — no
        hand-authored pack. Fail-closed: ``lower-fathom`` exits non-zero for any
        out-of-profile rule, surfacing as a StargraphRuntimeError.
        """
        if self._lowered_pack is None:
            with tempfile.TemporaryDirectory() as td:
                pack_dir = materialize_pack_dir(state_cls, rules_text, Path(td), pack_name)
                out = await self._ovarp("lower-fathom", str(pack_dir))
            self._lowered_pack = cast("dict[str, Any]", json.loads(out))
        return self._lowered_pack

    async def _ovarp(self, *args: str) -> str:
        """Run ``ovarp <args>`` offline-friendly; return stdout, raise on non-zero exit."""
        proc = await asyncio.create_subprocess_exec(
            self.ovarp_bin,
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, err = await proc.communicate()
        if proc.returncode != 0:
            detail = err.decode(errors="replace").strip()
            msg = f"ovarp {args[0]} failed (exit {proc.returncode}): {detail}"
            raise StargraphRuntimeError(msg)
        return out.decode()
