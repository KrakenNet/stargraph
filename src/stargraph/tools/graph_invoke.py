from typing import Any

from stargraph.core.graph import Graph
from stargraph.core.registry import registry  # Block ④ Runtime Registry
from stargraph.tools.decorator import tool


@tool(
    name="graph_invoke",
    description="Invokes a registered stargraph workflow by its ID or registry alias and returns the final execution state.",
)
async def graph_invoke(
    graph_id: str, initial_state_patch: dict[str, Any] | None = None
) -> dict[str, Any]:
    """
    Executes a compiled workflow synchronously as a functional tool.
    Resolves active weights via the live Block ④ registry mapping.
    """
    # 1. Grab target weights live from registry
    resolved_meta = registry.resolve_active_alias(graph_id)
    compiled_model_id = resolved_meta.get("model_id")
    version = resolved_meta.get("version")
    determinism_knob = resolved_meta.get("determinism_knob", "fully-compiled")  # Honors #117

    # 2. Package configuration context to override target weights
    config = {
        "compiled_model_id": compiled_model_id,
        "compiled_version": version,
        "determinism": determinism_knob,
        "state_patch": initial_state_patch or {},
    }

    # 3. Spin up run handle and wait out the completion
    graph_run = await Graph.start(graph_id, config=config)
    final_state = await graph_run.wait_for_completion()

    # 4. Return terminal state enriched with immutable audit data
    return {
        "final_state": final_state,
        "_audit": {
            "invoked_alias": graph_id,
            "executed_model_id": compiled_model_id,
            "version": version,
        },
    }
