# SPDX-License-Identifier: Apache-2.0
"""Collision-avoidance burn-option geometry, ported from the upstream pipeline.

Sources (math intentionally IDENTICAL; imports/typing adapted only):

* the upstream CDM schema -- ``tri21_to_matrix`` / ``combined_pos_cov`` /
  ``PC_FLOOR`` (CCSDS 508 CDM covariance conventions).
* the upstream Pc module -- Foster-style encounter-plane Pc by vectorized
  polar quadrature.
* the upstream Kepler backend -- closed-form Clohessy-Wiltshire
  along-track impulse response + the post/pre Pc *ratio* convention (judged
  outcomes apply the geometric ratio to the recorded operational Pc;
  reconstructed absolute Pc runs ~1.2 dex hot on Kelvins, ratios transfer).

This module carries the F0 (training-toolchain-side) geometry the reference
MPC planner reasons with. It is deliberately NOT an evaluator: admission /
shield judgments stay with an independent backend (the upstream toolchain split).

numpy-only; ships under the ``rl`` extra surface (imported lazily via the
planner entry point, never at ``stargraph.rl`` import time).
"""

from __future__ import annotations

from typing import Any

import numpy as np

PC_FLOOR = 1e-30  # "negligible Pc" sentinel (Kelvins encodes it as risk = -30)


def tri21_to_matrix(tri: list[float]) -> np.ndarray:
    """Upper-triangular 21-list -> symmetric 6x6 RTN covariance matrix."""
    m = np.zeros((6, 6))
    k = 0
    for i in range(6):
        for j in range(i, 6):
            m[i, j] = m[j, i] = tri[k]
            k += 1
    return m


def combined_pos_cov(cdm: dict[str, Any]) -> np.ndarray:
    """Combined 3x3 position covariance of the pair (obj1 + obj2), RTN meters^2."""
    c1 = tri21_to_matrix(cdm["cov_obj1"])[:3, :3]
    c2 = tri21_to_matrix(cdm["cov_obj2"])[:3, :3]
    return c1 + c2


def maneuver_shift(dv_ms: float, direction: int, dt_s: float, n: float) -> np.ndarray:
    """RTN displacement at TCA of an along-track impulse ``direction*dv_ms`` applied
    ``dt_s`` seconds before TCA. Closed-form CW solution for a circular reference
    orbit with mean motion n (rad/s):
        dr(radial)      = 2 dv/n (1 - cos nt)
        dt(along-track) = dv (4 sin(nt)/n - 3 t)
        dn(cross-track) = 0
    """
    dv = float(direction) * float(dv_ms)
    nt = n * dt_s
    d_r = 2.0 * dv / n * (1.0 - np.cos(nt))
    d_t = dv * (4.0 * np.sin(nt) / n - 3.0 * dt_s)
    return np.array([d_r, d_t, 0.0])


def encounter_plane_basis(rel_vel: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Orthonormal basis of the plane perpendicular to the relative velocity."""
    v = np.asarray(rel_vel, dtype=float)
    vhat = v / np.linalg.norm(v)
    helper = np.array([1.0, 0.0, 0.0]) if abs(vhat[0]) < 0.9 else np.array([0.0, 1.0, 0.0])
    e1 = np.cross(vhat, helper)
    e1 /= np.linalg.norm(e1)
    e2 = np.cross(vhat, e1)
    return e1, e2


def project_to_plane(
    rel_pos: np.ndarray, rel_vel: np.ndarray, cov3: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """Project the 3D relative state + covariance onto the encounter plane."""
    e1, e2 = encounter_plane_basis(rel_vel)
    basis = np.vstack([e1, e2])  # 2x3
    mu = basis @ np.asarray(rel_pos, dtype=float)
    cov2 = basis @ np.asarray(cov3, dtype=float) @ basis.T
    return mu, cov2


def foster_pc(
    rel_pos: Any, rel_vel: Any, cov3: Any, hbr: float, n_r: int = 80, n_theta: int = 90
) -> float:
    """Integrate the projected 2D Gaussian over the disk of radius ``hbr`` centred on
    the primary (origin of the relative frame), polar grid, midpoint rule."""
    mu, cov2 = project_to_plane(np.asarray(rel_pos), np.asarray(rel_vel), np.asarray(cov3))
    # Guard degenerate covariance (wall should have caught it; fail toward Pc=1).
    det = float(np.linalg.det(cov2))
    if det <= 0:
        return 1.0
    inv = np.linalg.inv(cov2)
    norm = 1.0 / (2.0 * np.pi * np.sqrt(det))

    r_edges = np.linspace(0.0, hbr, n_r + 1)
    r_mid = 0.5 * (r_edges[:-1] + r_edges[1:])
    dr = r_edges[1] - r_edges[0]
    theta = np.linspace(0.0, 2.0 * np.pi, n_theta, endpoint=False)
    dtheta = 2.0 * np.pi / n_theta

    rr, tt = np.meshgrid(r_mid, theta, indexing="ij")
    x = rr * np.cos(tt) - mu[0]
    y = rr * np.sin(tt) - mu[1]
    quad = inv[0, 0] * x * x + 2.0 * inv[0, 1] * x * y + inv[1, 1] * y * y
    dens = norm * np.exp(-0.5 * quad)
    pc = float(np.sum(dens * rr) * dr * dtheta)
    return min(max(pc, 0.0), 1.0)


def pc_of(cdm: dict[str, Any], hbr: float, shift: np.ndarray | None = None) -> float:
    """Foster Pc of a CDM's reconstructed geometry, optionally after a burn shift."""
    rel_pos = np.asarray(cdm["rel_pos_rtn"], dtype=float)
    if shift is not None:
        rel_pos = rel_pos + shift
    cov3 = combined_pos_cov(cdm)
    return foster_pc(rel_pos, cdm["rel_vel_rtn"], cov3, hbr)


def pc_ratio(cdm: dict[str, Any], hbr: float, shift: np.ndarray) -> float:
    """Post/pre Pc ratio of a maneuver, from this toolchain's own geometry. The
    consumer applies the ratio to the CDM's OPERATIONAL Pc -- reconstructed
    absolute Pc runs ~1.2 dex hot vs the recorded value on Kelvins
    (HBR/covariance-scale mismatch), but the ratio transfers. No demonstrated
    reduction -> ratio 1 (conservative)."""
    pre = pc_of(cdm, hbr)
    if pre <= 0.0:
        return 1.0
    return pc_of(cdm, hbr, shift=shift) / pre
