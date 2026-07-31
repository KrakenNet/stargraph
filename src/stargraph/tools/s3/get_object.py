# SPDX-License-Identifier: Apache-2.0
"""``s3.get_object`` -- fetch one object as text."""

from __future__ import annotations

import asyncio
from typing import Any

from stargraph.tools.decorator import tool
from stargraph.tools.s3 import _client
from stargraph.tools.spec import SideEffects

__all__ = ["get_object"]

_MAX_BYTES = 1_048_576


@tool(
    name="get_object",
    namespace="s3",
    version="1",
    side_effects=SideEffects.read,
    requires_capability="tools:s3:read",
    description=(
        "Read an S3 object as UTF-8 text (decode-replace, capped at 1 MiB). "
        "Credentials via boto3's default chain; needs stargraph[tools-saas]."
    ),
)
async def get_object(bucket: str, key: str, max_bytes: int = _MAX_BYTES) -> dict[str, Any]:
    """Return ``{bucket, key, content, content_type, truncated}``."""
    cap = max(1, min(max_bytes, _MAX_BYTES))

    def _run() -> dict[str, Any]:
        s3 = _client.build_client()
        resp = s3.get_object(Bucket=bucket, Key=key)
        raw: bytes = resp["Body"].read(cap + 1)
        return {
            "bucket": bucket,
            "key": key,
            "content": raw[:cap].decode("utf-8", errors="replace"),
            "content_type": str(resp.get("ContentType", "")),
            "truncated": len(raw) > cap,
        }

    return await asyncio.to_thread(_run)
