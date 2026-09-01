#!/usr/bin/env python3
"""
Resilience-Avoidance Generalization - the real-scenario evidence for the
resilience-avoidance layer (NetworkState.get_resilience_score -> ResilienceGate
-> GraphBuilder penalty -> DecisionEngine.evaluate_resilience_avoidance). Every
other resilience artefact only exercises it on synthetic ground truth
(experiments/resilience_sensitivity.py's ROC search).

Scenario: a link on the monitored flow's cost-optimal path develops an anomaly
that its *load* does not explain, while staying up and uncongested. An
ordinary threshold-driven reroute never sees this -- the link isn't a hotspot
and nothing failed -- and a reactive baseline (dynamic) can't see it either.
It is exactly the case that needs the resilience gate's own trigger. Two
anomaly shapes exercise the two loss-signal paths:

  abnormal_loss - the link stays up, utilisation normal, but its packet loss
                  climbs far above what that utilisation predicts, after a
                  clean baseline (LossJitterTracker's 3-sigma shift term).
  chronic_loss  - the same elevated loss, present from the first sample: no
                  clean baseline, no shift -- caught only by
                  LossJitterTracker's absolute-level term.

The flap signal is covered elsewhere: failure_recovery_generalization (proposed
now stays off a link that flaps during recovery), resilience_sensitivity.py's
ROC search, and scripts/mininet_link_flap_check.py on real hardware.

Four drivers share one seeded NetworkState:
  static           - never moves
  dynamic          - reroutes on any topology change, no gates
  proposed         - full stack, resilience avoidance ON (config default):
                     evaluate_resilience_avoidance() moves the flow off the
                     gated link (emergency, no offered-load correction -- a
                     gated link is a soft failure).
  proposed_noresil - identical stack, resilience forced OFF -- the controlled
                     A/B. For a loss anomaly it has no trigger at all and
                     rides the degrading link the whole run.

Only pairs whose cost-optimal path's first hop has a genuine alternative
route are scored (same filter idea as failure_recovery_generalization). A
seed is scored only if, without resilience, the flow actually ends up on the
anomalous link.

Run as: python3 -m experiments.resilience_avoidance
Writes results/resilience_avoidance/summary.csv (one row per pair x scenario).
"""
from __future__ import annotations

import csv
from pathlib import Path
from typing import Dict, List, Optional, Sequence

import networkx as nx

from .failure_recovery_generalization import MONITORED_FLOW, labeled_pairs_23
from .simulation_common import (
    SAMPLE_INTERVAL_S,
    GraphBuilder,
    ProposedDriver,
    build_network_state,
    compute_flow_metrics,
    link_id,
    make_drivers,
    set_link_condition,
)

BASELINE_UTILIZATION = 0.35
SEEDS_PER_PAIR = 5
TOTAL_SAMPLES = 24
SCORE_FROM = 4  # let the drivers settle onto the initial path first

# abnormal_loss: a clean baseline, then a sustained upward loss shift
LOSS_CLEAN_UNTIL = 5
ABNORMAL_LOSS_RATE = 0.08  # ~80x predicted_loss at utilisation 0.35 (which is ~0.001)

SCENARIOS = ("abnormal_loss", "chronic_loss")

DRIVERS = ("static", "dynamic", "proposed", "proposed_noresil")
METRICS = (
    "reroutes", "flow_updates", "delay_ms", "loss_rate",
    "on_anomaly_link_samples", "final_uses_anomaly_link",
)


def _links_of(path: Sequence[str]) -> List[str]:
    return [link_id(u, v) for u, v in zip(path, path[1:])]


def _anomaly_link(seed: int, src: str, dst: str) -> Optional[str]:
    """
    The first hop of the pair's cost-optimal path, iff removing it still leaves
    src and dst connected (a genuine alternative route exists). Probed on a
    throwaway NetworkState so the real run's trackers see none of this.
    """
    probe = build_network_state(Path("results/resilience_avoidance"), seed=seed)
    paths = GraphBuilder(probe).get_candidate_paths(src, dst, max_paths=1)
    if not paths or len(paths[0]) < 2:
        return None
    lid = _links_of(paths[0])[0]
    graph = probe.get_active_graph()
    u, v = paths[0][0], paths[0][1]
    trimmed = graph.copy()
    trimmed.remove_edge(u, v)
    if not nx.has_path(trimmed, src, dst):
        return None
    return lid


def _make_proposed(state, src, dst, initial_path, *, resilience: bool) -> ProposedDriver:
    driver = ProposedDriver(
        state, src, dst,
        persistence_required_samples=3,
        initial_path=initial_path,
        utilization_threshold=0.7,
        offered_load_mbps=MONITORED_FLOW.offered_load_mbps,
        service_type=MONITORED_FLOW.service_type,
    )
    if not resilience:
        driver.engine.path_cost.graph_builder.resilience_avoid_threshold = None
    return driver


def _apply_anomaly(scenario: str, state, anomaly_link: str, sample: int, now_s: float) -> None:
    """Play the scenario's loss anomaly on anomaly_link for this sample."""
    if scenario == "chronic_loss":
        loss = ABNORMAL_LOSS_RATE
    else:  # abnormal_loss
        loss = None if sample <= LOSS_CLEAN_UNTIL else ABNORMAL_LOSS_RATE
    set_link_condition(
        state, anomaly_link, utilization=BASELINE_UTILIZATION, loss_override=loss, now=now_s
    )


def _run_seed(scenario: str, src: str, dst: str, seed: int) -> Optional[Dict[str, Dict[str, float]]]:
    anomaly_link = _anomaly_link(seed, src, dst)
    if anomaly_link is None:
        return None
    state = build_network_state(Path("results/resilience_avoidance"), seed=seed)

    drivers = make_drivers(
        state, src, dst, threshold=0.7, persistence_required_samples=3,
        offered_load_mbps=MONITORED_FLOW.offered_load_mbps,
        service_type=MONITORED_FLOW.service_type,
    )
    static, dynamic, proposed = drivers["static"], drivers["dynamic"], drivers["proposed"]
    proposed_noresil = _make_proposed(state, src, dst, list(proposed.path), resilience=False)
    # only score seeds where the anomalous link really is the pair's first hop
    if anomaly_link not in set(_links_of(proposed.path)):
        return None
    tracked = {
        "static": static, "dynamic": dynamic,
        "proposed": proposed, "proposed_noresil": proposed_noresil,
    }
    acc = {n: {"reroutes": 0, "flow_updates": 0, "delays": [], "losses": [], "on_anomaly": 0}
           for n in tracked}

    for sample in range(1, TOTAL_SAMPLES + 1):
        now_s = sample * SAMPLE_INTERVAL_S
        _apply_anomaly(scenario, state, anomaly_link, sample, now_s)

        # A real controller re-evaluates its routing view every monitoring
        # poll; rebuild each proposed driver's graph so the resilience gate's
        # persistence clock and the jitter/churn windows stay current.
        proposed.engine.path_cost.graph_builder.build_weighted_graph(now=now_s)
        proposed_noresil.engine.path_cost.graph_builder.build_weighted_graph(now=now_s)

        results = {"static": static.step()}
        results["dynamic"] = dynamic.step(now_s=now_s, topology_changed=False)
        for name in ("proposed", "proposed_noresil"):
            # No hotspot args -> no congestion path; step() still runs the
            # resilience-avoidance check.
            results[name] = tracked[name].step(now_s=now_s)

        if sample < SCORE_FROM:
            continue
        for name in tracked:
            res = results[name]
            path = res["path"]
            acc[name]["reroutes"] += int(bool(res.get("reroute")))
            acc[name]["flow_updates"] += int(res.get("flow_updates", 0))
            metrics = compute_flow_metrics(state, path, MONITORED_FLOW.offered_load_mbps)
            acc[name]["delays"].append(metrics["delay_ms"])
            acc[name]["losses"].append(metrics["packet_loss"])
            if anomaly_link in (set(_links_of(path)) if path else set()):
                acc[name]["on_anomaly"] += 1

    out: Dict[str, Dict[str, float]] = {}
    for name, drv in tracked.items():
        a = acc[name]
        out[name] = {
            "reroutes": a["reroutes"],
            "flow_updates": a["flow_updates"],
            "delay_ms": sum(a["delays"]) / len(a["delays"]),
            "loss_rate": sum(a["losses"]) / len(a["losses"]),
            "on_anomaly_link_samples": a["on_anomaly"],
            "final_uses_anomaly_link": float(
                anomaly_link in set(_links_of(drv.path)) if drv.path else False
            ),
        }
    out["_exercised"] = out["proposed_noresil"]["on_anomaly_link_samples"] > 0
    return out


def run_pair(scenario: str, src: str, dst: str, group: str, hops: int) -> Dict[str, object]:
    agg = {n: {m: [] for m in METRICS} for n in DRIVERS}
    scored = 0
    for trial in range(SEEDS_PER_PAIR):
        seed = int(src) * 1000 + int(dst) + trial
        seed_out = _run_seed(scenario, src, dst, seed)
        if seed_out is None or not seed_out["_exercised"]:
            continue
        scored += 1
        for n in DRIVERS:
            for m in METRICS:
                agg[n][m].append(seed_out[n][m])
    summary: Dict[str, object] = {
        "scenario": scenario, "pair": "%s->%s" % (src, dst),
        "group": group, "hops": hops, "scored_seeds": scored,
    }
    for n in DRIVERS:
        summary[n] = (
            {m: sum(v) / len(v) for m, v in agg[n].items()} if scored
            else {m: float("nan") for m in METRICS}
        )
    return summary


def main() -> None:
    from src.monitor.link_capacity import _load_geant_graph

    graph = _load_geant_graph()
    labeled_pairs = labeled_pairs_23(graph)
    print("Selected %d pairs, %d scenarios" % (len(labeled_pairs), len(SCENARIOS)))

    rows: List[Dict[str, object]] = []
    for scenario in SCENARIOS:
        print("\n=== scenario: %s ===" % scenario)
        for group, (src, dst) in labeled_pairs:
            hops = nx.shortest_path_length(graph, src, dst)
            summary = run_pair(scenario, src, dst, group, hops)
            rows.append(summary)
            if not summary["scored_seeds"]:
                print("%-9s  no structure (skipped)" % summary["pair"])
                continue
            p, pn = summary["proposed"], summary["proposed_noresil"]
            print(
                "%-9s  n=%d  on-anomaly-link(prop/noR)=%.1f/%.1f  delay(prop/noR)=%.1f/%.1f  loss%%(prop/noR)=%.2f/%.2f"
                % (summary["pair"], summary["scored_seeds"],
                   p["on_anomaly_link_samples"], pn["on_anomaly_link_samples"],
                   p["delay_ms"], pn["delay_ms"],
                   100 * p["loss_rate"], 100 * pn["loss_rate"])
            )

    output_dir = Path("results/resilience_avoidance")
    output_dir.mkdir(parents=True, exist_ok=True)
    fieldnames = ["scenario", "pair", "group", "hops", "scored_seeds"] + [
        "%s_%s" % (n, m) for n in DRIVERS for m in METRICS
    ]
    with (output_dir / "summary.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(fieldnames)
        for row in rows:
            writer.writerow(
                [row["scenario"], row["pair"], row["group"], row["hops"], row["scored_seeds"]]
                + [row[n][m] for n in DRIVERS for m in METRICS]
            )
    print("\nWrote", output_dir / "summary.csv")

    for scenario in SCENARIOS:
        scored = [r for r in rows if r["scenario"] == scenario and r["scored_seeds"]]
        total = sum(1 for r in rows if r["scenario"] == scenario)
        if not scored:
            print("\n[%s] no pair had the structure." % scenario)
            continue
        n = len(scored)
        mean = lambda name, m: sum(r[name][m] for r in scored) / n
        print("\n[%s] %d/%d pairs had the structure. Mean across them (x%d seeds):"
              % (scenario, n, total, SEEDS_PER_PAIR))
        print("  %-16s %8s %11s %9s %9s" % ("driver", "reroutes", "onAnomaly", "delayMs", "loss%"))
        for name in DRIVERS:
            print("  %-16s %8.2f %11.2f %9.1f %9.2f"
                  % (name, mean(name, "reroutes"), mean(name, "on_anomaly_link_samples"),
                     mean(name, "delay_ms"), 100 * mean(name, "loss_rate")))
        print("  resilience effect (proposed vs proposed_noresil): "
              "on-anomaly %.2f->%.2f | delay %.1f->%.1f ms | loss %.2f->%.2f %%"
              % (mean("proposed_noresil", "on_anomaly_link_samples"), mean("proposed", "on_anomaly_link_samples"),
                 mean("proposed_noresil", "delay_ms"), mean("proposed", "delay_ms"),
                 100 * mean("proposed_noresil", "loss_rate"), 100 * mean("proposed", "loss_rate")))


if __name__ == "__main__":
    main()
