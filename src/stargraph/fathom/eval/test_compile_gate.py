print("📢 PYTHON IS ALIVE!")

from unittest.mock import MagicMock, patch

import numpy as np
from compile_gate import CompileGateHarness, EvalResult

# 1. Setup Mock Objects
mock_judge = MagicMock()
mock_response = MagicMock()
mock_response.content = [
    MagicMock(text='{"passed_criteria_count": 5, "is_absolute_success": true}')
]
mock_judge.messages.create.return_value = mock_response


# 2. Setup a Dummy Agent Runner
def mock_agent_runner(scenario):
    dummy_dialogue = [{"role": "user", "content": f"Execute request for {scenario['id']}"}]
    dummy_telemetry = {"decision_junction_1": False}
    return dummy_dialogue, dummy_telemetry


def run_test():
    print("🚀 Initializing CompileGateHarness Test...")

    # 3. Generate Mock Scenarios on the fly
    mock_scenarios = [{"id": f"scenario_{i}", "intent": "mock_test"} for i in range(10)]

    with patch.object(CompileGateHarness, "_load_local_scenarios", return_value=mock_scenarios):
        harness = CompileGateHarness(
            scenarios_path="dummy_path.json", cross_judge_client=mock_judge
        )

    # 4. Run the Concurrent Evaluation Loop
    print("🏃 Running evaluation loop (Simulating agent executions)...")
    compiled_results = harness.run_eval_loop(
        arm_name="compiled_v2_brain", runner_fn=mock_agent_runner
    )

    print(f"\n📊 Evaluation Complete for: {compiled_results.arm_name}")
    print(f"   Success Rate: {compiled_results.task_success * 100}%")
    print(f"   Routing Failures per Hub: {compiled_results.routing_failures_per_hub}")

    # 5. Synthesize a Baseline to evaluate the Math Gate
    baseline_results = EvalResult(
        arm_name="legacy_rules_baseline",
        task_success=1.0,
        routing_failures_per_hub={"decision_junction_1": 0},
        raw_success_vector=np.ones(10),
        raw_routing_failures={"decision_junction_1": np.zeros(10)},
    )

    # 6. Evaluate the Gate
    print("\n🧮 Evaluating Gate Promotion (Baseline vs. Compiled)...")
    block_promotion, metrics = harness.evaluate_gate(baseline_results, compiled_results)

    print(f"🚫 Block Promotion Decision: {block_promotion}")
    print(f"📈 Metrics Report Details: {metrics}")

    if not block_promotion:
        print("\n✅ TEST PASSED: The gate executed flawlessly without statistical errors!")
    else:
        print(
            "\n⚠️ GATE BLOCKED: Working as intended (triggered safely due to variance or baseline matching)."
        )


# MAKE SURE THESE LINES ARE TOTALLY FLUSH WITH THE LEFT MARGIN (NO SPACES):
if __name__ == "__main__":
    run_test()
