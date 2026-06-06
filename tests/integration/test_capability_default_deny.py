# SPDX-License-Identifier: Apache-2.0
"""Integration test for cleared-deployment default-deny store access.

Per FR-20 / NFR-6 / AC-8: a cleared deployment
(``Capabilities(default_deny=True, ...)`` whose ``granted`` set does
not include ``db.vectors:read``) must refuse any operation that
requires that capability. The runtime tool-execution path
(:func:`stargraph.runtime.tool_exec.execute_tool`) is the canonical
gate site -- step 2 raises :class:`stargraph.errors.CapabilityError`
when the gate denies.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from stargraph.errors import CapabilityError
from stargraph.runtime.tool_exec import RunContext, execute_tool
from stargraph.security.capabilities import Capabilities, CapabilityClaim
from stargraph.tools.decorator import tool
from stargraph.tools.spec import SideEffects


@tool(
    name="vector_read",
    namespace="test",
    version="1",
    side_effects=SideEffects.read,
    requires_capability="db.vectors:read",
    input_schema={"type": "object"},
    output_schema={"type": "object"},
)
def vector_read_tool() -> dict[str, Any]:
    return {"hits": []}


@pytest.mark.integration
@pytest.mark.knowledge
def test_cleared_deployment_refuses_ungranted_store_access() -> None:
    """Cleared deployment without ``db.vectors:read`` raises ``CapabilityError``."""
    # default_deny=True with grants for a *different* store only.
    caps = Capabilities(
        default_deny=True,
        granted={CapabilityClaim(name="db.facts", scope="*")},  # pyright: ignore[reportUnhashable]
    )
    ctx = RunContext(run_id="r1", capabilities=caps, deployment="cleared")

    with pytest.raises(CapabilityError) as excinfo:
        asyncio.run(execute_tool(vector_read_tool, {}, run_ctx=ctx))

    assert excinfo.value.context["tool_id"] == "vector_read"
    assert "db.vectors:read" in excinfo.value.context["capability"]
    assert excinfo.value.context["deployment"] == "cleared"
