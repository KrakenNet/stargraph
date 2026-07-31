# SPDX-License-Identifier: Apache-2.0
"""``github.list_issues`` -- list repo issues (PRs filtered out)."""

from __future__ import annotations

from typing import Any, cast

from stargraph.errors import StargraphRuntimeError
from stargraph.tools import _http
from stargraph.tools.decorator import tool
from stargraph.tools.github import _api
from stargraph.tools.spec import SideEffects

__all__ = ["list_issues"]

_MAX_LIMIT = 100
_STATES = ("open", "closed", "all")


@tool(
    name="list_issues",
    namespace="github",
    version="1",
    side_effects=SideEffects.read,
    requires_capability="tools:github:read",
    description=(
        "List issues in a GitHub repo (newest first, pull requests excluded). "
        "repo is 'owner/name'; state is open/closed/all."
    ),
)
async def list_issues(repo: str, state: str = "open", limit: int = 20) -> dict[str, Any]:
    """Return ``{repo, issues: [{number, title, state, user, url}]}``."""
    _api.check_repo(repo)
    if state not in _STATES:
        raise StargraphRuntimeError(
            f"github.list_issues: state must be one of {_STATES}, got {state!r}",
            repo=repo,
        )
    per_page = max(1, min(limit, _MAX_LIMIT))
    async with _http.build_client() as client:
        resp = await client.get(
            f"{_api.API_BASE}/repos/{repo}/issues",
            params={"state": state, "per_page": per_page},
            headers=_api.headers(require_token=False, tool="github.list_issues"),
        )
        resp.raise_for_status()
        payload: list[Any] = resp.json()
    issues: list[dict[str, Any]] = []
    for entry in payload:
        if not isinstance(entry, dict):
            continue
        item = cast("dict[str, Any]", entry)
        if "pull_request" in item:  # the issues API interleaves PRs
            continue
        user = cast("dict[str, Any]", item.get("user") or {})
        issues.append(
            {
                "number": int(item.get("number", 0)),
                "title": str(item.get("title", "")),
                "state": str(item.get("state", "")),
                "user": str(user.get("login", "")),
                "url": str(item.get("html_url", "")),
            }
        )
    return {"repo": repo, "issues": issues[:per_page]}
