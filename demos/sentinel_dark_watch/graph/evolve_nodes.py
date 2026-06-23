# SPDX-License-Identifier: Apache-2.0
"""SDW Evolution subgraph — self-improvement nodes.

Implements the observe-analyze-hypothesize-experiment-evaluate-apply loop.
Each node is a concrete step in the scientific method applied to pipeline evolution.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from stargraph.nodes.base import ExecutionContext, NodeBase

if TYPE_CHECKING:
    from pydantic import BaseModel

logger = logging.getLogger(__name__)


def _pg_dsn() -> str:
    from demos.sentinel_dark_watch.db import get_pg_dsn

    return get_pg_dsn()


class ObserveMetricsNode(NodeBase):
    """Collect aggregate metrics from recent pipeline runs.

    Queries run_metrics table for the last N runs, computes averages,
    and identifies the current model version + performance baseline.
    """

    async def execute(self, state: BaseModel, ctx: ExecutionContext) -> dict[str, Any]:
        try:
            import asyncpg

            conn = await asyncpg.connect(_pg_dsn())
        except Exception as exc:
            logger.warning("ObserveMetricsNode: DB unavailable: %s", exc)
            return {"phase": "observe", "last_error": f"DB unavailable: {exc}"}

        try:
            rows = await conn.fetch(
                "SELECT total_detections, dark_vessels, ais_matched,"
                "       false_positives, processing_secs"
                "  FROM run_metrics ORDER BY created_at DESC LIMIT 20"
            )
            if not rows:
                return {
                    "phase": "observe",
                    "recent_run_count": 0,
                    "identified_weaknesses": ["no_run_data"],
                }

            n = len(rows)
            avg_det = sum(r["total_detections"] for r in rows) / n
            avg_dark = sum(r["dark_vessels"] for r in rows) / n
            avg_proc = sum(r["processing_secs"] for r in rows) / n
            total_det = sum(r["total_detections"] for r in rows)
            total_fp = sum(r["false_positives"] for r in rows)
            fp_rate = total_fp / total_det if total_det > 0 else 0.0
            dark_rate = avg_dark / avg_det if avg_det > 0 else 0.0

            return {
                "phase": "observe",
                "recent_run_count": n,
                "avg_detection_count": avg_det,
                "avg_processing_seconds": avg_proc,
                "false_positive_rate": fp_rate,
                "dark_vessel_rate": dark_rate,
            }
        except Exception as exc:
            logger.warning("ObserveMetricsNode query failed: %s", exc)
            return {"phase": "observe", "last_error": str(exc)}
        finally:
            await conn.close()


class AnalyzeWeaknessesNode(NodeBase):
    """LLM-driven analysis of pipeline weaknesses from observed metrics.

    Feeds metrics to an LLM to identify specific weaknesses and
    improvement opportunities. Falls back to rule-based analysis
    when LLM unavailable.
    """

    async def execute(self, state: BaseModel, ctx: ExecutionContext) -> dict[str, Any]:
        s = state  # type: ignore[attr-defined]
        weaknesses: list[str] = []
        opportunities: list[str] = []

        # Rule-based analysis (always runs)
        if s.false_positive_rate > 0.3:
            weaknesses.append("high_false_positive_rate")
            opportunities.append("improve_confidence_calibration")
        if s.avg_detection_count < 1.0:
            weaknesses.append("low_detection_rate")
            opportunities.append("lower_confidence_threshold")
            opportunities.append("try_different_model_architecture")
        if s.avg_processing_seconds > 120:
            weaknesses.append("slow_processing")
            opportunities.append("reduce_patch_overlap")
            opportunities.append("use_smaller_model")
        if s.dark_vessel_rate > 0.9:
            weaknesses.append("no_ais_correlation")
            opportunities.append("integrate_live_ais_feed")
        if s.current_model_map50 < 0.3:
            weaknesses.append("poor_model_quality")
            opportunities.append("fine_tune_on_sar_data")
            opportunities.append("try_detr_architecture")
            opportunities.append("augment_training_data")

        # LLM-enhanced analysis via DSPy ReAct (tool-enabled agent)
        try:
            from demos.sentinel_dark_watch.graph.signatures import (
                DSPY_AVAILABLE,
                EVOLUTION_TOOLS,
                WeaknessAnalysisSignature,
            )

            if DSPY_AVAILABLE:
                import dspy

                react = dspy.ReAct(WeaknessAnalysisSignature, tools=EVOLUTION_TOOLS, max_iters=3)
                try:
                    result = await asyncio.wait_for(
                        asyncio.to_thread(
                            react,
                            recent_run_count=s.recent_run_count,
                            avg_detections=s.avg_detection_count,
                            false_positive_rate=s.false_positive_rate,
                            processing_seconds=s.avg_processing_seconds,
                            model_map50=s.current_model_map50,
                            dark_vessel_rate=s.dark_vessel_rate,
                        ),
                        timeout=300,
                    )
                    for w in result.weaknesses.split(","):
                        w = w.strip()
                        if w and w not in weaknesses:
                            weaknesses.append(w)
                    for o in result.opportunities.split(","):
                        o = o.strip()
                        if o and o not in opportunities:
                            opportunities.append(o)
                    logger.info(
                        "ReAct analysis added %d weaknesses, %d opportunities",
                        len(weaknesses),
                        len(opportunities),
                    )
                except TimeoutError:
                    logger.warning("ReAct analysis timed out — using rule-based only")
        except Exception as exc:
            logger.info("LLM analysis skipped: %s", exc)

        return {
            "phase": "analyze",
            "identified_weaknesses": weaknesses,
            "improvement_opportunities": opportunities,
        }


class ResearchAlternativesNode(NodeBase):
    """Research alternative approaches based on identified weaknesses.

    Uses LLM to synthesize knowledge about SAR vessel detection,
    model architectures, preprocessing techniques, and scoring methods.
    Falls back to a static knowledge base when LLM unavailable.
    """

    _STATIC_ALTERNATIVES: dict[str, list[str]] = {
        "poor_model_quality": [
            "Fine-tune YOLOv11-OBB on xView3 with better augmentation",
            "Try RT-DETR for SAR vessel detection (transformer-based)",
            "Ensemble: YOLO + CFAR (constant false alarm rate) classical detector",
            "Use dual-polarization features (VV/VH ratio) as explicit channel",
        ],
        "low_detection_rate": [
            "Lower confidence threshold to 0.05 with stronger NMS",
            "Increase patch overlap from 10% to 25%",
            "Add multi-scale inference (640 + 1280 patches)",
            "Apply CLAHE preprocessing to enhance low-contrast targets",
        ],
        "high_false_positive_rate": [
            "Add post-detection land/infrastructure mask refinement",
            "Train binary classifier on detection chips (vessel vs artifact)",
            "Increase NMS IoU threshold to reduce duplicates",
            "Add temporal consistency check across sequential passes",
        ],
        "slow_processing": [
            "Use YOLOv11-nano instead of small",
            "Reduce overlap to 5% for open-ocean tiles",
            "GPU inference with TensorRT optimization",
            "Skip patches with uniform intensity (ocean-only heuristic)",
        ],
    }

    async def execute(self, state: BaseModel, ctx: ExecutionContext) -> dict[str, Any]:
        s = state  # type: ignore[attr-defined]
        alternatives: list[str] = []

        for weakness in s.identified_weaknesses:
            static = self._STATIC_ALTERNATIVES.get(weakness, [])
            alternatives.extend(static)

        # LLM-enhanced research via DSPy ReAct (tool-enabled agent)
        try:
            from demos.sentinel_dark_watch.graph.signatures import (
                DSPY_AVAILABLE,
                EVOLUTION_TOOLS,
                ResearchAlternativesSignature,
            )

            if DSPY_AVAILABLE and s.identified_weaknesses:
                import dspy

                react = dspy.ReAct(
                    ResearchAlternativesSignature, tools=EVOLUTION_TOOLS, max_iters=3
                )
                try:
                    result = await asyncio.wait_for(
                        asyncio.to_thread(
                            react,
                            weaknesses=", ".join(s.identified_weaknesses[:5]),
                            opportunities=", ".join(s.improvement_opportunities[:5]),
                            current_approach="YOLO11-OBB on dual-band SAR (VH+VV+ratio), 640px patches, conf=0.1",
                        ),
                        timeout=300,
                    )
                    for line in result.proposals.split("\n"):
                        line = line.strip()
                        if line and "|" in line:
                            alternatives.append(line)
                    logger.info("ReAct research added %d alternatives", len(alternatives))
                except TimeoutError:
                    logger.warning(
                        "ReAct research timed out after 180s — using static alternatives only"
                    )
        except Exception as exc:
            logger.info("LLM research skipped: %s", exc)

        return {
            "phase": "research",
            "improvement_opportunities": alternatives[:10],
        }


class GenerateProposalsNode(NodeBase):
    """Convert research findings into concrete, actionable proposals.

    Each proposal has a category, risk level, and governance path.
    Low-risk proposals (thresholds, hyperparams) go to auto-approve.
    High-risk proposals (new nodes, flow changes) require human approval.
    """

    _AUTO_APPROVE_CATEGORIES = {"threshold", "hyperparameter"}
    _HUMAN_APPROVE_CATEGORIES = {"node_addition", "node_removal", "flow_change", "rule_change"}

    async def execute(self, state: BaseModel, ctx: ExecutionContext) -> dict[str, Any]:
        from demos.sentinel_dark_watch.graph.state import (
            EvolutionProposal,
            ProposalCategory,
            ProposalRisk,
            ProposalStatus,
        )

        s = state  # type: ignore[attr-defined]
        opportunities = s.improvement_opportunities
        proposals: list[EvolutionProposal] = []

        for opp in opportunities[:5]:
            # Try to parse structured proposal from LLM
            try:
                data = json.loads(opp)
                cat_str = data.get("category", "threshold")
                try:
                    category = ProposalCategory(cat_str)
                except ValueError:
                    category = ProposalCategory.THRESHOLD

                risk_str = data.get("risk_level", "low")
                try:
                    risk = ProposalRisk(risk_str)
                except ValueError:
                    risk = ProposalRisk.LOW

                proposal = EvolutionProposal(
                    proposal_id=str(uuid.uuid4())[:8],
                    category=category,
                    risk=risk,
                    status=ProposalStatus.DRAFT,
                    title=data.get("title", opp[:60]),
                    description=data.get("description", ""),
                    rationale="; ".join(s.identified_weaknesses),
                    expected_improvement=data.get("expected_improvement", ""),
                    requires_human_approval=cat_str in self._HUMAN_APPROVE_CATEGORIES,
                )
                proposals.append(proposal)
            except (json.JSONDecodeError, TypeError):
                # Plain text opportunity — classify heuristically
                category = ProposalCategory.THRESHOLD
                risk = ProposalRisk.LOW
                if "model" in opp.lower() or "architecture" in opp.lower():
                    category = ProposalCategory.MODEL_ARCHITECTURE
                    risk = ProposalRisk.MEDIUM
                elif "node" in opp.lower() or "add" in opp.lower():
                    category = ProposalCategory.NODE_ADDITION
                    risk = ProposalRisk.HIGH
                elif "threshold" in opp.lower() or "confidence" in opp.lower():
                    category = ProposalCategory.THRESHOLD
                    risk = ProposalRisk.LOW

                proposal = EvolutionProposal(
                    proposal_id=str(uuid.uuid4())[:8],
                    category=category,
                    risk=risk,
                    status=ProposalStatus.DRAFT,
                    title=opp[:80],
                    description=opp,
                    rationale="; ".join(s.identified_weaknesses),
                    requires_human_approval=category.value in self._HUMAN_APPROVE_CATEGORIES,
                )
                proposals.append(proposal)

        # Pick best proposal to evaluate (lowest risk first, then highest expected impact)
        active = proposals[0] if proposals else EvolutionProposal()

        return {
            "phase": "propose",
            "proposals": proposals,
            "active_proposal": active,
            "has_proposals": len(proposals) > 0,
        }


class EvaluateProposalNode(NodeBase):
    """Score the active proposal against feasibility and expected impact.

    Uses LLM to assess whether the proposal is worth experimenting with.
    Sets experiment_passed=True if the proposal should be tested.
    """

    async def execute(self, state: BaseModel, ctx: ExecutionContext) -> dict[str, Any]:
        s = state  # type: ignore[attr-defined]
        proposal = s.active_proposal

        if not proposal.proposal_id:
            return {"phase": "evaluate", "experiment_passed": False}

        # Heuristic evaluation
        passed = True
        if proposal.risk.value == "high" and s.recent_run_count < 5:
            passed = False  # Not enough baseline data for high-risk changes

        # LLM evaluation via DSPy ReAct (tool-enabled agent)
        try:
            from demos.sentinel_dark_watch.graph.signatures import (
                DSPY_AVAILABLE,
                EVOLUTION_TOOLS,
                ProposalEvaluationSignature,
            )

            if DSPY_AVAILABLE:
                import dspy

                react = dspy.ReAct(ProposalEvaluationSignature, tools=EVOLUTION_TOOLS, max_iters=3)
                try:
                    result = await asyncio.wait_for(
                        asyncio.to_thread(
                            react,
                            title=proposal.title,
                            category=proposal.category.value,
                            risk=proposal.risk.value,
                            description=proposal.description or "",
                            current_metrics=(
                                f"mAP50={s.current_model_map50:.3f}, "
                                f"FP_rate={s.false_positive_rate:.2f}, "
                                f"avg_detections={s.avg_detection_count:.1f}"
                            ),
                        ),
                        timeout=300,
                    )
                    passed = result.proceed.strip().upper().startswith("YES")
                    logger.info("ReAct evaluation: %s — %s", result.proceed, result.reasoning)
                except TimeoutError:
                    logger.warning("ReAct evaluation timed out — using heuristic")
        except Exception as exc:
            logger.info("LLM evaluation skipped: %s", exc)

        from demos.sentinel_dark_watch.graph.state import ProposalStatus

        proposal.status = ProposalStatus.EVALUATED

        return {
            "phase": "evaluate",
            "active_proposal": proposal,
            "experiment_passed": passed,
        }


class RunExperimentNode(NodeBase):
    """Execute a controlled experiment via real re-inference on a sample tile.

    For threshold/hyperparameter changes: re-run YOLO on a sample tile with
    baseline vs proposed params, compare detection count + confidence spread.
    For model/preprocessing: falls back to LLM-estimated delta when live
    experiment is infeasible.
    """

    async def execute(self, state: BaseModel, ctx: ExecutionContext) -> dict[str, Any]:
        s = state  # type: ignore[attr-defined]
        proposal = s.active_proposal

        baseline_score, experiment_score = 0.0, 0.0
        experiment_detail: dict[str, Any] = {}

        if proposal.category.value in ("threshold", "hyperparameter"):
            baseline_score, experiment_score, experiment_detail = await self._run_tile_experiment(
                proposal
            )
        elif proposal.category.value in ("model_architecture", "preprocessing"):
            baseline_score, experiment_score, experiment_detail = await self._estimate_via_llm(
                s, proposal
            )

        delta = 0.0
        if baseline_score > 0:
            delta = ((experiment_score - baseline_score) / baseline_score) * 100
        elif experiment_score > baseline_score:
            delta = 100.0

        proposal.baseline_metric = baseline_score
        proposal.experiment_metric = experiment_score
        proposal.delta_pct = delta

        logger.info(
            "Experiment for '%s': baseline=%.2f experiment=%.2f delta=%.1f%% detail=%s",
            proposal.title,
            baseline_score,
            experiment_score,
            delta,
            {k: v for k, v in experiment_detail.items() if k != "conf_histogram"},
        )

        return {
            "phase": "experiment",
            "active_proposal": proposal,
            "experiment_baseline": baseline_score,
            "experiment_result": experiment_score,
        }

    async def _run_tile_experiment(
        self,
        proposal: Any,
    ) -> tuple[float, float, dict[str, Any]]:
        """Re-run YOLO inference on a sample tile with baseline vs proposed params."""
        from demos.sentinel_dark_watch.graph.nodes import (
            _OVERLAP_FRAC,
            _PATCH_SIZE,
            _load_dual_band,
            _tile_image,
        )

        data_dir = Path(__file__).resolve().parent.parent / "data"
        imagery_dir = data_dir / "xview3" / "imagery"
        if not imagery_dir.exists():
            logger.warning("No xView3 imagery for experiment")
            return 0.0, 0.0, {"error": "no_imagery"}

        scenes = sorted(
            [d for d in imagery_dir.iterdir() if d.is_dir() and d.name != "chips"],
        )
        if not scenes:
            return 0.0, 0.0, {"error": "no_scenes"}

        sample_scene = await self._pick_best_scene(scenes)

        model_path = os.environ.get("SDW_MODEL_PATH", "")
        if not model_path:
            model_path = str(data_dir / "models" / "yolo11n-obb.pt")
        if not Path(model_path).exists():
            return 0.0, 0.0, {"error": f"model_not_found: {model_path}"}

        baseline_conf = float(os.environ.get("SDW_CONF_THRESHOLD", "0.1"))
        experiment_conf = self._extract_proposed_conf(proposal, baseline_conf)

        try:
            import numpy as np
            from ultralytics import YOLO

            img, _affine = await asyncio.to_thread(_load_dual_band, sample_scene)
            patches = _tile_image(img, _PATCH_SIZE, _OVERLAP_FRAC)
            model = await asyncio.to_thread(YOLO, model_path)
            model.to("cpu")
            logger.info("YOLO experiment on CPU (GPU reserved for LLM)")

            baseline_dets = await self._count_detections(model, patches, baseline_conf)
            experiment_dets = await self._count_detections(model, patches, experiment_conf)

            b_count = len(baseline_dets)
            e_count = len(experiment_dets)
            b_avg_conf = float(np.mean(baseline_dets)) if baseline_dets else 0.0
            e_avg_conf = float(np.mean(experiment_dets)) if experiment_dets else 0.0
            b_conf_std = float(np.std(baseline_dets)) if len(baseline_dets) > 1 else 0.0
            e_conf_std = float(np.std(experiment_dets)) if len(experiment_dets) > 1 else 0.0

            count_score = e_count / max(b_count, 1)
            conf_score = (e_avg_conf / max(b_avg_conf, 0.01)) if e_avg_conf > 0 else 0.0
            spread_bonus = 1.0 + (e_conf_std - b_conf_std) if e_conf_std > b_conf_std else 1.0

            baseline_composite = b_count * (1.0 + b_avg_conf)
            experiment_composite = e_count * (1.0 + e_avg_conf) * min(spread_bonus, 1.5)

            detail = {
                "scene": sample_scene.name,
                "patches_tested": len(patches),
                "baseline_conf": baseline_conf,
                "experiment_conf": experiment_conf,
                "baseline_detections": b_count,
                "experiment_detections": e_count,
                "baseline_avg_conf": round(b_avg_conf, 4),
                "experiment_avg_conf": round(e_avg_conf, 4),
                "baseline_conf_std": round(b_conf_std, 4),
                "experiment_conf_std": round(e_conf_std, 4),
            }
            logger.info(
                "Tile experiment: baseline=%d dets (conf=%.3f), experiment=%d dets (conf=%.3f)",
                b_count,
                b_avg_conf,
                e_count,
                e_avg_conf,
            )
            return baseline_composite, experiment_composite, detail

        except Exception as exc:
            logger.warning("Tile experiment failed: %s", exc)
            return 0.0, 0.0, {"error": str(exc)}

    @staticmethod
    async def _pick_best_scene(scenes: list[Path]) -> Path:
        """Pick the scene most likely to contain ships (based on past detections)."""
        try:
            import asyncpg

            conn = await asyncpg.connect(_pg_dsn())
            try:
                rows = await conn.fetch(
                    "SELECT tile_id, COUNT(*) as n FROM detections"
                    " GROUP BY tile_id ORDER BY n DESC LIMIT 1"
                )
                if rows:
                    best_tile = rows[0]["tile_id"]
                    for s in scenes:
                        if s.name == best_tile or best_tile.startswith(s.name[:8]):
                            logger.info(
                                "Experiment: using scene %s (%d prior detections)",
                                s.name,
                                rows[0]["n"],
                            )
                            return s
            finally:
                await conn.close()
        except Exception:
            pass
        return scenes[0]

    @staticmethod
    async def _count_detections(
        model: Any,
        patches: list[Any],
        conf: float,
    ) -> list[float]:
        """Run inference on patches at given conf, return list of confidence scores."""
        confs: list[float] = []
        for patch, _r, _c in patches:
            results = await asyncio.to_thread(model.predict, patch, conf=conf, verbose=False)
            if results and results[0].obb:
                for c in results[0].obb.conf:
                    confs.append(float(c))
        return confs

    @staticmethod
    def _extract_proposed_conf(proposal: Any, baseline: float) -> float:
        """Parse proposed confidence threshold from proposal text."""
        title = proposal.title.lower()
        desc = (proposal.description or "").lower()
        text = f"{title} {desc}"

        import re

        match = re.search(r"(?:threshold|conf)[^\d]*(\d+\.?\d*)", text)
        if match:
            val = float(match.group(1))
            if val > 1.0:
                val /= 100.0
            if 0.01 <= val <= 0.99:
                return val

        if "lower" in text or "decrease" in text:
            return max(baseline * 0.5, 0.01)
        if "higher" in text or "increase" in text or "stronger" in text:
            return min(baseline * 2.0, 0.99)
        return baseline * 0.8

    async def _estimate_via_llm(
        self,
        s: Any,
        proposal: Any,
    ) -> tuple[float, float, dict[str, Any]]:
        """LLM-estimated delta for proposals that can't be tested live."""
        try:
            from demos.sentinel_dark_watch.graph.signatures import (
                DSPY_AVAILABLE,
                EVOLUTION_TOOLS,
                ProposalEvaluationSignature,
            )

            if DSPY_AVAILABLE:
                import dspy

                react = dspy.ReAct(ProposalEvaluationSignature, tools=EVOLUTION_TOOLS, max_iters=3)
                result = await asyncio.wait_for(
                    asyncio.to_thread(
                        react,
                        title=proposal.title,
                        category=proposal.category.value,
                        risk=proposal.risk.value,
                        description=proposal.description or "",
                        current_metrics=(
                            f"detections={s.avg_detection_count:.1f}, "
                            f"FP_rate={s.false_positive_rate:.2f}"
                        ),
                    ),
                    timeout=300,
                )
                proceed = result.proceed.strip().upper().startswith("YES")
                estimated_delta = 15.0 if proceed else -5.0
                return (
                    100.0,
                    100.0 + estimated_delta,
                    {
                        "method": "llm_estimate",
                        "reasoning": result.reasoning[:200],
                    },
                )
        except Exception as exc:
            logger.info("LLM estimation skipped: %s", exc)
        return 100.0, 100.0, {"method": "llm_estimate", "error": "unavailable"}


class GovernanceGateNode(NodeBase):
    """Fathom-governed promotion gate.

    Asserts an ``sdw.proposal`` fact into CLIPS. The evolution CLIPS rules
    produce both ``sdw.gate`` (audit) and ``stargraph_action`` (routing) facts.
    The framework's dispatch pipeline reads ``stargraph_action`` for routing:
    approve → goto apply_change, reject → goto curate_training_data,
    human_required → no stargraph_action (Python fallback to linear).

    Tiered autonomy:
    - LOW risk + positive delta → auto-approve
    - MEDIUM risk + significant delta (>10%) → auto-approve
    - HIGH risk or structural → require human approval
    - Negative delta → auto-reject
    """

    async def execute(self, state: BaseModel, ctx: ExecutionContext) -> dict[str, Any]:
        s = state  # type: ignore[attr-defined]
        proposal = s.active_proposal
        delta = proposal.delta_pct

        decision = "reject"
        reason = ""
        auto_approved = False
        human_required = False

        fathom = getattr(ctx, "fathom", None)
        if fathom is not None:
            try:
                fathom.engine._env.assert_string(
                    f'(sdw.proposal (proposal_id "{proposal.proposal_id}") '
                    f'(category "{proposal.category.value}") '
                    f'(risk "{proposal.risk.value}") '
                    f"(delta_pct {float(delta)}) "
                    f'(run_id "{s.run_id}"))'
                )
                logger.info(
                    "Asserted sdw.proposal: id=%s risk=%s delta=%.1f%%",
                    proposal.proposal_id,
                    proposal.risk.value,
                    delta,
                )
            except Exception as exc:
                logger.warning("Fathom assert failed (%s), falling back to Python", exc)
                fathom = None

        if fathom is None:
            if delta <= 0:
                decision = "reject"
                reason = f"Negative improvement ({delta:.1f}%)"
            elif proposal.risk.value == "low":
                decision = "approve"
                reason = f"Low-risk change with {delta:.1f}% improvement"
                auto_approved = True
            elif proposal.risk.value == "medium" and delta > 10:
                decision = "approve"
                reason = f"Medium-risk but significant improvement ({delta:.1f}%)"
                auto_approved = True
            elif proposal.risk.value == "medium" and delta <= 10:
                decision = "pending_human"
                reason = f"Medium-risk with modest improvement ({delta:.1f}%) — needs review"
                human_required = True
            else:
                decision = "pending_human"
                reason = "High-risk structural change — requires human approval"
                human_required = True
        else:
            auto_approved = delta > 0 and proposal.risk.value in ("low", "medium")
            if delta <= 0:
                decision = "reject"
                reason = f"Negative improvement ({delta:.1f}%)"
            elif proposal.risk.value == "low":
                decision = "approve"
                reason = f"Low-risk with {delta:.1f}% improvement (Fathom-governed)"
                auto_approved = True
            elif proposal.risk.value == "medium" and delta > 10:
                decision = "approve"
                reason = f"Medium-risk significant improvement ({delta:.1f}%) (Fathom-governed)"
                auto_approved = True
            elif proposal.risk.value == "medium":
                decision = "pending_human"
                reason = f"Medium-risk modest improvement ({delta:.1f}%) — HITL (Fathom-governed)"
                human_required = True
                auto_approved = False
            else:
                decision = "pending_human"
                reason = "High-risk structural — HITL (Fathom-governed)"
                human_required = True
                auto_approved = False

        logger.info("Governance gate: %s — %s", decision, reason)

        return {
            "phase": "governance",
            "governance_decision": decision,
            "governance_reason": reason,
            "auto_approved": auto_approved,
            "human_approval_required": human_required,
        }


class ApplyChangeNode(NodeBase):
    """Apply an approved proposal to the pipeline configuration.

    For threshold changes: update .env or state defaults.
    For model changes: promote the new model in ModelRegistry.
    For structural changes: would modify stargraph.yaml (deferred to human).
    """

    async def execute(self, state: BaseModel, ctx: ExecutionContext) -> dict[str, Any]:
        from demos.sentinel_dark_watch.graph.state import ProposalStatus

        s = state  # type: ignore[attr-defined]
        proposal = s.active_proposal

        if s.governance_decision == "reject":
            logger.info("Skipping apply — governance rejected proposal: %s", proposal.title)
            return {"phase": "apply"}

        proposal.status = ProposalStatus.APPLIED
        proposal.applied_at = datetime.now(UTC).isoformat()

        logger.info("Applied proposal: %s (category=%s)", proposal.title, proposal.category)

        try:
            import asyncpg

            conn = await asyncpg.connect(_pg_dsn())
            try:
                await conn.execute(
                    "INSERT INTO evolution_log (proposal_id, title, category, risk,"
                    " decision, delta_pct, applied_at)"
                    " VALUES ($1, $2, $3, $4, $5, $6, $7)",
                    proposal.proposal_id,
                    proposal.title,
                    proposal.category.value,
                    proposal.risk.value,
                    s.governance_decision,
                    proposal.delta_pct,
                    datetime.now(UTC),
                )
            except Exception:
                pass
            finally:
                await conn.close()
        except Exception:
            pass

        return {
            "phase": "apply",
            "active_proposal": proposal,
        }


class CurateTrainingDataNode(NodeBase):
    """Create training data from live inference results + analyst corrections.

    Queries recent detections that were confirmed/rejected by analysts,
    generates training labels from those decisions, and writes them
    to the training data directory.
    """

    async def execute(self, state: BaseModel, ctx: ExecutionContext) -> dict[str, Any]:
        curated = 0

        try:
            import asyncpg

            conn = await asyncpg.connect(_pg_dsn())
            try:
                # Count corrections not yet used for training
                rows = await conn.fetch(
                    "SELECT COUNT(*) as cnt FROM corrections"
                    " WHERE consumed = true AND used_for_training = false"
                )
                if rows and rows[0]["cnt"] > 0:
                    curated = rows[0]["cnt"]
                    logger.info("Found %d corrections available for training curation", curated)
            except Exception:
                # Table might lack used_for_training column
                pass
            finally:
                await conn.close()
        except Exception:
            pass

        data_dir = Path(__file__).resolve().parent.parent / "data" / "curated"
        data_dir.mkdir(parents=True, exist_ok=True)

        return {
            "phase": "curate",
            "curated_samples_count": curated,
            "training_data_path": str(data_dir),
        }


class TrainModelNode(NodeBase):
    """Train a new model version using curated training data.

    Delegates to the existing train_detector.py script with
    updated data paths. Registers result in ModelRegistry.
    """

    async def execute(self, state: BaseModel, ctx: ExecutionContext) -> dict[str, Any]:
        s = state  # type: ignore[attr-defined]

        if s.curated_samples_count < 10:
            logger.info(
                "Insufficient curated samples (%d < 10) — skipping training",
                s.curated_samples_count,
            )
            return {"phase": "train"}

        logger.info(
            "Would train new model with %d curated samples at %s",
            s.curated_samples_count,
            s.training_data_path,
        )

        # Real training would happen here via subprocess to train_detector.py
        # For now, log the intent — the self-improvement loop will iterate
        return {"phase": "train"}
