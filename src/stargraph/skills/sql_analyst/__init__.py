# SPDX-License-Identifier: Apache-2.0
"""stargraph.skills.sql_analyst — the ``sql-analyst`` agent reference skill.

A natural-language question over structured data → a query → run → validated
answer, with a bounded repair loop. See :data:`SQL_ANALYST` for the Skill
manifest and :class:`SqlAnalystState` for the declared output channels.
"""

from __future__ import annotations

from stargraph.skills.sql_analyst._skill import SQL_ANALYST
from stargraph.skills.sql_analyst.state import SqlAnalystState

__all__ = ["SQL_ANALYST", "SqlAnalystState"]
