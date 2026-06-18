# SPDX-License-Identifier: Apache-2.0
"""Nodesmith — a Stargraph graph that builds Stargraph nodes.

Generate → verify (static / contract / tests) → repair-loop → record.
The verify gate is the contract: a node is never recorded unless it
imports, constructs, runs ``execute`` on its declared fixture, and passes
its own test. Gate-passing builds accumulate as ``(spec → node)`` training
pairs for offline DSPy optimization (``scripts/nodesmith_optimize.py``);
failures accumulate as reflexion lessons recalled before the next build.
"""
