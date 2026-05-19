# SPDX-License-Identifier: Apache-2.0
"""cve_rem.gepa_score_policy — governance pack entry-point marker.

Loaded by Phase 6 offline_learning graph. Computes the weighted GEPA
score from per-component facts, gates artifact promotion on a
strictly-better epsilon margin, and emits halt-severity violations on
policy breach (e.g., score-component out of range, weights drift).
"""
