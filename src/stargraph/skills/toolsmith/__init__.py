# SPDX-License-Identifier: Apache-2.0
"""toolsmith — a Stargraph skill that builds Stargraph tools from a brief.

The second instance of the shared smith core (``stargraph.skills._smith``): same
generate → gate → repair loop + ledger, with a tool-shaped plug-in (a
``@tool``-decorated callable bound to a ``ToolSpec``). Proves the core
generalizes beyond nodes.
"""
