# SPDX-License-Identifier: Apache-2.0
"""``email.send`` -- gated SMTP send with a deterministic Message-ID."""

from __future__ import annotations

import asyncio
import hashlib
import os
import smtplib
from email.message import EmailMessage
from typing import Any

from stargraph.tools import _saas
from stargraph.tools.decorator import tool
from stargraph.tools.spec import SideEffects

__all__ = ["send_email"]

_NAMESPACE = "email"


def _message_id(dedupe_key: str) -> str:
    digest = hashlib.sha256(dedupe_key.encode("utf-8")).hexdigest()[:32]
    return f"<stargraph-{digest}@stargraph.invalid>"


@tool(
    name="send",
    namespace=_NAMESPACE,
    version="1",
    side_effects=SideEffects.write,
    requires_capability="tools:email:write",
    description=(
        "Send a plain-text email over SMTP. Dry-run by default; set "
        "STARGRAPH_EMAIL_LIVE=1 to send. Needs SMTP_HOST/SMTP_USERNAME/"
        "SMTP_PASSWORD and a caller-supplied dedupe_key (drives a "
        "deterministic Message-ID so retries are detectable)."
    ),
)
async def send_email(
    to: str,
    subject: str,
    body: str,
    dedupe_key: str,
    cc: str | None = None,
) -> dict[str, Any]:
    """Send ``body`` to ``to`` (comma-separated); ``{status, message_id, to}``."""
    key = _saas.require_dedupe_key(dedupe_key, "email.send")
    message_id = _message_id(key)
    envelope = {
        "to": to,
        "cc": cc,
        "subject": subject,
        "message_id": message_id,
        "body_chars": len(body),
    }
    if not _saas.live_enabled(_NAMESPACE):
        return _saas.dry_run_envelope(_NAMESPACE, key, envelope)

    host = _saas.require_env("SMTP_HOST", tool="email.send")
    username = _saas.require_env("SMTP_USERNAME", tool="email.send")
    password = _saas.require_env("SMTP_PASSWORD", tool="email.send")
    port = int(os.environ.get("SMTP_PORT", "587"))
    sender = os.environ.get("SMTP_FROM", "").strip() or username
    starttls = os.environ.get("SMTP_STARTTLS", "1").strip().lower() in ("1", "true", "yes", "on")

    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = to
    if cc:
        msg["Cc"] = cc
    msg["Subject"] = subject
    msg["Message-ID"] = message_id
    msg.set_content(body)

    def _run() -> None:
        with smtplib.SMTP(host, port, timeout=30) as smtp:
            if starttls:
                smtp.starttls()
            smtp.login(username, password)
            smtp.send_message(msg)

    await asyncio.to_thread(_run)
    return {
        "status": "ok",
        "message_id": message_id,
        "to": to,
        "__stargraph_provenance__": {
            "origin": "tool",
            "source": _NAMESPACE,
            "external_id": message_id,
        },
    }
