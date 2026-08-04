# SPDX-License-Identifier: Apache-2.0
"""3-way disjoint split, by event AND by time: events sorted by first-CDM epoch,
cut at cumulative fractions -- train strictly precedes admission strictly
precedes deploy on the timeline.

Ported from the upstream collision-avoidance governed-RL pipeline, itself a
port of the fund's holdout discipline (``holdout_levels=2``) re-expressed
over events. The split arithmetic is intentionally IDENTICAL to upstream's --
these cuts define the partitions behind the ppo-v4 admission verdict; only
imports and typing were adapted.

An *event* is a dict with an ``"event_id"`` key and a ``"cdms"`` list whose
entries carry a ``"creation_epoch"`` (seconds); any record shape satisfying
that contract splits identically.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

Event = dict[str, Any]
"""One episode record: ``{"event_id": str, "cdms": [{"creation_epoch": float, ...}, ...], ...}``."""


@dataclass
class Split:
    """Ordered event-id partitions: ``train`` < ``admission`` < ``deploy`` in time."""

    train: list[str]
    admission: list[str]
    deploy: list[str]


def three_way(events: list[Event], fracs: tuple[float, float, float] = (0.6, 0.2, 0.2)) -> Split:
    """Cut the timeline-ordered event list at cumulative ``fracs``."""
    if abs(sum(fracs) - 1.0) > 1e-9:
        raise ValueError(f"fractions must sum to 1, got {fracs}")
    ordered = sorted(events, key=lambda e: e["cdms"][0]["creation_epoch"])
    n = len(ordered)
    cut1 = max(1, round(n * fracs[0]))
    cut2 = max(cut1 + 1, cut1 + round(n * fracs[1]))
    if cut2 >= n:
        raise ValueError(f"not enough events ({n}) for a 3-way split")
    ids: list[str] = [e["event_id"] for e in ordered]
    return Split(train=ids[:cut1], admission=ids[cut1:cut2], deploy=ids[cut2:])


def materialize(events: list[Event], split: Split, outdir: Path) -> None:
    """Write each partition as ``<name>/events.json`` + a sha256-pinned ``split.json``."""
    by_id = {e["event_id"]: e for e in events}
    for name, ids in (
        ("train", split.train),
        ("admission", split.admission),
        ("deploy", split.deploy),
    ):
        part_dir = outdir / name
        part_dir.mkdir(parents=True, exist_ok=True)
        part = [by_id[i] for i in ids]
        blob = json.dumps(part, sort_keys=True)
        (part_dir / "events.json").write_text(blob)
        (part_dir / "split.json").write_text(
            json.dumps(
                {
                    "split": name,
                    "n_events": len(part),
                    "sha256": hashlib.sha256(blob.encode()).hexdigest(),
                }
            )
        )
