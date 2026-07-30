# SPDX-License-Identifier: Apache-2.0
"""Run state for the reference RL eval graph (``eval-graph.yaml``).

All pluggable pieces enter as **dotted references** (``"pkg.mod:attr"``, or a
bare ``"pkg.mod"`` for module-shaped backends) so the graph stays IR-portable
and the stations stay zero-arg constructible (``module:Class`` node kinds are
built zero-arg by the CLI). The Mirror-annotated ``*_verdict`` fields are what
the graph's Fathom rules route on -- e.g.
``(gate_verdict (value "refused"))`` -- so transitions are decided by rules
over facts, never by static edges.
"""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import BaseModel, Field

from stargraph.ir import Mirror

__all__ = ["EvalState"]


class EvalState(BaseModel):
    """State threaded through wall -> train -> gate -> shield."""

    # -- inputs: data + config (dotted refs) ---------------------------
    dataset_path: str = ""
    model_dir: str = ""
    events_loader: str = ""  # callable(Path) -> list[Event]
    expert_cfg_loader: str = ""  # callable() -> dict
    env_factory: str = ""  # callable(events, cfg, backend) -> EpisodeEnv
    gate_backend: str = ""  # module-shaped Backend (has IMPL_ID)
    candidate_loader: str = ""  # callable(Path model_dir) -> EventPolicy
    baseline_factory: str = ""  # callable(cfg) -> EventPolicy
    config_family_factory: str = ""  # callable(candidate, cfg) -> list[EventPolicy]; "" = no PBO
    trainer: str = ""  # callable(train_events, cfg, Path model_dir, int steps); "" = cached only
    shield: str = ""  # callable(cfg, backend) -> evaluator with .evaluate(rec, ctx)

    # -- knobs ---------------------------------------------------------
    train_timesteps: int = 0
    max_admission_events: int = 0  # 0 = the full admission split

    # -- shield recommendation (read only on the admitted path) --------
    shield_event_index: int = 0
    shield_decision_cdm_seq: int = 1
    shield_dv_ms: float = 0.0
    shield_direction: int = 1

    # -- mirrored verdicts (the rules route on these) ------------------
    wall_verdict: Annotated[str, Mirror(lifecycle="step")] = "pending"
    train_verdict: Annotated[str, Mirror(lifecycle="step")] = "pending"
    gate_verdict: Annotated[str, Mirror(lifecycle="step")] = "pending"
    shield_verdict: Annotated[str, Mirror(lifecycle="step")] = "pending"

    # -- outputs -------------------------------------------------------
    wall_reasons: list[str] = Field(default_factory=list)
    train_reasons: list[str] = Field(default_factory=list)
    gate_reasons: list[str] = Field(default_factory=list)
    shield_reasons: list[str] = Field(default_factory=list)
    gate_metrics: dict[str, Any] = Field(default_factory=dict)
    shield_facts: dict[str, Any] = Field(default_factory=dict)
    candidate_id: str = ""
    n_events: int = 0
    n_admission_events: int = 0

    # ``halt``-kind terminals resolve to EchoNode, which copies ``message``.
    message: str = ""
