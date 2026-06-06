# SPDX-License-Identifier: Apache-2.0
"""``stargraph.skills`` entry-point group is discovered by the loader.

The two-stage pluggy loader enumerates entry points across
:data:`stargraph.plugin.loader.GROUPS`. This test pins the contract that
``"stargraph.skills"`` is one of those groups so plugin distributions can
contribute skills via ``[project.entry-points."stargraph.skills"]``.
"""

from __future__ import annotations

import pytest

from stargraph.plugin.loader import GROUPS

pytestmark = [pytest.mark.knowledge, pytest.mark.unit]


def test_stargraph_skills_group_in_groups_tuple() -> None:
    """``"stargraph.skills"`` is one of the four discovered entry-point groups."""
    assert "stargraph.skills" in GROUPS
