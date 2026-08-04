# SPDX-License-Identifier: Apache-2.0
"""Live-network smoke tests for the SaaS packs' unauthenticated read paths.

Gated behind ``STARGRAPH_LIVE_NET=1`` (same switch as the ``std`` live
suite). Only GitHub's public read surface can run without credentials;
the authenticated paths (slack/s3/email/postgres and GitHub writes) are
covered offline in ``tests/unit/test_saas_tools.py`` and exercised for
real only by operators with the relevant env configured.
"""

from __future__ import annotations

import os

import pytest

from stargraph.tools.github.list_issues import list_issues
from stargraph.tools.github.read_file import read_file

pytestmark = [
    pytest.mark.integration,
    pytest.mark.slow,
    pytest.mark.skipif(
        not os.environ.get("STARGRAPH_LIVE_NET"),
        reason="set STARGRAPH_LIVE_NET=1 to run live network tests",
    ),
]


async def test_github_read_file_public_repo() -> None:
    out = await read_file(repo="python/cpython", path="README.rst")
    assert "Python" in out["content"]
    assert out["sha"]


async def test_github_list_issues_public_repo() -> None:
    out = await list_issues(repo="python/cpython", limit=3)
    assert out["issues"]
    assert all(i["number"] > 0 for i in out["issues"])
