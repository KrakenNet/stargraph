# SPDX-License-Identifier: Apache-2.0
"""pluginsmith — a Stargraph skill that builds Stargraph plugins from a brief.

A *composite* smith on the shared core (``stargraph.skills._smith``) that targets
the plugin archetype rather than a graph: it emits a single ``plugin.py`` carrying a
``@tool``-decorated callable plus pluggy ``@hookimpl`` functions — ``register_tools``
(advertise the tool), ``authorize_action`` (default-deny one action kind, abstain
otherwise — Bosun first-deny semantics), and ``before/after_tool_call`` audit hooks.
Its contract gate is the un-cheatable floor for a *plugin*: it registers the
generated module on a fresh, ISOLATED pluggy ``PluginManager`` (only the Stargraph
hookspecs, no entry-point discovery) and drives it FOR REAL — asserts
``register_tools`` advertises the declared tool, calls the tool and checks its
output, fires ``authorize_action`` and asserts it denies the deny-kind / abstains
otherwise, and fires the audit hooks. Because the asserts are on a live plugin
manager, a trivially-passing generated test cannot land a plugin that does not
register, compute, gate, or audit. Chaining smiths into a full build is the
orchestrator's job (Phase D), not pluginsmith's.
"""
