#!/usr/bin/env python3
"""
Scenario Performance Objective - weight-search objective grounded in real
end-to-end scenario outcomes (delay/throughput/loss vs the dynamic
baseline), replacing the abstract regret-vs-assumed-ground-truth objective
in weight_search_comparison.py.

Rationale (per explicit user direction): searching for "which weight
vector best matches an arbitrary equal-importance ground truth" is only
ever as trustworthy as that ground truth, which this project's own Pareto
analysis showed is itself an unvalidated assumption. There is no paper or
external dataset that settles "how much should link instability outweigh
congestion" as a context-independent fact -- it is a deployment-specific
value judgment. The more directly defensible question or this specific
project is: "does this weight vector make `proposed` actually perform
better than the `dynamic` baseline on the real scenario experiments this
project already has?" -- grounded in real measured outcomes (delay,
throughput, loss), not an assumed preference ordering.

Mechanism: `simulation_common.make_drivers()` builds a real ProposedDriver
wired to a real DecisionEngine/PathCost/GraphBuilder reading weights from
config/decision.yaml by default. To evaluate a *candidate* weight vector
without touching the file on disk (this needs to run many times), the
GraphBuilder's own `weights` dict is swapped in-place after construction
(`driver.engine.path_cost.graph_builder.weights = candidate_weights`) --
the same dict object `_calculate_edge_cost` reads on every subsequent
`build_weighted_graph()` call, so this takes effect immediately.

Benchmarked before committing to a search budget (not assumed): a single
congestion scenario run (all 3 algorithms) takes ~0.086s, failure_recovery
~0.11s -- a 2-scenario evaluation costs ~0.2s, meaning even a 729-evaluation
DIRECT budget completes in ~2-3 minutes. Comfortably affordable; no need to
narrow the search dimensionality for cost reasons.

Run as: python3 -m experiments.scenario_performance_objective
"""
from __future__ import annotations

import csv
import statistics
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from .congestion import CongestionScenario
from .failure_recovery import FailureRecoveryScenario
from .simulation_common import make_drivers
from .traffic_generator import TrafficGenerator
from .weight_search_comparison import PARAM_ORDER

import experiments.congestion as _congestion_module
import experiments.failure_recovery as _failure_recovery_module

OUTPUT_DIR = Path("results/pilot/sensitivity")

# Scenarios included in the objective -- started with 2 representative ones
# per explicit user direction ("先测1-2个代表性场景，如果效果不错再推广5个都做"),
# not the full 5-scenario pilot_experiments.py suite. congestion.py exercises
# graded utilization response (temporary vs sustained overload); failure_recovery.py
# exercises a structurally different trigger (a real down/up link, not a
# utilization threshold) -- together they cover both of this project's two
# distinct reroute-trigger mechanisms, a reasonable representative pair
# before deciding whether to extend to stale_stats/priority_policy/increasing_load.
SCENARIO_CLASSES = [CongestionScenario, FailureRecoveryScenario]
REPEAT_SEEDS = [1, 2, 3]  # multiple run_index seeds per evaluation for stability

_traffic_generator: Optional[TrafficGenerator] = None
_flows = None


def _get_flows():
    global _traffic_generator, _flows
    if _flows is None:
        _traffic_generator = TrafficGenerator(output_dir=OUTPUT_DIR / "scenario_objective_traffic")
        _flows = _traffic_generator.generate_concurrent_flows()
    return _flows


def _inject_weights(weights: Dict[str, float]):
    """Monkeypatch make_drivers so the proposed driver's GraphBuilder uses
    `weights` instead of config/decision.yaml's, for the duration this
    patch is installed. Returns a restore function."""
    original_make_drivers = _congestion_module.make_drivers

    def patched(*args, **kwargs):
        drivers = make_drivers(*args, **kwargs)
        drivers["proposed"].engine.path_cost.graph_builder.weights = weights
        drivers["proposed"].engine.path_cost.weights = weights
        return drivers

    _congestion_module.make_drivers = patched
    _failure_recovery_module.make_drivers = patched

    def restore():
        _congestion_module.make_drivers = original_make_drivers
        _failure_recovery_module.make_drivers = original_make_drivers

    return restore


def _mean_metrics(rows: List[Dict[str, object]], algorithm: str) -> Dict[str, float]:
    sub = [r for r in rows if r["algorithm"] == algorithm]
    return {
        "delay_ms": statistics.mean(float(r["delay_ms"]) for r in sub),
        "throughput_mbps": statistics.mean(float(r["throughput_mbps"]) for r in sub),
        "packet_loss": statistics.mean(float(r["packet_loss"]) for r in sub),
    }


def _relative_improvement(proposed: Dict[str, float], dynamic: Dict[str, float]) -> float:
    """
    Average relative improvement of `proposed` over `dynamic` across
    delay/throughput/loss (simple average, per explicit user direction --
    not weighted, not combined with reroute-count/stability metrics yet).
    Positive = proposed better. Guards zero-valued denominators (e.g. a
    scenario phase with no dynamic-baseline loss) by skipping that metric
    for that comparison rather than dividing by zero.
    """
    terms = []
    if dynamic["delay_ms"] > 0:
        terms.append((dynamic["delay_ms"] - proposed["delay_ms"]) / dynamic["delay_ms"])
    if dynamic["throughput_mbps"] > 0:
        terms.append((proposed["throughput_mbps"] - dynamic["throughput_mbps"]) / dynamic["throughput_mbps"])
    if dynamic["packet_loss"] > 0:
        terms.append((dynamic["packet_loss"] - proposed["packet_loss"]) / dynamic["packet_loss"])
    return statistics.mean(terms) if terms else 0.0


def mean_relative_improvement(weights: Sequence[float]) -> float:
    """
    weights: values in PARAM_ORDER (alpha,beta,gamma,delta,zeta,eta) -- eta
    included since epsilon is fixed at its production default, same
    convention as weight_search_comparison.py.
    """
    weights_dict = dict(zip(PARAM_ORDER, weights))
    weights_dict["epsilon"] = 0.05

    restore = _inject_weights(weights_dict)
    try:
        flows = _get_flows()
        improvements = []
        for scenario_cls in SCENARIO_CLASSES:
            for seed in REPEAT_SEEDS:
                out_dir = OUTPUT_DIR / "scenario_objective" / scenario_cls.__name__
                path = scenario_cls(out_dir).run(flows, run_index=seed)
                with open(path) as f:
                    rows = list(csv.DictReader(f))
                proposed = _mean_metrics(rows, "proposed")
                dynamic = _mean_metrics(rows, "dynamic")
                improvements.append(_relative_improvement(proposed, dynamic))
        return statistics.mean(improvements)
    finally:
        restore()


def objective_to_minimize(*weights: float) -> float:
    """DIRECT/scipy minimize -- negate so minimizing this maximizes real
    improvement over the dynamic baseline."""
    return -mean_relative_improvement(weights)


def _sanity_check() -> None:
    """Before trusting any search result: does this objective behave
    sensibly at all? Compare production defaults against a deliberately
    bad vector (alpha=0 -- ignores congestion entirely, the one thing this
    formula's whole design is built around detecting) and a deliberately
    good-looking one (alpha dominant, matching what congestion-driven
    reroute scenarios should reward)."""
    candidates = {
        "current_production_defaults": [0.4, 0.3, 0.2, 0.05, 0.05, 0.05],
        "alpha_zero (should be bad -- blind to congestion)": [0.0, 0.3, 0.2, 0.05, 0.05, 0.05],
        "alpha_dominant": [0.8, 0.1, 0.05, 0.02, 0.02, 0.01],
        "all_equal (the old degenerate DIRECT artifact)": [0.5, 0.5, 0.5, 0.5, 0.5, 0.5],
    }
    print("Sanity check: mean relative improvement over dynamic baseline (higher = better)")
    for label, w in candidates.items():
        v = mean_relative_improvement(w)
        print(f"  {label}: {v:+.4f}")


def operational_cost_report(weights: Sequence[float], seeds: Sequence[int] = (11, 12, 13)) -> Dict[str, object]:
    """
    Reroute *operational cost* (flow-table rule installs, reroute event
    count) for proposed vs dynamic -- a dimension DIRECT's search on
    mean_relative_improvement (delay/throughput/loss) cannot see or
    optimize, added 2026-08-20 after that search confirmed weight tuning
    alone cannot close the delay/throughput/loss gap (dynamic wins there
    by design -- it reacts with zero caution, so persistence's reaction
    lag, not the cost weights, is what proposed trails on). Real question
    this answers instead: does proposed's stability machinery actually
    prevent some of dynamic's reroutes, at a real operational cost saved
    (fewer OpenFlow rule installs), even though it doesn't win on delay?

    IMPORTANT: `flow_updates`/`reroute` in each scenario's CSV are written
    identically onto every flow's row for a given sample (one row per
    flow, same shared per-sample decision) -- summing them naively over
    all rows overcounts by the flow count (4x here). This filters to a
    single representative flow (flow-video-1, this project's convention
    for "the" monitored flow, see PRIMARY_PAIR's docstring) to get the
    real per-scenario-run count.
    """
    weights_dict = dict(zip(PARAM_ORDER, weights))
    weights_dict["epsilon"] = 0.05
    restore = _inject_weights(weights_dict)
    try:
        flows = _get_flows()
        report: Dict[str, object] = {}
        for scenario_cls in SCENARIO_CLASSES:
            for algo in ("proposed", "dynamic"):
                total_fu, total_reroutes = 0, 0
                for seed in seeds:
                    out_dir = OUTPUT_DIR / "scenario_objective" / scenario_cls.__name__
                    path = scenario_cls(out_dir).run(flows, run_index=seed)
                    with open(path) as f:
                        rows = list(csv.DictReader(f))
                    sub = [r for r in rows if r["algorithm"] == algo and r["flow_id"] == "flow-video-1"]
                    total_fu += sum(int(r["flow_updates"]) for r in sub)
                    total_reroutes += sum(1 for r in sub if r["reroute"] in ("True", "1"))
                report[f"{scenario_cls.__name__}/{algo}"] = {
                    "mean_flow_updates_per_run": total_fu / len(seeds),
                    "mean_reroute_events_per_run": total_reroutes / len(seeds),
                }
        return report
    finally:
        restore()


if __name__ == "__main__":
    _sanity_check()
    print("\nOperational cost (proposed vs dynamic), production defaults, holdout seeds:")
    for key, stats in operational_cost_report([0.4, 0.3, 0.2, 0.05, 0.05, 0.05]).items():
        print(f"  {key}: {stats}")
