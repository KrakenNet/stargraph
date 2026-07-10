# SPDX-License-Identifier: Apache-2.0
"""OVARP integration -- attest StarGraph governance ticks as offline-verifiable receipts.

Implements the StarGraph side of the OVARP producer-runtime replay contract
(ADR-0012): a Fathom-routed tick is emitted as a signed ``replayable`` receipt
whose routing decision OVARP re-derives offline, and which ``ovarp verify
--replay`` reproduces bit-for-bit by driving ``stargraph ovarp-reproduce``.

* :mod:`~stargraph.ovarp.harness` -- the shared build/reproduce/project path used
  by both the emit side and the reproducer (so a re-run reconstructs an identical
  tick).
* :mod:`~stargraph.ovarp.sink` -- :class:`~stargraph.ovarp.sink.OvarpReceiptSink`,
  wired as a run's ``receipt_sink`` to emit a receipt per committed tick.
* :mod:`~stargraph.ovarp.example` -- the worked merge-gate governance scenario the
  cross-repo end-to-end test attests.
"""

from __future__ import annotations

from stargraph.ovarp.harness import (
    build_attestable_run,
    producer_output_from_checkpoint,
    reproduce_from_bundle,
)
from stargraph.ovarp.sink import AttestationSpec, OvarpReceiptSink

__all__ = [
    "AttestationSpec",
    "OvarpReceiptSink",
    "build_attestable_run",
    "producer_output_from_checkpoint",
    "reproduce_from_bundle",
]
