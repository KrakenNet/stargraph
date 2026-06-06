# SPDX-License-Identifier: Apache-2.0
"""stargraph.skills.refs -- in-tree reference :class:`Skill` implementations (FR-32).

Phase-1 POC ships :class:`stargraph.skills.refs.rag.RagSkill` (FR-32 / AC-7.1);
``autoresearch`` (FR-33) and ``wiki`` (FR-34) land in subsequent tasks.
"""

from __future__ import annotations

from stargraph.skills.refs.rag import RagSkill, RagState

__all__ = ["RagSkill", "RagState"]
