# SPDX-License-Identifier: Apache-2.0
"""Shared "smith" core — the domain-agnostic machinery behind every generator.

A *smith* turns a natural-language brief into a gate-verified stargraph artifact
(node, tool, store, …) via a bounded generate→gate→repair loop, and improves at
it over time off an append-only ledger. nodesmith was the first; this package is
the reusable spine each new smith plugs into so they don't re-implement it.

Generic here (reused verbatim): LM plumbing + clarify (``lm``), web research
(``web``), RAG retrieval primitives (``retrieval``). Domain-specific bits — the
DSPy signature, the gate/verifier (the heart), seeds, and the corpus — are
supplied per smith.
"""
