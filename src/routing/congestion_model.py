#!/usr/bin/env python3
"""
Congestion Model - shared utilization -> expected delay/loss curves.

Used in two places that need to agree with each other: (1) the offline
simulator (experiments/simulation_common.py) derives synthetic delay/loss
from utilization so pilot scenarios stay internally consistent, and (2) the
real cost formula (GraphBuilder._calculate_edge_cost) uses the same curves
to compute how much of a link's *measured* delay/loss is already explained
by its utilization, so utilization isn't counted three times over (once
directly via alpha, then again via beta/gamma on values that are themselves
mostly a function of utilization in a real network).
"""
from __future__ import annotations

BASELINE_DELAY_MS = 34.062
BASELINE_LOSS = 0.001


def congestion_delay_bump_ms(utilization: float, scale_ms: float = 120.174) -> float:
    """
    M/M/1-inspired queueing delay: grows as utilization/(1-utilization), which
    diverges near saturation -- a real, well-known queueing-theory shape.

    Theil-Sen fit (robust to outliers, unlike OLS) on 195 real samples across
    4 links, u in [0.006, 0.782]. BASELINE_DELAY_MS=34.062 is the fitted
    intercept, not a hand-picked wire-delay number -- it absorbs real
    per-sample overhead (host stack, ARP, virtual-switch forwarding,
    scheduling) that a pure propagation-delay figure wouldn't capture.

    Known, quantified limitation, not yet resolved: 22 of the 23 real
    samples with u>0.6 come from a single link, so the fit's high-
    utilization shape reflects that one link's behavior more than a
    cross-link average -- real delay for u>0.6 still runs systematically
    above this curve's prediction (median residual +108.8ms). Further
    tuning without more real, cross-link high-u data would likely overfit
    to that one link's idiosyncrasies rather than improve the curve.

    Input is clamped at MAX_UTILIZATION_FOR_EXTRAPOLATION (just under this
    fit's real evidence ceiling) -- extrapolating past real evidence is how
    this curve previously produced a negative edge weight.
    GraphBuilder._calculate_edge_cost's MIN_EDGE_COST floor is the
    correctness backstop regardless of this curve's calibration.
    """
    MAX_UTILIZATION_FOR_EXTRAPOLATION = 0.75
    u = min(max(utilization, 0.0), MAX_UTILIZATION_FOR_EXTRAPOLATION)
    return scale_ms * (u / (1.0 - u))


def congestion_loss_bump(utilization: float, onset: float = 0.7, scale: float = 0.05) -> float:
    """
    Loss stays ~0 below `onset` (buffers absorb bursts up to that point), then
    rises quadratically toward `scale` as utilization approaches 1 -- a common
    simplified heuristic for finite-buffer overflow probability near
    saturation, not derived from a specific queueing model like the delay
    curve above.
    """
    excess = max(0.0, utilization - onset)
    return scale * (excess / (1.0 - onset)) ** 2


def predicted_delay_ms(utilization: float) -> float:
    """Expected delay for a link purely as a function of its utilization."""
    return BASELINE_DELAY_MS + congestion_delay_bump_ms(utilization)


def predicted_loss(utilization: float) -> float:
    """Expected packet loss for a link purely as a function of its utilization."""
    return BASELINE_LOSS + congestion_loss_bump(utilization)
