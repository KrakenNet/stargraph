# SPDX-License-Identifier: Apache-2.0
"""packsmith — a Stargraph skill that builds Bosun rule packs from a brief.

A *leaf* smith on the shared core (``stargraph.skills._smith``) targeting the Bosun
rule-pack archetype: it emits a governance pack — a ``rules.clp`` (CLIPS deftemplates +
defrules that read an input fact and assert a decision/action fact) plus the assembled
``pack.yaml`` + ``manifest.yaml`` descriptors and a test. Its contract gate is the
un-cheatable floor for a *rule pack*: it loads the rules into a real Fathom engine,
asserts the fixture's input fact, fires the engine, and asserts the rule produced the
expected action — then signs the assembled tree with an ephemeral key and verifies it
under a mandatory-verify profile, proving the pack coheres as a tree-hash-verifiable,
deployable unit. Because the asserts run against a live engine + the real signing path,
a trivially-passing test cannot land a pack whose rules don't compile, don't fire, or
don't cohere as a signable unit. Wiring the pack into a graph (a Fathom engine spec) is
the orchestrator's job (Phase D).
"""
