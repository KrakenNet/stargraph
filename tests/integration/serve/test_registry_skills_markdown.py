# SPDX-License-Identifier: Apache-2.0
"""P2 round-trip: vendored Claude Code SKILL.md files surface via the serve API.

User-required verification (plan Phase 2): real Claude Code skills,
vendored **unmodified** under ``tests/fixtures/claude-skills/``, must
compile, register on the ``ToolRegistry``, and appear in
``GET /v1/registry/skills`` -- the same seed path ``stargraph serve``
runs at startup (``seed_markdown_skills`` in ``cli/serve.py``).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest

from stargraph.registry.tools import ToolRegistry
from stargraph.serve.api import create_app
from stargraph.serve.profiles import OssDefaultProfile
from stargraph.skills.markdown import seed_markdown_skills

pytestmark = [pytest.mark.serve, pytest.mark.integration]

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "claude-skills"


async def test_vendored_claude_skills_surface_in_registry_endpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Isolate discovery to the vendored fixtures only.
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path / "isolated-home"))
    monkeypatch.delenv("STARGRAPH_SKILLS_DIR", raising=False)

    registry = ToolRegistry()
    compiled = seed_markdown_skills(registry, extra_dir=FIXTURES)
    assert len(compiled) == 3

    deps: dict[str, Any] = {"runs": {}, "registry": {"tools": registry}}
    app = create_app(OssDefaultProfile(), deps=deps)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/v1/registry/skills")

    assert resp.status_code == 200, resp.text
    rows: list[dict[str, Any]] = resp.json()
    by_name = {row["name"]: row for row in rows}
    assert set(by_name) == {"init-project", "caveman-stats", "ruflo-doctor"}
    for row in by_name.values():
        assert row["namespace"] == "local"
        assert row["kind"] == "agent"
        assert row["description"].strip()
        assert row["system_prompt"].strip()
