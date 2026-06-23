# SPDX-License-Identifier: Apache-2.0
"""The ``sql-analyst`` Skill instance — an ``agent`` reference skill.

``agent`` = drives a bounded generate→run→repair loop toward a goal (a
validated query result), not a single pure transform. ``state_schema`` is the
whole contract: the engine ``SubGraphNode`` lets this skill write only
``query`` / ``rows`` / ``answer`` / ``error`` / ``attempts`` / ``succeeded``
back to the parent state, and ``SqlAnalystState`` declares exactly those
channels.
"""

from __future__ import annotations

from stargraph.skills.base import Skill, SkillKind
from stargraph.skills.sql_analyst.state import SqlAnalystState

SQL_ANALYST = Skill(
    name="sql-analyst",
    version="0.1.0",
    kind=SkillKind.agent,
    description="Answer a question over structured data by generating, running, and repairing SQL.",
    state_schema=SqlAnalystState,
    subgraph="stargraph.skills.sql_analyst:graph.yaml",
    requires=["llm.generate"],
    system_prompt="Write SQL for the question against the schema; repair it from the last error.",
)
