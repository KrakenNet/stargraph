# src/stargraph/eval/compile_gate.py

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

import numpy as np
import scipy.stats as stats


@dataclass
class EvalResult:
    arm_name: str
    task_success: float  # Average success rate (0.0 to 1.0)
    routing_failures_per_hub: dict[str, int]  # Total failures at each decision junction
    raw_success_vector: np.ndarray  # Array of 1s (pass) and 0s (fail) for each scenario
    raw_routing_failures: dict[str, np.ndarray]  # Junction ID -> array of 1s and 0s


class CompileGateHarness:
    def __init__(self, scenarios_path: str, cross_judge_client: Any):
        """
        Initializes our safety inspector.
        cross_judge_client must be an independent AI provider (like Anthropic Claude)
        to make sure the evaluation is completely unbiased.
        """
        self.scenarios = self._load_local_scenarios(scenarios_path)
        self.judge = cross_judge_client

    def _load_local_scenarios(self, path: str) -> list[dict[str, Any]]:
        with open(path) as f:
            data = json.load(f)
        return data if isinstance(data, list) else data.get("scenarios", [])

    def _evaluate_single_scenario(
        self, args: tuple[int, Any, dict[str, Any]]
    ) -> tuple[int, int, dict[str, int]]:
        """Helper method to parallelize both the simulation AND the judge evaluation."""
        idx, runner_fn, scenario = args
        dialogue_history, routing_telemetry = runner_fn(scenario)

        # Call the judge instantly inside the thread pool
        is_successful = self._call_cross_judge(scenario, dialogue_history)
        success_val = 1 if is_successful else 0

        return idx, success_val, {k: (1 if v else 0) for k, v in routing_telemetry.items()}

    def run_eval_loop(self, arm_name: str, runner_fn: Any) -> EvalResult:
        """Runs scenarios and cross-judge calls concurrently to maximize throughput."""
        successes = [0] * len(self.scenarios)
        hub_tracks: dict[str, list[int]] = {}

        # Package payloads for the thread pool
        tasks = [(idx, runner_fn, scenario) for idx, scenario in enumerate(self.scenarios)]

        # Parallelize both steps simultaneously
        with ThreadPoolExecutor(max_workers=10) as executor:
            results = list(executor.map(self._evaluate_single_scenario, tasks))

        for idx, success_val, local_routing in results:
            successes[idx] = success_val

            for hub_id, failed_val in local_routing.items():
                hub_tracks.setdefault(hub_id, [0] * len(self.scenarios))
                hub_tracks[hub_id][idx] = failed_val

        return EvalResult(
            arm_name=arm_name,
            task_success=float(np.mean(successes)),
            routing_failures_per_hub={k: sum(v) for k, v in hub_tracks.items()},
            raw_success_vector=np.array(successes),
            raw_routing_failures={k: np.array(v) for k, v in hub_tracks.items()},
        )

    def _call_cross_judge(
        self, scenario: dict[str, Any], dialogue_log: list[dict[str, Any]]
    ) -> bool:
        """Sends the dialogue to our external judge to check our 5 core criteria."""
        prompt = f"""
        You are an independent external auditor validating an agentic workflow compilation.
        Evaluate this dialogue log against the initialization scenario layout.
        
        Scenario Context: {json.dumps(scenario)}
        Dialogue Log: {json.dumps(dialogue_log)}
        
        Evaluate across exactly 5 criteria:
        1. Intent Preservation: Did the workflow retain original user intent?
        2. State-Machine Alignment: Did it stay bounded by true operational graph paths?
        3. Guardrail Adherence: Were audited system controls maintained without leaks?
        4. Information Density: Did it avoid weight-tuning loops/repetitions?
        5. Task Resolution: Did the terminal state resolve the initial request safely?
        
        Respond ONLY with a JSON object structured exactly like this:
        {{"passed_criteria_count": 5, "is_absolute_success": true}}
        """
        try:
            response = self.judge.messages.create(
                model="claude-3-5-sonnet-20241022",
                max_tokens=150,
                temperature=0.0,
                messages=[{"role": "user", "content": prompt}],
            )
            res_data = json.loads(response.content[0].text.strip())
            return bool(res_data.get("is_absolute_success", False))
        except Exception:
            # Safe failure mode
            return False

    def _safe_wilcoxon_p(self, v1: np.ndarray, v2: np.ndarray) -> float:
        """Helper to protect against statistical crashes when distributions are identical."""
        if np.array_equal(v1, v2):
            return 1.0  # Zero difference means no mathematical shift occurred
        try:
            _, p_val = stats.wilcoxon(v1, v2)
            return float(p_val)
        except ValueError:
            return 1.0

    def evaluate_gate(
        self, baseline: EvalResult, compiled: EvalResult
    ) -> tuple[bool, dict[str, Any]]:
        """Compares baseline rules against compiled graphs using safe statistical gates."""
        p_val_success = self._safe_wilcoxon_p(
            baseline.raw_success_vector, compiled.raw_success_vector
        )

        hub_p_values = {}
        for hub_id in baseline.raw_routing_failures:
            if hub_id in compiled.raw_routing_failures:
                hub_p_values[hub_id] = self._safe_wilcoxon_p(
                    baseline.raw_routing_failures[hub_id], compiled.raw_routing_failures[hub_id]
                )

        sorted_hubs = sorted(hub_p_values.items(), key=lambda x: x[1])
        m_total_hubs = len(sorted_hubs)
        regression_detected = False
        hub_reports = {}

        for rank_idx, (hub_id, raw_p) in enumerate(sorted_hubs):
            # Safe Holm-Bonferroni upper-bound capping at 1.0
            adjusted_p = min(1.0, raw_p * (m_total_hubs - rank_idx))

            has_more_failures = compiled.routing_failures_per_hub.get(
                hub_id, 0
            ) > baseline.routing_failures_per_hub.get(hub_id, 0)
            is_significant = (adjusted_p < 0.05) and has_more_failures

            if is_significant:
                regression_detected = True

            hub_reports[hub_id] = {
                "raw_p": float(raw_p),
                "adjusted_p": float(adjusted_p),
                "significant_regression": is_significant,
            }

        ci_lower, ci_upper = self._bootstrap_ci(compiled.raw_success_vector)

        task_regressed = compiled.task_success < baseline.task_success and p_val_success < 0.05
        block_promotion = task_regressed or regression_detected

        return block_promotion, {
            "block_promotion": block_promotion,
            "task_success_p_value": float(p_val_success),
            "compiled_bootstrap_95_ci": (ci_lower, ci_upper),
            "hub_evaluation": hub_reports,
        }

    def _bootstrap_ci(self, data: np.ndarray, resamples: int = 1000) -> tuple[float, float]:
        if len(data) == 0:
            return 0.0, 0.0
        boot_means = [
            np.mean(np.random.choice(data, size=len(data), replace=True)) for _ in range(resamples)
        ]
        return float(np.percentile(boot_means, 2.5)), float(np.percentile(boot_means, 97.5))
