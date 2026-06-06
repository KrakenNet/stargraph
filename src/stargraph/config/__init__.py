# SPDX-License-Identifier: Apache-2.0
"""Boot-time config loaders for Stargraph (config-dir → typed specs).

Today: ``triggers.py`` reads ``<config_dir>/triggers.yaml`` and returns
typed :class:`~stargraph.triggers.cron.CronSpec` /
:class:`~stargraph.triggers.webhook.WebhookSpec` lists plus a manual-trigger
descriptor list. The lifespan factory wires those into
``deps={cron_specs, webhook_specs, manual_descriptors, scheduler}`` for
:func:`stargraph.plugin.triggers_dispatcher.dispatch_trigger_lifecycle`.
"""

from __future__ import annotations

from stargraph.config.triggers import (
    LoadedTriggers,
    ManualDescriptor,
    load_triggers,
)

__all__ = [
    "LoadedTriggers",
    "ManualDescriptor",
    "load_triggers",
]
