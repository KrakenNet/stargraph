# SPDX-License-Identifier: Apache-2.0
"""adaptersmith — a Stargraph skill that builds Stargraph adapters from a brief.

A leaf instance of the shared smith core (``stargraph.skills._smith``): same
generate → gate → repair loop + ledger, with an adapter-shaped plug-in (a module
exposing async ``bind`` + ``call_tool`` over an external-runtime seam). Proves
the core generalizes to artifacts that have no base class — discovery finds the
two async functions, not a class.
"""
