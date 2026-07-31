# SPDX-License-Identifier: Apache-2.0
"""``slack.post_message`` -- gated ``chat.postMessage``."""

from __future__ import annotations

from typing import Any

from stargraph.errors import StargraphRuntimeError
from stargraph.tools import _http, _saas
from stargraph.tools.decorator import tool
from stargraph.tools.spec import SideEffects

__all__ = ["post_message"]

_NAMESPACE = "slack"


@tool(
    name="post_message",
    namespace=_NAMESPACE,
    version="1",
    side_effects=SideEffects.write,
    requires_capability="tools:slack:write",
    description=(
        "Post a message to a Slack channel (chat.postMessage). Dry-run by "
        "default; set STARGRAPH_SLACK_LIVE=1 to send. Needs SLACK_BOT_TOKEN "
        "and a caller-supplied dedupe_key (stamped into message metadata)."
    ),
)
async def post_message(
    channel: str,
    text: str,
    dedupe_key: str,
    thread_ts: str | None = None,
) -> dict[str, Any]:
    """Post ``text`` to ``channel`` (id or name); returns ``{status, ts, channel}``."""
    key = _saas.require_dedupe_key(dedupe_key, "slack.post_message")
    body: dict[str, Any] = {
        "channel": channel,
        "text": text,
        "metadata": {
            "event_type": "stargraph_dedupe",
            "event_payload": {"dedupe_key": key},
        },
    }
    if thread_ts is not None:
        body["thread_ts"] = thread_ts

    if not _saas.live_enabled(_NAMESPACE):
        return _saas.dry_run_envelope(_NAMESPACE, key, body)

    token = _saas.require_env("SLACK_BOT_TOKEN", tool="slack.post_message")
    async with _http.build_client() as client:
        resp = await client.post(
            "https://slack.com/api/chat.postMessage",
            json=body,
            headers={"Authorization": f"Bearer {token}"},
        )
        resp.raise_for_status()
        payload: dict[str, Any] = resp.json()
    if not payload.get("ok"):
        raise StargraphRuntimeError(
            f"slack.post_message failed: {payload.get('error', 'unknown_error')}",
            channel=channel,
        )
    return {
        "status": "ok",
        "channel": str(payload.get("channel", channel)),
        "ts": str(payload.get("ts", "")),
        "__stargraph_provenance__": {
            "origin": "tool",
            "source": _NAMESPACE,
            "external_id": str(payload.get("ts", "")),
        },
    }
