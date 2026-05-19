# SPDX-License-Identifier: Apache-2.0
"""cve_rem.offline_isolation — governance pack entry-point marker.

Loaded by Phase 6 offline_learning graph. Enforces network-isolation
invariants: no inbound from production, egress only on signed prompts.tar
drop socket, redaction-pack-hash match at replica boundary.
"""
