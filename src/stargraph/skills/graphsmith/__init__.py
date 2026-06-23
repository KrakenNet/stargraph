# SPDX-License-Identifier: Apache-2.0
"""graphsmith — a Stargraph skill that builds whole Stargraph graphs from a brief.

The first *composite* smith: where the leaf smiths (node/tool/store/trigger/adapter)
each emit one artifact, graphsmith emits a runnable multi-file **bundle** — a
``State`` model, one or more ``NodeBase`` classes, and the ``graph.yaml`` wiring —
on the same shared core (``stargraph.skills._smith``): same generate → gate →
repair loop + ledger. Its contract gate is the un-cheatable floor for a *graph*:
it LOADS the assembled bundle into a real :class:`~stargraph.graph.Graph` and RUNS
it to completion on a fixture, asserting the nodes wired end-to-end and produced
the expected output. Chaining smiths into a full build is the orchestrator's job
(Phase D), not graphsmith's.
"""
