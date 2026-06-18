# SPDX-License-Identifier: Apache-2.0
"""The ``extract`` Skill instance — a ``utility`` reference skill.

``utility`` = pure transformation, no external side effects (beyond the LLM
read). ``state_schema`` is the whole contract: the engine ``SubGraphNode`` lets
this skill write only ``fields`` / ``missing`` / ``valid`` back to the parent
state, and ``ExtractState`` declares exactly those channels.
"""

from __future__ import annotations

from stargraph.skills.base import Skill, SkillKind
from stargraph.skills.extract.state import ExtractState

EXTRACT = Skill(
    name="extract",
    version="0.1.0",
    kind=SkillKind.utility,
    description="Pull a named set of fields out of unstructured text and report what is missing.",
    state_schema=ExtractState,
    subgraph="stargraph.skills.extract:graph.yaml",
    requires=["llm.generate"],
    system_prompt="Extract the requested fields from the text; leave a field blank if absent.",
)
