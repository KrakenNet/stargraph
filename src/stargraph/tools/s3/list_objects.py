# SPDX-License-Identifier: Apache-2.0
"""``s3.list_objects`` -- list keys under a prefix."""

from __future__ import annotations

import asyncio
from typing import Any

from stargraph.tools.decorator import tool
from stargraph.tools.s3 import _client
from stargraph.tools.spec import SideEffects

__all__ = ["list_objects"]

_MAX_LIMIT = 1000


@tool(
    name="list_objects",
    namespace="s3",
    version="1",
    side_effects=SideEffects.read,
    requires_capability="tools:s3:read",
    description=(
        "List S3 object keys under a prefix (list_objects_v2). Credentials "
        "via boto3's default chain; needs stargraph[tools-saas]."
    ),
)
async def list_objects(bucket: str, prefix: str = "", limit: int = 100) -> dict[str, Any]:
    """Return ``{bucket, prefix, objects: [{key, size, last_modified}], truncated}``."""
    cap = max(1, min(limit, _MAX_LIMIT))

    def _run() -> dict[str, Any]:
        s3 = _client.build_client()
        resp = s3.list_objects_v2(Bucket=bucket, Prefix=prefix, MaxKeys=cap)
        objects = [
            {
                "key": str(item.get("Key", "")),
                "size": int(item.get("Size", 0)),
                "last_modified": item["LastModified"].isoformat()
                if item.get("LastModified") is not None
                else "",
            }
            for item in resp.get("Contents", [])
        ]
        return {
            "bucket": bucket,
            "prefix": prefix,
            "objects": objects,
            "truncated": bool(resp.get("IsTruncated", False)),
        }

    return await asyncio.to_thread(_run)
