# SPDX-License-Identifier: Apache-2.0
"""``email.fetch`` -- read recent messages over IMAP (peek, no flag mutation)."""

from __future__ import annotations

import asyncio
import email as email_stdlib
import email.header
import imaplib
import os
from typing import Any

from stargraph.errors import StargraphRuntimeError
from stargraph.tools import _saas
from stargraph.tools.decorator import tool
from stargraph.tools.spec import SideEffects

__all__ = ["fetch_email"]

_MAX_LIMIT = 50
_SNIPPET_CHARS = 500


def _decode_header(raw: str) -> str:
    parts = email.header.decode_header(raw)
    out: list[str] = []
    for value, charset in parts:
        if isinstance(value, bytes):
            out.append(value.decode(charset or "utf-8", errors="replace"))
        else:
            out.append(value)
    return "".join(out)


def _snippet(msg: Any) -> str:
    """First text/plain part, decoded, capped at ``_SNIPPET_CHARS``."""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                payload = part.get_payload(decode=True)
                if isinstance(payload, bytes):
                    charset = part.get_content_charset() or "utf-8"
                    return payload.decode(charset, errors="replace")[:_SNIPPET_CHARS]
        return ""
    payload = msg.get_payload(decode=True)
    if isinstance(payload, bytes):
        charset = msg.get_content_charset() or "utf-8"
        return payload.decode(charset, errors="replace")[:_SNIPPET_CHARS]
    return ""


@tool(
    name="fetch",
    namespace="email",
    version="1",
    side_effects=SideEffects.read,
    requires_capability="tools:email:read",
    description=(
        "Fetch recent emails over IMAP (BODY.PEEK -- never mutates seen "
        "flags). Needs IMAP_HOST/IMAP_USERNAME/IMAP_PASSWORD."
    ),
)
async def fetch_email(
    mailbox: str = "INBOX",
    limit: int = 5,
    unseen_only: bool = False,
) -> dict[str, Any]:
    """Newest-first ``{mailbox, messages: [{subject, from, date, snippet}]}``."""
    host = _saas.require_env("IMAP_HOST", tool="email.fetch")
    username = _saas.require_env("IMAP_USERNAME", tool="email.fetch")
    password = _saas.require_env("IMAP_PASSWORD", tool="email.fetch")
    port = int(os.environ.get("IMAP_PORT", "993"))
    cap = max(1, min(limit, _MAX_LIMIT))

    def _run() -> list[dict[str, str]]:
        with imaplib.IMAP4_SSL(host, port) as imap:
            imap.login(username, password)
            status, _ = imap.select(mailbox, readonly=True)
            if status != "OK":
                raise StargraphRuntimeError(
                    f"email.fetch: cannot select mailbox {mailbox!r}",
                    mailbox=mailbox,
                )
            criteria = "UNSEEN" if unseen_only else "ALL"
            status, data = imap.search(None, criteria)
            if status != "OK":
                raise StargraphRuntimeError(
                    f"email.fetch: IMAP search failed for {criteria!r}",
                    mailbox=mailbox,
                )
            ids = data[0].split()
            out: list[dict[str, str]] = []
            for msg_id in reversed(ids[-cap:]):
                status, fetched = imap.fetch(msg_id, "(BODY.PEEK[])")
                if status != "OK" or not fetched or not isinstance(fetched[0], tuple):
                    continue
                msg = email_stdlib.message_from_bytes(fetched[0][1])
                out.append(
                    {
                        "subject": _decode_header(msg.get("Subject", "")),
                        "from": _decode_header(msg.get("From", "")),
                        "date": str(msg.get("Date", "")),
                        "snippet": _snippet(msg),
                    }
                )
            return out

    messages = await asyncio.to_thread(_run)
    return {"mailbox": mailbox, "messages": messages}
