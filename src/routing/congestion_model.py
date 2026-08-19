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

    Refit 2026-08-19 on 195 real samples (one 1368ms/u=0.024 sample excluded
    as an unambiguous outlier -- removing it alone shifted scale_ms by ~16%,
    see compliance_check.md) spanning 4 real links, u in [0.006, 0.782] --
    the first real coverage above ~0.55, added specifically because the
    previous fit (scale_ms=113.603, BASELINE_DELAY_MS=6.0, fit 2026-08-12 on
    60 samples capped at u~0.55) had zero real evidence past that point.
    Used Theil-Sen (median of pairwise slopes) instead of OLS: the "low-u"
    samples turned out not to be a clean baseline (12-443ms spread even below
    u=0.25, likely Mininet/VM scheduling jitter under concurrent iperf+ping),
    and OLS/mean-based fitting is not robust to that kind of noise the way
    Theil-Sen's median is.

    BASELINE_DELAY_MS moved from a hand-picked 6.0 to the fitted 34.062 --
    higher than the topology's configured 10ms propagation delay alone would
    suggest, consistent with real per-sample overhead (host stack, ARP,
    virtual-switch forwarding, Mininet scheduling) that a pure wire-delay
    number never captured.

    Important caveat carried forward, not resolved: of the 23 real samples
    with u>0.6, 22 come from a single link (s5-s14, the low-capacity link
    added specifically to reach this range -- see
    scripts/mininet_independence_check.py). The fit's high-utilization shape
    therefore rests on one real link's behavior, not a cross-link average --
    cannot yet distinguish "delay genuinely steepens faster than u/(1-u)
    predicts at high u" from "this specific link's queueing/buffer sizing is
    idiosyncratic". Even after this refit (using the more robust Theil-Sen
    estimator), real delay for u>0.6 still runs systematically above the
    curve's prediction (median residual +108.8ms, vs -6.6ms for u<=0.6;
    dCor(u, residual)=0.347, p=0.0001, still significant) -- refitting
    reduced but did not eliminate this. Documented as a known, quantified
    limitation rather than further tuned; the confound above means further
    curve tweaking without more real, cross-link high-u data would likely
    just be overfitting to one link's idiosyncrasies.

    MAX_UTILIZATION_FOR_EXTRAPOLATION widened from 0.6 (the previous data's
    ceiling) to 0.75 (just under this fit's real ceiling of 0.782) for the
    same reason as before: extrapolating past any real evidence is what
    caused the earlier negative-edge-weight bug (see compliance_check.md).
    GraphBuilder._calculate_edge_cost's unconditional MIN_EDGE_COST floor
    remains the correctness backstop regardless of this curve's calibration.
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
