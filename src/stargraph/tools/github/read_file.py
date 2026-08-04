# SPDX-License-Identifier: Apache-2.0
"""``github.read_file`` -- fetch one file via the contents API."""

from __future__ import annotations

import base64
from typing import Any

from stargraph.errors import StargraphRuntimeError
from stargraph.tools import _http
from stargraph.tools.decorator import tool
from stargraph.tools.github import _api
from stargraph.tools.spec import SideEffects

__all__ = ["read_file"]

_MAX_BYTES = 1_048_576  # decoded cap; matches std.file_read


@tool(
    name="read_file",
    namespace="github",
    version="1",
    side_effects=SideEffects.read,
    requires_capability="tools:github:read",
    description=(
        "Read a file from a GitHub repo (contents API). repo is 'owner/name'; "
        "optional ref pins a branch/tag/sha. GITHUB_TOKEN optional for public repos."
    ),
)
async def read_file(repo: str, path: str, ref: str | None = None) -> dict[str, Any]:
    """Return ``{repo, path, content, sha, truncated}`` (UTF-8, decode-replace)."""
    _api.check_repo(repo)
    params: dict[str, Any] = {}
    if ref is not None:
        params["ref"] = ref
    async with _http.build_client() as client:
        resp = await client.get(
            f"{_api.API_BASE}/repos/{repo}/contents/{path}",
            params=params,
            headers=_api.headers(require_token=False, tool="github.read_file"),
        )
        if resp.status_code == 404:
            raise StargraphRuntimeError(
                f"github.read_file: {repo}:{path} not found (or private without GITHUB_TOKEN)",
                repo=repo,
                path=path,
            )
        resp.raise_for_status()
        payload: dict[str, Any] = resp.json()
    if payload.get("type") != "file" or "content" not in payload:
        raise StargraphRuntimeError(
            f"github.read_file: {path!r} is not a regular file (type={payload.get('type')!r})",
            repo=repo,
            path=path,
        )
    raw = base64.b64decode(payload["content"])
    truncated = len(raw) > _MAX_BYTES
    return {
        "repo": repo,
        "path": path,
        "content": raw[:_MAX_BYTES].decode("utf-8", errors="replace"),
        "sha": str(payload.get("sha", "")),
        "truncated": truncated,
    }
