# SPDX-License-Identifier: Apache-2.0
"""``github.create_issue`` -- gated, idempotent issue creation.

Idempotency is real here (unlike providers with no server-side search):
the caller's ``dedupe_key`` is stamped into the issue body as
``<!-- stargraph-dedupe:<key> -->`` and the live path searches for that
marker first; a retried call returns ``status="exists"`` with the
original issue instead of filing a duplicate.
"""

from __future__ import annotations

from typing import Any

from stargraph.tools import _http, _saas
from stargraph.tools.decorator import tool
from stargraph.tools.github import _api
from stargraph.tools.spec import SideEffects

__all__ = ["create_issue"]

_NAMESPACE = "github"


def _marker(key: str) -> str:
    return f"stargraph-dedupe:{key}"


@tool(
    name="create_issue",
    namespace=_NAMESPACE,
    version="1",
    side_effects=SideEffects.write,
    requires_capability="tools:github:write",
    description=(
        "Create a GitHub issue. Dry-run by default; set STARGRAPH_GITHUB_LIVE=1 "
        "to file it. Needs GITHUB_TOKEN and a caller-supplied dedupe_key -- a "
        "retried call finds the marker and returns the existing issue."
    ),
)
async def create_issue(
    repo: str,
    title: str,
    body: str,
    dedupe_key: str,
    labels: list[str] | None = None,
) -> dict[str, Any]:
    """Create (or find) the issue; ``{status: ok|exists|dry-run, number, url}``."""
    _api.check_repo(repo)
    key = _saas.require_dedupe_key(dedupe_key, "github.create_issue")
    request_body: dict[str, Any] = {
        "title": title,
        "body": f"{body}\n\n<!-- {_marker(key)} -->",
    }
    if labels:
        request_body["labels"] = labels

    if not _saas.live_enabled(_NAMESPACE):
        return _saas.dry_run_envelope(_NAMESPACE, key, request_body)

    headers = _api.headers(require_token=True, tool="github.create_issue")
    async with _http.build_client() as client:
        search = await client.get(
            f"{_api.API_BASE}/search/issues",
            params={"q": f'repo:{repo} in:body "{_marker(key)}"'},
            headers=headers,
        )
        search.raise_for_status()
        found: list[dict[str, Any]] = search.json().get("items", [])
        if found:
            first = found[0]
            return {
                "status": "exists",
                "number": int(first.get("number", 0)),
                "url": str(first.get("html_url", "")),
            }
        resp = await client.post(
            f"{_api.API_BASE}/repos/{repo}/issues",
            json=request_body,
            headers=headers,
        )
        resp.raise_for_status()
        created: dict[str, Any] = resp.json()
    return {
        "status": "ok",
        "number": int(created.get("number", 0)),
        "url": str(created.get("html_url", "")),
        "__stargraph_provenance__": {
            "origin": "tool",
            "source": _NAMESPACE,
            "external_id": str(created.get("number", "")),
        },
    }
