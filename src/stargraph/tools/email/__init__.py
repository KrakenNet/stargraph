# SPDX-License-Identifier: Apache-2.0
"""``stargraph.tools.email`` -- SMTP/IMAP tools (namespace ``email``).

| tool | capability | side effects |
|---|---|---|
| ``email.send@1`` | ``tools:email:write`` | write (dry-run by default) |
| ``email.fetch@1`` | ``tools:email:read`` | read |

Pure stdlib (``smtplib``/``imaplib``, run in a worker thread) -- no
extra dependency. Connection config comes from env:

* send: ``SMTP_HOST``, ``SMTP_PORT`` (587), ``SMTP_USERNAME``,
  ``SMTP_PASSWORD``, ``SMTP_FROM`` (defaults to username),
  ``SMTP_STARTTLS`` (default on).
* fetch: ``IMAP_HOST``, ``IMAP_PORT`` (993, SSL), ``IMAP_USERNAME``,
  ``IMAP_PASSWORD``.

Writes follow the SaaS safety boundaries (:mod:`stargraph.tools._saas`):
dry-run unless ``STARGRAPH_EMAIL_LIVE`` is truthy, and the required
``dedupe_key`` derives a deterministic ``Message-ID`` so a retried send
is detectable (and deduped by receivers that honor Message-ID).
``fetch`` uses ``BODY.PEEK`` so reading never mutates seen-flags.
"""

from __future__ import annotations

from stargraph.tools.email.fetch import fetch_email
from stargraph.tools.email.send import send_email

__all__ = ["fetch_email", "send_email"]
