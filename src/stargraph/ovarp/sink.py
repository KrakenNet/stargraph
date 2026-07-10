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
the signing seeds are all supplied by the caller as an :class:`AttestationSpec`
(see :func:`stargraph.ovarp.example.merge_gate_attestation_spec`) — the sink owns
the *mechanism*, not the governance specifics.

OVARP is invoked as a subprocess: StarGraph gains no build-time dependency on the
verifier, and the sink is opt-in (a run without ``receipt_sink`` never touches it).
The offline verifier is never run here — this is emit-time (network/tooling
allowed); verification stays a separate, offline step.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from stargraph.errors import StargraphRuntimeError
from stargraph.ovarp.harness import producer_output_from_checkpoint

if TYPE_CHECKING:
    from stargraph.checkpoint.protocol import Checkpoint
    from stargraph.graph.run import GraphRun

__all__ = ["AttestationSpec", "OvarpReceiptSink"]


@dataclass(frozen=True)
class AttestationSpec:
    """The per-integration inputs OVARP needs that the runtime cannot itself derive.

    Everything here is stable for a given attested graph: the offline policy
    re-implementation (``ovarp_pack`` + ``facts_fields``), the authority envelope,
    the pinned RATS reference values, and the signing seeds. The sink combines it
    with the per-tick ``pre_state``/node/step to build a receipt.
    """

    runtime: str
    """Runtime tag bound in the receipt + bundle (e.g. ``"stargraph@0.3"``)."""
    ir_dict: dict[str, Any]
    """Graph IR (its ``state_class`` resolves the Mirror state model)."""
    fathom_pack: str
    """Fathom routing pack YAML text — the in-stack evaluator, carried in the bundle."""
    policy_id: str
    """Receipt ``policy.id`` (names the governance rule)."""
    ovarp_pack: dict[str, Any]
    """OVARP v0 policy pack: the OFFLINE re-implementation of the routing predicate."""
    authority: dict[str, Any]
    """Receipt ``authority`` grant envelope."""
    verb: str
    resource: str
    context: dict[str, Any]
    facts_fields: tuple[str, ...]
    """State keys projected from ``pre_state`` into the OVARP ``facts`` vector."""
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
        producer_output = producer_output_from_checkpoint(checkpoint)

        missing = [k for k in spec.facts_fields if k not in pre_state_dict]
        if missing:
            raise StargraphRuntimeError(
                f"ovarp sink: state is missing fact field(s) {missing} for node {node_id!r}"
            )
        facts = {k: pre_state_dict[k] for k in spec.facts_fields}

        bundle = {
            "ovarp_replay_bundle": "v1",
            "runtime": spec.runtime,
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
            "pack": spec.ovarp_pack,
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
