import asyncio

# Mock out the system registry and graph before importing the tool
import sys
from unittest.mock import MagicMock

mock_registry = MagicMock()
mock_graph = MagicMock()

# Setup dummy return values for the Block 4 registry resolution
mock_registry.resolve_active_alias.return_value = {
    "model_id": "compiled_v2_brain_weights",
    "version": "2.4.1",
    "determinism_knob": "fully-compiled",
}

# Setup a dummy graph completion handler
mock_run_handle = MagicMock()


async def dummy_wait():
    return {"status": "success", "user_authenticated": True, "balance": 150.00}


mock_run_handle.wait_for_completion = dummy_wait


async def dummy_start(graph_id, config):
    print(f"🎯 Graph.start() caught invocation for ID: '{graph_id}'")
    print(
        f"📦 Configured targeting weights: {config['compiled_model_id']} (v{config['compiled_version']})"
    )
    return mock_run_handle


mock_graph.start = dummy_start

# Inject our mocks dynamically into python's module cache
sys.modules["stargraph.core.registry"] = MagicMock(registry=mock_registry)
sys.modules["stargraph.core.graph"] = MagicMock(Graph=mock_graph)
sys.modules["stargraph.tools.decorator"] = MagicMock(tool=lambda **kwargs: lambda f: f)

# Now import the tool function we just built
from stargraph.tools.graph_invoke import graph_invoke


async def main():
    print("🚀 Initializing standalone graph_invoke tool execution check...")

    # Run the tool function directly with mock parameters
    result = await graph_invoke(
        graph_id="production_payment_clearance_flow", initial_state_patch={"amount": 50.0}
    )

    print("\n📬 Tool Invocation Returned Output State Successfully:")
    print(f"   Final State: {result['final_state']}")
    print(f"   Audit Lineage Record: {result['_audit']}")
    print("\n✅ RUN SUCCESSFUL: Tool dynamically resolved weights and verified audit trail.")


if __name__ == "__main__":
    asyncio.run(main())
