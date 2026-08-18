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

BASELINE_DELAY_MS = 6.0
BASELINE_LOSS = 0.001


def congestion_delay_bump_ms(utilization: float, scale_ms: float = 113.603) -> float:
    """
    M/M/1-inspired queueing delay: grows as utilization/(1-utilization), which
    diverges near saturation -- a real, well-known queueing-theory shape.

    scale_ms fit 2026-08-12 by ordinary least squares (delay - BASELINE_DELAY_MS
    = scale_ms * u/(1-u)) against 60 real samples from scripts/
    mininet_independence_check.py (results/independence_check/independence_samples.csv,
    3 real links, randomized order). The previous value (8.0) was hand-picked,
    never fit to data, and left a real, statistically significant correlation
    between utilization and the delay *residual* (Spearman rho=0.433,
    p=0.0010 -- meaning beta was still partly pricing utilization a second
    time, undermining the point of the residual fix). Refitting this one
    constant dropped the residual-vs-utilization Pearson correlation from
    0.495 to -0.084 on the same data. Still a *chosen functional form*
    (a different queueing discipline would give a different shape), and
    calibrated to this project's own Mininet testbed (uniformly configured
    at 100Mbit per link in topology.py) rather than GEANT's real, per-link
    capacities (see compliance_check.md -- e.g. real link s5-s6 is
    officially 10Gbps per data/Geant2012.graphml's LinkLabel, tested here at
    100Mbit) -- appropriate for predicting this testbed's own behavior, not
    a claim about real GEANT queueing at real capacities.

    The 60 real samples never exceeded ~0.55 achieved utilization (a separate,
    still-open limitation -- see compliance_check.md on the iperf generation
    ceiling), so this fit has no real evidence above that point. u/(1-u)
    diverges hyperbolically as u->1, and with this much larger scale_ms than
    the previous hand-picked value, extrapolating the fit up to the old
    clamp (0.99) produced absurd values (11252ms at u=0.99) that, in one real
    scenario, made a background link's delay_residual negative enough to push
    a whole edge's cost below zero -- networkx's Dijkstra correctly refuses
    negative weights. Clamped at MAX_UTILIZATION_FOR_EXTRAPOLATION (0.6, just
    above the real data's range) instead of 0.99: past that point this
    function holds its value at the u=0.6 plateau rather than continuing to
    extrapolate into untested territory. GraphBuilder._calculate_edge_cost
    also floors the final cost at a small positive epsilon independently of
    this -- an edge cost should never be able to go negative regardless of
    how well any curve is calibrated, that's a correctness property, not a
    calibration one.
    """
    MAX_UTILIZATION_FOR_EXTRAPOLATION = 0.6
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
