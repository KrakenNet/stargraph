# SPDX-License-Identifier: Apache-2.0
"""The ``digest`` Skill instance — a ``workflow`` reference skill.

``workflow`` = fixed topology that condenses rather than retrieves: chunk the
input, summarize each chunk (map), then summarize the summaries (reduce).
``state_schema`` is the whole contract: the engine ``SubGraphNode`` lets this
skill write only ``chunks`` / ``partials`` / ``summary`` back to the parent
state, and ``DigestState`` declares exactly those channels.
"""

from __future__ import annotations

from stargraph.skills.base import Skill, SkillKind
from stargraph.skills.digest.state import DigestState

DIGEST = Skill(
    name="digest",
    version="0.1.0",
    kind=SkillKind.workflow,
    description="Condense long text via a chunked map-reduce summary into a single summary.",
    state_schema=DigestState,
    subgraph="stargraph.skills.digest:graph.yaml",
    requires=["llm.generate"],
    system_prompt="Summarize the text faithfully and concisely, preserving the key facts.",
)
