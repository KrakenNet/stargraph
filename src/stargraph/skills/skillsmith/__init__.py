# SPDX-License-Identifier: Apache-2.0
"""skillsmith — a Stargraph skill that builds whole Stargraph skills from a brief.

A *composite* smith on the shared core (``stargraph.skills._smith``), one rung
above graphsmith: where graphsmith emits a runnable graph bundle, skillsmith emits
a registerable **skill** — that same graph bundle plus a :class:`~stargraph.skills.base.Skill`
manifest (kind, description, capability ``requires``, optional ``system_prompt``,
and the declared output boundary the engine's ``SubGraphNode`` enforces). Its
contract gate is the un-cheatable floor for a *skill*: it LOADS the assembled
subgraph into a real :class:`~stargraph.graph.Graph` and RUNS it to completion on a
fixture (the graph works), AND constructs the ``Skill`` from the assembled manifest
(the manifest validates — valid kind, replay-safe state, no ``set`` fields) and
asserts the skill's declared output channels cover what the run produced. Chaining
smiths into a full build is the orchestrator's job (Phase D), not skillsmith's.
"""
