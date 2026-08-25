#!/usr/bin/env python3
"""
Pareto Weight Analysis - "path 2" of the weight-tuning arc in
compliance_check.md's "Which optimizer actually fits this formula" /
"A method actually designed for this structure" sections.

Background: DIRECT's own headline recommendation (found by
weight_search_comparison.py) turned out to be the search space's exact
geometric center (all 6 searched weights = 0.5) -- not a meaningfully
refined result, just DIRECT's mandatory first evaluation happening to
already sit inside the benchmark's large (68% of the space) zero-regret
region. A real-scenario check with the production GraphBuilder then found
that vector prefers a heavily-congested-but-stable link over a
lightly-loaded-but-recently-churned one -- the opposite of what production
weights (and, it turns out, the benchmark's own equal-weight "ground
truth") were built to encourage.

Tried "path 1" first (unit normalization -- rescale delay_residual_ms and
loss_residual onto comparable [0,1]-ish ranges before weighting, since the
raw formula's beta/gamma terms use physically different units than
alpha/delta/zeta/eta). Checked directly: it made no difference for the
utilization-vs-churn conflict specifically, because utilization and
churn_score are *already* both properly range-normalized to [0,1] (one
because it's a physical fraction of link capacity, the other via
LinkChurnTracker's own saturation_count cap) -- there's no unit mismatch
to fix there. The real disagreement is a relative-*importance* judgment
(is a churn event worth 0.4 units of utilization or not?), which no amount
of rescaling can resolve, since churn_score's saturation_count=5 anchor is
itself an unvalidated default, not a real measured "equivalent severity."

This module is "path 2": rather than assert one arbitrary weight vector as
correct (which is what both the original CURRENT_DEFAULTS and DIRECT's raw
recommendation implicitly do), report the *Pareto frontier* -- for a set of
genuine trade-off scenarios, whether one candidate path actually dominates
the other on every raw dimension (in which case there's no real conflict
and no weight choice matters), or whether they're mutually non-dominated
(a real trade-off), and for the latter, sweep the weight simplex to
characterize *which weight regime prefers which path* and where the
boundary sits -- instead of forcing a single number.

Run as: python3 -m experiments.pareto_weight_analysis
"""
from __future__ import annotations

import csv
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np

from src.monitor.models import LinkStatistics
from src.monitor.network_state import NetworkState
from src.routing.congestion_model import predicted_delay_ms, predicted_loss
from src.routing.graph_builder import GraphBuilder

from .weight_search_comparison import PARAM_ORDER, normalize_weights

OUTPUT_DIR = Path("results/pilot/sensitivity")

RAW_DIMENSIONS = ["utilization", "delay_residual", "loss_residual", "churn", "delay_jitter", "loss_jitter"]


def _seed(state: NetworkState, link_id: str, u: float, delay: float = None, loss: float = None) -> None:
    d = delay if delay is not None else predicted_delay_ms(u)
    l = loss if loss is not None else predicted_loss(u)
    state.update_link_statistics(
        LinkStatistics(
            timestamp=datetime.now(), link_id=link_id, utilization=u,
            rx_mbps=20.0, tx_mbps=18.0, status="up", delay_ms=d, packet_loss=l,
        )
    )


def raw_dimension_values(state: NetworkState, link_id: str, now: float) -> Dict[str, float]:
    """Every dimension's *raw*, unweighted value for one link -- what a
    Pareto dominance check compares directly, no weights involved."""
    stats = state.get_link_stats(link_id)
    u = float(stats.utilization)
    delay_res = float(stats.delay_ms) - predicted_delay_ms(u)
    loss_res = float(stats.packet_loss) - predicted_loss(u)
    return {
        "utilization": u,
        "delay_residual": max(delay_res, 0.0),  # only the "worse than predicted" direction counts as a cost
        "loss_residual": max(loss_res, 0.0),
        "churn": state.link_churn.get_churn_score(link_id, now=now),
        "delay_jitter": state.delay_jitter.get_jitter_score(link_id, now=now),
        "loss_jitter": state.loss_jitter.get_jitter_score(link_id, now=now),
    }


def pareto_dominates(a: Dict[str, float], b: Dict[str, float]) -> bool:
    """True if path a dominates path b: a is no worse on every dimension
    (lower cost = better) and strictly better on at least one."""
    no_worse = all(a[d] <= b[d] + 1e-12 for d in RAW_DIMENSIONS)
    strictly_better = any(a[d] < b[d] - 1e-12 for d in RAW_DIMENSIONS)
    return no_worse and strictly_better


def weighted_cost(raw: Dict[str, float], weights: Dict[str, float]) -> float:
    return (
        weights["alpha"] * raw["utilization"]
        + weights["beta"] * raw["delay_residual"]
        + weights["gamma"] * raw["loss_residual"]
        + weights["delta"] * raw["churn"]
        + weights.get("zeta", 0.0) * raw["delay_jitter"]
        + weights.get("eta", 0.0) * raw["loss_jitter"]
    )


# ---------------------------------------------------------------------------
# Trade-off scenarios. Two candidate links (not full multi-hop paths --
# matches how the real-scenario diagnostic that found this issue was built,
# and keeps dominance/weight-sweep results directly interpretable per
# dimension rather than mixed across hops).
# ---------------------------------------------------------------------------

def build_churn_vs_congestion(seed: int, u_light: float, churn_events: int, u_heavy: float) -> Tuple[NetworkState, float]:
    """"churny_light": low utilization, recently churned. "congested_clean":
    high utilization, no churn history. Mirrors the real-scenario check in
    compliance_check.md that first found this trade-off."""
    state = NetworkState()
    _seed(state, "congested_clean", u=u_heavy)
    _seed(state, "churny_light", u=u_light)
    now = time.time()
    for i in range(churn_events):
        state.record_link_churn("churny_light", timestamp=now - churn_events + i)
    return state, now


def build_jitter_vs_congestion(seed: int, u_light: float, delay_samples: List[float], u_heavy: float) -> Tuple[NetworkState, float]:
    """"jittery_light": low utilization, unstable delay (high variance
    around its own mean, not a mean shift). "congested_clean": high
    utilization, perfectly predictable delay."""
    state = NetworkState()
    ts = time.time()
    _seed(state, "congested_clean", u=u_heavy)
    for i, d in enumerate(delay_samples):
        _seed(state, "jittery_light", u=u_light, delay=d)
        state.update_link_statistics(
            LinkStatistics(
                timestamp=datetime.fromtimestamp(ts + i * 2.0), link_id="jittery_light", utilization=u_light,
                rx_mbps=20.0, tx_mbps=18.0, status="up", delay_ms=d, packet_loss=predicted_loss(u_light),
            ),
            now=ts + i * 2.0,
        )
    return state, ts + len(delay_samples) * 2.0


CHURN_VS_CONGESTION_TRIALS = [
    ("CD1", 0.20, 4, 0.60),
    ("CD2", 0.30, 3, 0.75),
    ("CD3", 0.15, 5, 0.50),
    ("CD4", 0.25, 2, 0.55),   # milder churn -- check if dominance flips
    ("CD5", 0.10, 5, 0.90),   # most extreme utilization gap tested
]

JITTER_VS_CONGESTION_TRIALS = [
    ("JD1", 0.20, [20.0, 90.0, 20.0], 0.60),
    ("JD2", 0.30, [15.0, 75.0, 15.0], 0.75),
    ("JD3", 0.15, [10.0, 110.0, 10.0], 0.50),
]


def run_dominance_and_sweep(
    label: str, state: NetworkState, now: float, link_a: str, link_b: str,
    secondary_weight: str, num_samples: int = 5000, sweep_seed: int = 0,
) -> Dict[str, object]:
    """
    secondary_weight: which of the 6 searched weights is the one link_a's
    advantage depends on (delta for churn trials, zeta for delay-jitter
    trials) -- the boundary is characterized along this weight's ratio to
    alpha specifically, since alpha (utilization) is link_b's advantage in
    every trial built here.
    """
    raw_a = raw_dimension_values(state, link_a, now)
    raw_b = raw_dimension_values(state, link_b, now)

    a_dominates = pareto_dominates(raw_a, raw_b)
    b_dominates = pareto_dominates(raw_b, raw_a)
    if a_dominates:
        status = f"{link_a} dominates -- no real trade-off, {link_b} is never preferable regardless of weights"
    elif b_dominates:
        status = f"{link_b} dominates -- no real trade-off, {link_a} is never preferable regardless of weights"
    else:
        status = "mutually non-dominated -- a genuine trade-off, weight choice actually matters"

    rng = np.random.default_rng(sweep_seed)
    fraction_a = None
    boundary_ratio = None
    if not a_dominates and not b_dominates:
        raw_weights = rng.uniform(0.0, 1.0, size=(num_samples, len(PARAM_ORDER)))
        prefers_a = np.zeros(num_samples, dtype=bool)
        for i in range(num_samples):
            weights = normalize_weights(raw_weights[i].tolist())
            cost_a = weighted_cost(raw_a, weights)
            cost_b = weighted_cost(raw_b, weights)
            prefers_a[i] = cost_a < cost_b
        fraction_a = float(np.mean(prefers_a))

        # Boundary characterization: link_a wins on utilization (lower) but
        # loses on `secondary_weight`'s raw dimension (e.g. churn) in every
        # trial built here -- so at ratio=0 (secondary weight negligible),
        # link_a's utilization edge trivially wins; as the secondary
        # weight's share relative to alpha grows, its penalty eventually
        # outweighs that edge and preference flips to link_b. Sort by
        # ascending ratio and find where the *descending* crossing through
        # 50% happens (prefers_a starts near-always-True at ratio~0, ends
        # near-always-False at high ratio) -- a simple, transparent
        # empirical estimate, not a fitted model.
        sec_idx = PARAM_ORDER.index(secondary_weight)
        alpha_idx = PARAM_ORDER.index("alpha")
        ratios = raw_weights[:, sec_idx] / np.maximum(raw_weights[:, alpha_idx], 1e-9)
        order = np.argsort(ratios)
        sorted_prefers_a = prefers_a[order]
        window = max(50, num_samples // 100)
        rolling = np.convolve(sorted_prefers_a.astype(float), np.ones(window) / window, mode="valid")
        crossing_idx = next((i for i in range(len(rolling) - 1) if rolling[i] >= 0.5 > rolling[i + 1]), None)
        if crossing_idx is not None:
            boundary_ratio = round(float(ratios[order][crossing_idx + window // 2]), 4)

    return {
        "label": label,
        "secondary_weight": secondary_weight,
        "raw_a": raw_a,
        "raw_b": raw_b,
        "dominance_status": status,
        "fraction_weight_space_preferring_a": round(fraction_a, 4) if fraction_a is not None else None,
        "approx_boundary_ratio_secondary_over_alpha": boundary_ratio,
    }


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    results: List[Dict[str, object]] = []

    for name, u_light, churn_events, u_heavy in CHURN_VS_CONGESTION_TRIALS:
        state, now = build_churn_vs_congestion(0, u_light, churn_events, u_heavy)
        results.append(run_dominance_and_sweep(
            f"churn_vs_congestion_{name}", state, now, "churny_light", "congested_clean", secondary_weight="delta"
        ))

    for name, u_light, delay_samples, u_heavy in JITTER_VS_CONGESTION_TRIALS:
        state, now = build_jitter_vs_congestion(0, u_light, delay_samples, u_heavy)
        results.append(run_dominance_and_sweep(
            f"jitter_vs_congestion_{name}", state, now, "jittery_light", "congested_clean", secondary_weight="zeta"
        ))

    with (OUTPUT_DIR / "pareto_weight_analysis.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=[
            "label", "secondary_weight", "dominance_status", "fraction_weight_space_preferring_a",
            "approx_boundary_ratio_secondary_over_alpha",
        ])
        w.writeheader()
        for r in results:
            w.writerow({k: r[k] for k in w.fieldnames})

    (OUTPUT_DIR / "pareto_weight_analysis.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
