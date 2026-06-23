# SPDX-License-Identifier: Apache-2.0
"""foundry — the orchestrator stargraph that builds a whole Stargraph from a brief.

The capstone of the smith family. Where each smith builds ONE artifact, the
foundry is itself a runnable Stargraph graph (``plan → execute → assemble →
verify``) that turns a natural-language build request into a complete, running
Stargraph:

1. **plan** — an LLM planner decomposes the request into a typed *build manifest*
   (:mod:`stargraph.skills.foundry.manifest`): exactly one ``graph`` spine plus
   any number of capabilities (store / pack / ml / tool / …).
2. **execute** — a deterministic executor drives each manifest item through its
   smith's own lifecycle (triage → recall → build → record), landing a real,
   gate-passing artifact for each (:mod:`stargraph.skills.foundry.dispatch`).
3. **assemble** — the spine bundle becomes the runnable graph dir; each built
   capability is mounted beside it and recorded in an ``assembly.yaml`` manifest
   (:mod:`stargraph.skills.foundry.assemble`).
4. **verify** — the assembled graph is actually RUN to a terminal ``done`` state
   (the same un-cheatable run contract every graph-running smith uses), so the
   foundry only reports success when the built stargraph really runs end-to-end.

The foundry composes coherent-by-construction pieces: the spine is a whole graph
graphsmith already proves runnable, and each capability is independently gated by
its own smith. Full *semantic* wiring (a generated node invoking a generated
store) is out of scope — capabilities are built, mounted, and registered, and the
spine provably runs.
"""
