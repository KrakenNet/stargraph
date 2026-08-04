# SPDX-License-Identifier: Apache-2.0
"""Wall / train / gate / shield stations for the reference RL eval graph.

Each station is a zero-arg-constructible :class:`~stargraph.nodes.base.NodeBase`
(the CLI builds ``module:Class`` node kinds zero-arg), reading its pluggable
pieces as dotted references from :class:`~stargraph.rl.gauntlet.eval_state.EvalState`
and writing a Mirror-annotated ``*_verdict`` the graph's Fathom rules route on.

Posture (ported from the upstream collision-avoidance governed-RL pipeline):

* **default = refusal, fail-closed** -- an exception while *evaluating*
  becomes a ``refuse``/``refused`` verdict with the error in the reasons
  (the upstream shield's stance), never a silent pass. The upstream admission
  script *raises* on a split-binding mismatch; as a graph station that
  condition is a first-class ``refused`` verdict instead -- same fail-closed
  outcome, graph-shaped. That is the only behavioral adaptation.
* **misconfiguration is loud** -- an unresolvable dotted reference raises
  :class:`~stargraph.errors.RLNodeError` (FR-6 force-loud), it does not
  masquerade as a refusal.

:class:`GateStation` is the port of the upstream admission run: re-derive the
deterministic 3-way split, bind the candidate's recorded train-split sha to
the gate's OWN re-derived partition (never trainer-authored files), then gate
Pareto + CSCV-PBO via :func:`stargraph.rl.gauntlet.admission.gate`.
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib
import itertools
import json
from functools import lru_cache
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

from stargraph.errors import RLNodeError
from stargraph.nodes.base import ExecutionContext, NodeBase
from stargraph.rl.gauntlet import admission, splits

if TYPE_CHECKING:
    from pydantic import BaseModel

    from stargraph.rl.gauntlet.splits import Event

__all__ = ["GateStation", "ShieldStation", "TrainStation", "WallStation"]


# --------------------------------------------------------------------------- #
# Dotted-reference plumbing                                                   #
# --------------------------------------------------------------------------- #


def resolve_ref(ref: str) -> Any:
    """Resolve ``"pkg.mod:attr[.attr...]"`` (or a bare ``"pkg.mod"``) to an object.

    The same convention the IR uses for ``state_class`` / ``module:Class``
    node kinds. A bare module path (no colon) returns the module itself --
    that is how module-shaped backends (``IMPL_ID`` + functions) are named.
    """
    module_path, _, attr_path = ref.partition(":")
    if not module_path:
        raise RLNodeError(
            f"empty module path in dotted reference {ref!r}",
            hint="use 'pkg.module:attr' or a bare 'pkg.module'",
        )
    try:
        obj: Any = importlib.import_module(module_path)
    except ImportError as exc:
        raise RLNodeError(
            f"cannot import module {module_path!r} for reference {ref!r}: {exc}",
            hint="is the referenced distribution installed in this environment?",
        ) from exc
    for part in [p for p in attr_path.split(".") if p]:
        try:
            obj = getattr(obj, part)
        except AttributeError as exc:
            raise RLNodeError(
                f"reference {ref!r}: {obj!r} has no attribute {part!r}",
            ) from exc
    return obj


@lru_cache(maxsize=8)
def _load_events(events_loader: str, dataset_path: str) -> list[Event]:
    """Load (and cache) the event list -- every station shares one load."""
    loader = resolve_ref(events_loader)
    events: list[Event] = loader(Path(dataset_path))
    return events


def _load_cfg(expert_cfg_loader: str) -> dict[str, Any]:
    cfg: dict[str, Any] = resolve_ref(expert_cfg_loader)()
    return cfg


def _s(state: BaseModel, name: str) -> str:
    value: Any = getattr(state, name)
    return str(value)


def _i(state: BaseModel, name: str) -> int:
    value: Any = getattr(state, name)
    return int(value)


def _f(state: BaseModel, name: str) -> float:
    value: Any = getattr(state, name)
    return float(value)


def _meta_path(model_dir: str) -> Path:
    return Path(model_dir) / "policy_meta.json"


def _canonical_train_sha(events: list[Event], split: splits.Split) -> str:
    """The gate's own re-derived train-partition sha (``run_admission`` verbatim)."""
    by_id = {e["event_id"]: e for e in events}
    return hashlib.sha256(
        json.dumps([by_id[i] for i in split.train], sort_keys=True).encode()
    ).hexdigest()


# --------------------------------------------------------------------------- #
# Stations                                                                    #
# --------------------------------------------------------------------------- #


class WallStation(NodeBase):
    """Data wall: structural admission of the event dataset (refuse > repair).

    Checks: non-empty dataset; every event carries ``event_id`` and a
    non-empty ``cdms`` list; every CDM carries ``creation_epoch`` / ``tca``;
    CDM epochs are non-decreasing within an event. Anything deeper
    (covariance quality, source lineage) belongs to a domain wall upstream --
    this station only refuses, it never repairs (the upstream wall stance).
    """

    async def execute(self, state: BaseModel, ctx: ExecutionContext) -> dict[str, Any]:
        del ctx
        loader = _s(state, "events_loader")
        dataset = _s(state, "dataset_path")
        try:
            events = _load_events(loader, dataset)
        except RLNodeError:
            raise
        except Exception as exc:  # fail-closed: unloadable data refuses
            return {
                "wall_verdict": "refuse",
                "wall_reasons": [f"fail-closed: {type(exc).__name__}: {exc}"],
            }
        reasons: list[str] = []
        if not events:
            reasons.append("wall: dataset is empty")
        bad = 0
        for event in events:
            raw_cdms: Any = event.get("cdms")
            cdms: list[Any] = cast("list[Any]", raw_cdms) if isinstance(raw_cdms, list) else []
            ok = bool(event.get("event_id")) and bool(cdms)
            if ok:
                epochs: list[Any] = [c.get("creation_epoch") for c in cdms]
                ok = (
                    all(e is not None for e in epochs)
                    and all(c.get("tca") is not None for c in cdms)
                    and all(a <= b for a, b in itertools.pairwise(epochs))
                )
            if not ok:
                bad += 1
                if bad <= 5:
                    reasons.append(f"wall: malformed event {event.get('event_id')!r}")
        if bad > 5:
            reasons.append(f"wall: {bad - 5} further malformed events")
        return {
            "wall_verdict": "refuse" if reasons else "pass",
            "wall_reasons": reasons,
            "n_events": len(events),
        }


class TrainStation(NodeBase):
    """Trainer door: ensure a candidate exists at ``model_dir``, train-split only.

    ``policy_meta.json`` already present -> ``cached`` (nothing runs). Else the
    dotted ``trainer`` callable gets ONLY the re-derived train partition
    (the upstream isolation posture: the trainer never sees admission/deploy events)
    and must leave ``policy_meta.json`` behind. No cached candidate and no
    trainer -> ``refuse``.
    """

    async def execute(self, state: BaseModel, ctx: ExecutionContext) -> dict[str, Any]:
        del ctx
        model_dir = _s(state, "model_dir")
        if model_dir and _meta_path(model_dir).exists():
            return {"train_verdict": "cached", "train_reasons": []}
        trainer_ref = _s(state, "trainer")
        if not trainer_ref:
            return {
                "train_verdict": "refuse",
                "train_reasons": ["train: no cached candidate and no trainer configured"],
            }
        trainer = resolve_ref(trainer_ref)
        events = _load_events(_s(state, "events_loader"), _s(state, "dataset_path"))
        cfg = _load_cfg(_s(state, "expert_cfg_loader"))
        split = splits.three_way(events)
        by_id = {e["event_id"]: e for e in events}
        train_events = [by_id[i] for i in split.train]
        timesteps = _i(state, "train_timesteps")
        try:
            await asyncio.to_thread(trainer, train_events, cfg, Path(model_dir), timesteps)
        except Exception as exc:  # fail-closed: a failed training run refuses
            return {
                "train_verdict": "refuse",
                "train_reasons": [f"fail-closed: {type(exc).__name__}: {exc}"],
            }
        if not _meta_path(model_dir).exists():
            return {
                "train_verdict": "refuse",
                "train_reasons": ["train: trainer left no policy_meta.json behind"],
            }
        return {"train_verdict": "trained", "train_reasons": []}


class GateStation(NodeBase):
    """The admission gate station -- ``run_admission.run`` as a graph node.

    Re-derives the deterministic 3-way split from the dataset, binds the
    candidate's recorded ``train_split_sha`` to the gate's OWN re-derived
    partition (both meta and any materialized splits come from the trainer;
    comparing them to each other proves nothing), then gates Pareto vs the
    baseline + CSCV-PBO over the config family on the evaluator backend.
    """

    async def execute(self, state: BaseModel, ctx: ExecutionContext) -> dict[str, Any]:
        del ctx
        events = _load_events(_s(state, "events_loader"), _s(state, "dataset_path"))
        cfg = _load_cfg(_s(state, "expert_cfg_loader"))
        split = splits.three_way(events)
        by_id = {e["event_id"]: e for e in events}

        model_dir = _s(state, "model_dir")
        meta: dict[str, Any] | None = None
        candidate_id = ""
        if model_dir and _meta_path(model_dir).exists():
            meta = cast("dict[str, Any]", json.loads(_meta_path(model_dir).read_text()))
            candidate_id = str(meta.get("policy_id", ""))
            if meta.get("train_split_sha") != _canonical_train_sha(events, split):
                return {
                    "gate_verdict": "refused",
                    "gate_reasons": [
                        "split-binding: candidate's recorded train split does not "
                        "match the gate's re-derived split"
                    ],
                    "candidate_id": candidate_id,
                }

        admission_events = [by_id[i] for i in split.admission]
        max_events = _i(state, "max_admission_events")
        if max_events:
            admission_events = admission_events[:max_events]

        candidate: Any = resolve_ref(_s(state, "candidate_loader"))(Path(model_dir))
        baseline: Any = resolve_ref(_s(state, "baseline_factory"))(cfg)
        family_ref = _s(state, "config_family_factory")
        family: list[Any] | None = (
            list(resolve_ref(family_ref)(candidate, cfg)) if family_ref else None
        )
        backend: Any = resolve_ref(_s(state, "gate_backend"))
        env_factory: Any = resolve_ref(_s(state, "env_factory"))
        if not candidate_id:
            candidate_id = str(getattr(candidate, "policy_id", ""))

        try:
            verdict = await asyncio.to_thread(
                admission.gate,
                candidate,
                admission_events,
                baseline,
                cfg,
                family,
                backend=backend,
                env_factory=env_factory,
            )
        except Exception as exc:  # fail-closed: a judge that errors refuses
            return {
                "gate_verdict": "refused",
                "gate_reasons": [f"fail-closed: {type(exc).__name__}: {exc}"],
                "candidate_id": candidate_id,
                "n_admission_events": len(admission_events),
            }
        return {
            "gate_verdict": "admitted" if verdict.admitted else "refused",
            "gate_reasons": list(verdict.reasons),
            "gate_metrics": dict(verdict.metrics),
            "candidate_id": candidate_id,
            "n_admission_events": len(admission_events),
        }


class ShieldStation(NodeBase):
    """Assurance-shield station: wrap an upstream-style rule evaluator, deterministically.

    The dotted ``shield`` reference names a factory
    ``factory(expert_cfg, backend) -> evaluator`` whose
    ``evaluate(recommendation, ctx)`` returns an object with ``approved`` /
    ``reasons`` / ``facts`` (the upstream ``ShieldVerdict`` dataclass shape). The
    recommendation under review comes from state
    (``shield_dv_ms`` / ``shield_direction`` on event
    ``shield_event_index``). The shield only ever emits a verdict --
    it commands nothing (the upstream shield posture).
    """

    async def execute(self, state: BaseModel, ctx: ExecutionContext) -> dict[str, Any]:
        del ctx
        cfg = _load_cfg(_s(state, "expert_cfg_loader"))
        backend: Any = resolve_ref(_s(state, "gate_backend"))
        evaluator: Any = resolve_ref(_s(state, "shield"))(cfg, backend)
        events = _load_events(_s(state, "events_loader"), _s(state, "dataset_path"))
        model_dir = _s(state, "model_dir")
        meta: dict[str, Any] | None = None
        if model_dir and _meta_path(model_dir).exists():
            meta = cast("dict[str, Any]", json.loads(_meta_path(model_dir).read_text()))
        rec = SimpleNamespace(
            dv_ms=_f(state, "shield_dv_ms"), direction=_i(state, "shield_direction")
        )
        shield_ctx: dict[str, Any] = {
            "policy_meta": meta,
            "event": events[_i(state, "shield_event_index")],
            "decision_cdm_seq": _i(state, "shield_decision_cdm_seq"),
        }
        try:
            verdict: Any = await asyncio.to_thread(evaluator.evaluate, rec, shield_ctx)
        except Exception as exc:  # fail-closed (mirrors the upstream shield itself)
            return {
                "shield_verdict": "refused",
                "shield_reasons": [f"fail-closed: {type(exc).__name__}: {exc}"],
                "shield_facts": {"fail_closed": True},
            }
        return {
            "shield_verdict": "approved" if verdict.approved else "refused",
            "shield_reasons": list(verdict.reasons),
            "shield_facts": dict(verdict.facts),
        }
