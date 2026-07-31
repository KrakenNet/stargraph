# SPDX-License-Identifier: Apache-2.0
"""``slack.read_messages`` -- ``conversations.history`` reader."""

from __future__ import annotations

from typing import Any, cast

from stargraph.errors import StargraphRuntimeError
from stargraph.tools import _http, _saas
from stargraph.tools.decorator import tool
from stargraph.tools.spec import SideEffects

__all__ = ["read_messages"]

_MAX_LIMIT = 200


@tool(
    name="read_messages",
    namespace="slack",
    version="1",
    side_effects=SideEffects.read,
    requires_capability="tools:slack:read",
    description=(
        "Read recent messages from a Slack channel (conversations.history). "
        "Needs SLACK_BOT_TOKEN; channel must be an id the bot is a member of."
    ),
)
async def read_messages(
    channel: str,
    limit: int = 10,
    oldest: str | None = None,
) -> dict[str, Any]:
    """Newest-first messages: ``{channel, messages: [{ts, user, text}]}``."""
    token = _saas.require_env("SLACK_BOT_TOKEN", tool="slack.read_messages")
    params: dict[str, Any] = {"channel": channel, "limit": max(1, min(limit, _MAX_LIMIT))}
    if oldest is not None:
        params["oldest"] = oldest
    async with _http.build_client() as client:
        resp = await client.get(
            "https://slack.com/api/conversations.history",
            params=params,
            headers={"Authorization": f"Bearer {token}"},
        )
        resp.raise_for_status()
        payload: dict[str, Any] = resp.json()
    if not payload.get("ok"):
        raise StargraphRuntimeError(
            f"slack.read_messages failed: {payload.get('error', 'unknown_error')}",
            channel=channel,
        )
    raw = cast("list[Any]", payload.get("messages", []))
    messages: list[dict[str, str]] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        m = cast("dict[str, Any]", entry)
        messages.append(
            {
                "ts": str(m.get("ts", "")),
                "user": str(m.get("user", "")),
                "text": str(m.get("text", "")),
            }
        )
    return {"channel": channel, "messages": messages}
