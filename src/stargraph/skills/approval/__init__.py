# SPDX-License-Identifier: Apache-2.0
"""stargraph.skills.approval — the ``approval`` human-in-the-loop gate skill.

A reusable gate deciding whether a proposed side-effecting action may proceed,
combining an optional policy pre-approval with a human verdict. See
:data:`APPROVAL` for the Skill manifest and :class:`ApprovalState` for the
declared output channels.
"""

from __future__ import annotations

from stargraph.skills.approval._skill import APPROVAL
from stargraph.skills.approval.state import ApprovalState

__all__ = ["APPROVAL", "ApprovalState"]
