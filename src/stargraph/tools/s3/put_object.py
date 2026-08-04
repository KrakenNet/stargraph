# SPDX-License-Identifier: Apache-2.0
"""``s3.put_object`` -- gated text upload."""

from __future__ import annotations

import asyncio
from typing import Any

from stargraph.tools import _saas
from stargraph.tools.decorator import tool
from stargraph.tools.s3 import _client
from stargraph.tools.spec import SideEffects

__all__ = ["put_object"]

_NAMESPACE = "s3"


@tool(
    name="put_object",
    namespace=_NAMESPACE,
    version="1",
    side_effects=SideEffects.write,
    requires_capability="tools:s3:write",
    description=(
        "Write UTF-8 text to an S3 object. Dry-run by default; set "
        "STARGRAPH_S3_LIVE=1 to upload. The object key is the idempotency "
        "key: retrying the same put converges. Needs stargraph[tools-saas]."
    ),
)
async def put_object(
    bucket: str,
    key: str,
    content: str,
    content_type: str = "text/plain",
) -> dict[str, Any]:
    """Upload ``content``; returns ``{status, bucket, key, bytes_written}``."""
    data = content.encode("utf-8")
    if not _saas.live_enabled(_NAMESPACE):
        return _saas.dry_run_envelope(
            _NAMESPACE,
            f"{bucket}/{key}",
            {"bucket": bucket, "key": key, "bytes": len(data), "content_type": content_type},
        )

    def _run() -> dict[str, Any]:
        s3 = _client.build_client()
        s3.put_object(Bucket=bucket, Key=key, Body=data, ContentType=content_type)
        return {
            "status": "ok",
            "bucket": bucket,
            "key": key,
            "bytes_written": len(data),
            "__stargraph_provenance__": {
                "origin": "tool",
                "source": _NAMESPACE,
                "external_id": f"s3://{bucket}/{key}",
            },
        }

    return await asyncio.to_thread(_run)
