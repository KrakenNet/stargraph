# SPDX-License-Identifier: Apache-2.0
"""stargraph.skills.digest — the ``digest`` workflow reference skill.

Long text → a chunked map-reduce summary. See :data:`DIGEST` for the Skill
manifest and :class:`DigestState` for the declared output channels.
"""

from __future__ import annotations

from stargraph.skills.digest._skill import DIGEST
from stargraph.skills.digest.state import DigestState

__all__ = ["DIGEST", "DigestState"]
