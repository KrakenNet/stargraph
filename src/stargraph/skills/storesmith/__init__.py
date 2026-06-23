# SPDX-License-Identifier: Apache-2.0
"""storesmith — a Stargraph skill that builds Stargraph stores from a brief.

Another instance of the shared smith core (``stargraph.skills._smith``): same
generate → gate → repair loop + ledger, with a store-shaped plug-in (a single
class implementing the ``DocStore`` protocol, gated by exercising it for real on
a tmpfile sqlite DB). Proves the core generalizes to stateful artifacts.
"""
