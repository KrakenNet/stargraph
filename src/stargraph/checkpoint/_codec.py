# SPDX-License-Identifier: Apache-2.0
"""orjson JSONB codec helpers shared across checkpoint drivers (design §3.2.4).

Two flavors:

* :func:`dumps_jsonb` / :func:`loads_jsonb` -- driver-agnostic ``bytes`` codec
  used by the SQLite driver to serialize ``state_snapshot``, ``clips_facts``,
  and ``next_action`` into BLOB columns. ``None`` round-trips to ``None``.
* :func:`_init_jsonb_codec` -- asyncpg connection ``init`` hook for the
  Postgres driver (Phase 3, design §3.2.4 / FR-18). Registers ``orjson.dumps``
  / ``orjson.loads`` against the ``pg_catalog.jsonb`` type.

  .. note::
     The codec uses ``format='text'``. asyncpg's ``format='binary'`` is
     **broken for jsonb** -- see asyncpg issue #623. The binary wire format
     for jsonb prepends a single ``\\x01`` version byte that the codec
     machinery does not strip, so ``orjson.loads`` chokes on a leading
     non-JSON byte. Stay on text until upstream ships a fix.

This module is private (``_codec``); drivers re-export only what they need.
"""

from __future__ import annotations

from typing import Any

import orjson

__all__ = ["_init_jsonb_codec", "dumps_jsonb", "loads_jsonb"]


def dumps_jsonb(value: Any) -> bytes:
    """orjson-encode ``value`` to ``bytes`` for BLOB / jsonb storage."""
    return orjson.dumps(value)


def loads_jsonb(blob: bytes | None) -> Any:
    """orjson-decode a BLOB / jsonb payload; ``None`` round-trips to ``None``."""
    if blob is None:
        return None
    return orjson.loads(blob)


def _jsonb_text_encoder(value: Any) -> str:
    """orjson-encode ``value`` to ``str`` for asyncpg text-format jsonb."""
    return orjson.dumps(value).decode("utf-8")


def _jsonb_text_decoder(payload: str) -> Any:
    """orjson-decode a text-format jsonb payload (asyncpg hands us ``str``)."""
    return orjson.loads(payload)


async def _init_jsonb_codec(conn: Any) -> None:
    """Register orjson as the asyncpg codec for ``pg_catalog.jsonb``.

    Wired via ``asyncpg.create_pool(init=_init_jsonb_codec, ...)`` by the
    Phase 3 Postgres driver (design §3.2.4). ``format='text'`` is mandatory
    -- ``format='binary'`` is broken for jsonb (asyncpg #623): the binary
    wire format prepends a single ``\\x01`` version byte that the codec
    machinery does not strip, so ``orjson.loads`` chokes on a leading
    non-JSON byte. Stay on text until upstream ships a fix.

    With ``format='text'`` asyncpg expects the encoder to return :class:`str`
    and hands the decoder a :class:`str`; ``orjson.dumps`` returns
    :class:`bytes`, so we wrap it with ``.decode('utf-8')``.

    ``conn`` is typed ``Any`` because asyncpg ships no type stubs (PEP 561)
    -- importing ``asyncpg.Connection`` would surface ``reportMissingTypeStubs``
    on every consumer. Phase 3's Postgres driver pins the concrete type at
    its call site.
    """
    await conn.set_type_codec(  # pyright: ignore[reportUnknownMemberType]
        "jsonb",
        encoder=_jsonb_text_encoder,
        decoder=_jsonb_text_decoder,
        schema="pg_catalog",
        format="text",
    )
