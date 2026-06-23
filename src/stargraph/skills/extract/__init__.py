# SPDX-License-Identifier: Apache-2.0
"""stargraph.skills.extract — the ``extract`` utility reference skill.

Unstructured text → a validated set of named fields. See :data:`EXTRACT` for
the Skill manifest and :class:`ExtractState` for the declared output channels.
"""

from __future__ import annotations

from stargraph.skills.extract._skill import EXTRACT
from stargraph.skills.extract.state import ExtractState

__all__ = ["EXTRACT", "ExtractState"]
