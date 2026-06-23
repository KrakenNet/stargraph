# SPDX-License-Identifier: Apache-2.0
"""triggersmith — a Stargraph skill that builds Stargraph triggers from a brief.

Another instance of the shared smith core (``stargraph.skills._smith``): same
generate → gate → repair loop + ledger, with a trigger-shaped plug-in (a
zero-arg class implementing the ``init``/``start``/``stop``/``routes`` lifecycle
plus a synchronous ``enqueue`` that delegates to the scheduler). Targets the
**manual** trigger variant only (synchronous, offline; cron/webhook are future).
"""
