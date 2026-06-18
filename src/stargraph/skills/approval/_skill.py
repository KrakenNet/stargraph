# SPDX-License-Identifier: Apache-2.0
"""The ``approval`` Skill instance — a ``workflow`` reference skill.

``workflow`` = fixed topology, no LLM: a human-in-the-loop gate that decides
whether a proposed side-effecting action may proceed. ``state_schema`` is the
whole contract: the engine ``SubGraphNode`` lets this skill write only
``status`` / ``approved`` / ``decided_by`` / ``reason`` back to the parent
state, and ``ApprovalState`` declares exactly those channels.
"""

from __future__ import annotations

from stargraph.skills.approval.state import ApprovalState
from stargraph.skills.base import Skill, SkillKind

APPROVAL = Skill(
    name="approval",
    version="0.1.0",
    kind=SkillKind.workflow,
    description="Gate a proposed side-effecting action behind a policy check and a human verdict.",
    state_schema=ApprovalState,
    subgraph="stargraph.skills.approval:graph.yaml",
    requires=["hitl.respond"],
    system_prompt="Deny by default; approve only on an explicit policy or human yes.",
)
