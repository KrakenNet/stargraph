# SPDX-License-Identifier: Apache-2.0
"""``triggers.yaml`` loader.

Reads ``<config_dir>/triggers.yaml`` and translates the convention shape
into typed :class:`~harbor.triggers.cron.CronSpec` /
:class:`~harbor.triggers.webhook.WebhookSpec` instances plus a list of
manual-trigger descriptors (the ManualTrigger plugin holds no per-spec
state, but operators want them in the YAML for discoverability).

The convention shape (mirrors the ``everything-demo``
``triggers.yaml``)::

    version: "1.0"
    manual:
      - id: manual.replay
        graph_id: graph:sdw-main
        description: ...
    cron:
      - id: cron.daily_anchor
        graph_id: graph:sdw-audit-anchor
        expr: "0 3 * * *"
        tz: UTC
        missed_fire_policy: fire_once_catchup
        params: {trigger_kind: cron}
        description: ...
    webhook:
      - id: webhook.feed
        graph_id: graph:sdw-main
        path: /triggers/feed
        current_secret_env: WEBHOOK_SECRET_CURRENT
        previous_secret_env: WEBHOOK_SECRET_PREVIOUS
        timestamp_window_seconds: 300
        nonce_lru_size: 10000
        params_extractor: json
        description: ...

Translations applied:

  * ``id``                  → ``trigger_id`` (CronSpec / WebhookSpec)
  * ``expr``                → ``cron_expression`` (CronSpec)
  * ``current_secret_env``  → resolved env var → ``current_secret`` (bytes)
  * ``previous_secret_env`` → resolved env var → ``previous_secret`` (bytes)
  * ``description`` field is dropped (operator-facing prose, not a spec field)
  * ``params_extractor: "json"`` is the default — passed through as a sentinel
    string; webhook plugin accepts a callable, the lifespan factory binds
    the concrete extractor (Phase E follow-up).

Field-name mismatches between the YAML convention and the spec class
fail loud at :meth:`pydantic.BaseModel.model_validate` time.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from harbor.errors import HarborRuntimeError
from harbor.triggers.cron import CronSpec
from harbor.triggers.webhook import WebhookSpec

__all__ = [
    "LoadedTriggers",
    "ManualDescriptor",
    "load_triggers",
]


@dataclass(frozen=True)
class ManualDescriptor:
    """Documentation-only record for a manual trigger entry.

    The :class:`~harbor.triggers.manual.ManualTrigger` plugin is
    stateless; the YAML rows exist purely to enumerate the surfaces
    operators (and ``harbor run``) can fire.
    """

    trigger_id: str
    graph_id: str
    description: str = ""


@dataclass
class LoadedTriggers:
    """Parsed result of :func:`load_triggers`."""

    cron_specs: list[CronSpec] = field(default_factory=list)
    webhook_specs: list[WebhookSpec] = field(default_factory=list)
    manual_descriptors: list[ManualDescriptor] = field(default_factory=list)
    version: str = "1.0"


# ---------------------------------------------------------------------------
# Translation helpers
# ---------------------------------------------------------------------------

# Set of keys we drop before model_validate — purely documentational.
_DOC_KEYS = {"description"}


def _translate_cron_row(row: dict[str, Any]) -> dict[str, Any]:
    """Map convention keys to :class:`CronSpec` field names."""
    out: dict[str, Any] = {}
    for k, v in row.items():
        if k in _DOC_KEYS:
            continue
        if k == "id":
            out["trigger_id"] = v
        elif k == "expr":
            out["cron_expression"] = v
        else:
            out[k] = v
    return out


def _resolve_env(env_name: str | None, *, required: bool) -> bytes:
    """Resolve an env-var reference to bytes; empty bytes when optional+unset."""
    if not env_name:
        if required:
            raise HarborRuntimeError(
                "webhook trigger missing current_secret_env reference"
            )
        return b""
    val = os.environ.get(env_name)
    if val is None or val == "":
        if required:
            raise HarborRuntimeError(
                f"webhook trigger requires env var {env_name!r} but it is unset"
            )
        return b""
    return val.encode("utf-8")


def _translate_webhook_row(row: dict[str, Any]) -> dict[str, Any]:
    """Map convention keys (incl. *_secret_env env refs) to WebhookSpec fields."""
    out: dict[str, Any] = {}
    current_env = None
    previous_env = None
    for k, v in row.items():
        if k in _DOC_KEYS:
            continue
        if k == "id":
            out["trigger_id"] = v
        elif k == "current_secret_env":
            current_env = v
        elif k == "previous_secret_env":
            previous_env = v
        elif k == "params_extractor":
            # Sentinel string handed through — the lifespan factory binds
            # the concrete extractor (phase E follow-up). WebhookSpec has
            # no string-extractor field today; drop unless callable.
            if callable(v):
                out["params_extractor"] = v
        else:
            out[k] = v
    out["current_secret"] = _resolve_env(current_env, required=True)
    prev = _resolve_env(previous_env, required=False)
    if prev:
        out["previous_secret"] = prev
    return out


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def load_triggers(config_dir: Path | str) -> LoadedTriggers:
    """Load ``<config_dir>/triggers.yaml`` and return typed specs.

    A missing file returns an empty :class:`LoadedTriggers` (caller
    decides whether that is an error). Schema/translation failures
    raise :class:`HarborRuntimeError` with the offending row id when
    available so operators can fix the YAML directly.
    """
    cfg_dir = Path(config_dir)
    yaml_path = cfg_dir / "triggers.yaml"
    if not yaml_path.is_file():
        return LoadedTriggers()

    try:
        doc = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise HarborRuntimeError(f"triggers.yaml parse error: {exc}") from exc
    if not isinstance(doc, dict):
        raise HarborRuntimeError(
            f"triggers.yaml top-level must be a mapping, got {type(doc).__name__}"
        )

    out = LoadedTriggers(version=str(doc.get("version", "1.0")))

    for row in doc.get("manual", []) or []:
        if not isinstance(row, dict):
            raise HarborRuntimeError(f"triggers.yaml manual entry not a mapping: {row!r}")
        out.manual_descriptors.append(
            ManualDescriptor(
                trigger_id=str(row["id"]),
                graph_id=str(row["graph_id"]),
                description=str(row.get("description", "")),
            )
        )

    for row in doc.get("cron", []) or []:
        if not isinstance(row, dict):
            raise HarborRuntimeError(f"triggers.yaml cron entry not a mapping: {row!r}")
        translated = _translate_cron_row(row)
        try:
            out.cron_specs.append(CronSpec.model_validate(translated))
        except ValidationError as exc:
            raise HarborRuntimeError(
                f"triggers.yaml cron[{row.get('id', '?')}] invalid: {exc}"
            ) from exc

    for row in doc.get("webhook", []) or []:
        if not isinstance(row, dict):
            raise HarborRuntimeError(f"triggers.yaml webhook entry not a mapping: {row!r}")
        translated = _translate_webhook_row(row)
        try:
            out.webhook_specs.append(WebhookSpec.model_validate(translated))
        except ValidationError as exc:
            raise HarborRuntimeError(
                f"triggers.yaml webhook[{row.get('id', '?')}] invalid: {exc}"
            ) from exc

    return out
