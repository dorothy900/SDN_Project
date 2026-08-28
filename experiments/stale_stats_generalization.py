#!/usr/bin/env python3
"""
Stale-Statistics Node-Pair Generalization - generalizes stale_stats.py's
robustness claim ("proposed doesn't falsely reroute on one glitchy sample,
and still detects a real sustained event despite delayed polls") from a
single monitored pair (PRIMARY_PAIR) to the same 23-pair sample used
across the other generalized scenarios.

Reuses the identical 23-pair sample (`labeled_pairs_23`) drawn for the
failure/recovery check, so results are directly comparable pair-for-pair
across scenarios.

Method: reimplements stale_stats.py's two phases per pair (the original
hardcodes PRIMARY_PAIR):
  - "noise": ground truth flat, one sample's OBSERVED value glitches high.
    Metric: false-positive reroute count per algorithm (want 0).
  - "delayed_detection": a real sustained congestion event, two polls
    during it report the previous (stale) sample instead of the current
    one. Metric: does the algorithm still reroute during the real
    congestion window (correct detection), and how many samples late.
5 deterministic seeds per pair per phase.

Run as: python3 -m experiments.stale_stats_generalization
"""
from __future__ import annotations

import csv
from pathlib import Path
from typing import Dict, List

import networkx as nx

from .failure_recovery_generalization import labeled_pairs_23
from .simulation_common import (
    SAMPLE_INTERVAL_S,
    build_network_state,
    link_id,
    make_drivers,
    set_link_condition,
)

BASELINE_UTILIZATION = 0.35
CONGESTED_UTILIZATION = 0.88
GLITCH_UTILIZATION = 0.90
PERSISTENCE_REQUIRED_SAMPLES = 3
SEEDS_PER_PAIR = 5

ALGOS = ("static", "dynamic", "proposed")


def _noise_phase(src: str, dst: str, seed: int) -> Dict[str, int]:
    total_samples, glitch_sample = 7, 4
    observed_state = build_network_state(Path("results/stale_stats_generalization"), seed=seed)
    drivers = make_drivers(observed_state, src, dst, threshold=0.7, persistence_required_samples=PERSISTENCE_REQUIRED_SAMPLES)
    hotspot_link = link_id(drivers["static"].path[0], drivers["static"].path[1])
    false_reroutes = {a: 0 for a in ALGOS}
    for sample in range(1, total_samples + 1):
        now_s = sample * SAMPLE_INTERVAL_S
        observed_utilization = GLITCH_UTILIZATION if sample == glitch_sample else BASELINE_UTILIZATION
        set_link_condition(observed_state, hotspot_link, utilization=observed_utilization, now=now_s)
        for algo, driver in drivers.items():
            result = driver.step(now_s=now_s, hotspot_link=hotspot_link, hotspot_utilization=observed_utilization)
            # ground truth never actually changes in this phase -- any reroute is a false positive
            if bool(result["reroute"]):
                false_reroutes[algo] += 1
    return false_reroutes


def _delayed_detection_phase(src: str, dst: str, seed: int) -> Dict[str, object]:
    total_samples = 10
    congestion_samples = set(range(4, 10))
    delayed_samples = {5, 7}
    observed_state = build_network_state(Path("results/stale_stats_generalization"), seed=seed)
    drivers = make_drivers(observed_state, src, dst, threshold=0.7, persistence_required_samples=PERSISTENCE_REQUIRED_SAMPLES)
    hotspot_link = link_id(drivers["static"].path[0], drivers["static"].path[1])

    detected_sample = {a: None for a in ALGOS}
    false_reroutes = {a: 0 for a in ALGOS}
    last_ground_truth = BASELINE_UTILIZATION
    for sample in range(1, total_samples + 1):
        now_s = sample * SAMPLE_INTERVAL_S
        ground_truth_utilization = CONGESTED_UTILIZATION if sample in congestion_samples else BASELINE_UTILIZATION
        observed_utilization = last_ground_truth if sample in delayed_samples else ground_truth_utilization
        last_ground_truth = ground_truth_utilization
        set_link_condition(observed_state, hotspot_link, utilization=observed_utilization, now=now_s)
        for algo, driver in drivers.items():
            result = driver.step(now_s=now_s, hotspot_link=hotspot_link, hotspot_utilization=observed_utilization)
            if bool(result["reroute"]):
                if ground_truth_utilization > 0.7 and detected_sample[algo] is None:
                    detected_sample[algo] = sample
                elif ground_truth_utilization <= 0.7:
                    false_reroutes[algo] += 1
    # first congestion sample is 4 -- detection_lag_samples counts how many
    # samples late the reroute landed (0 = reacted the very first sample)
    lag = {a: (detected_sample[a] - 4) if detected_sample[a] is not None else None for a in ALGOS}
    return {"detected": {a: detected_sample[a] is not None for a in ALGOS}, "lag": lag, "false_reroutes": false_reroutes}


def run_pair(src: str, dst: str, group: str, hops: int) -> Dict[str, object]:
    noise_fp = {a: [] for a in ALGOS}
    detect_rate = {a: [] for a in ALGOS}
    detect_lag = {a: [] for a in ALGOS}
    dd_false = {a: [] for a in ALGOS}
    for trial in range(SEEDS_PER_PAIR):
        seed = int(src) * 1000 + int(dst) + trial
        noise = _noise_phase(src, dst, seed + 30000)
        dd = _delayed_detection_phase(src, dst, seed + 40000)
        for a in ALGOS:
            noise_fp[a].append(noise[a])
            detect_rate[a].append(int(dd["detected"][a]))
            dd_false[a].append(dd["false_reroutes"][a])
            if dd["lag"][a] is not None:
                detect_lag[a].append(dd["lag"][a])

    row = {"pair": "%s->%s" % (src, dst), "group": group, "hops": hops}
    for a in ALGOS:
        row["%s_noise_false_reroutes" % a] = sum(noise_fp[a]) / SEEDS_PER_PAIR
        row["%s_detect_rate" % a] = sum(detect_rate[a]) / SEEDS_PER_PAIR
        row["%s_detect_lag_samples" % a] = (sum(detect_lag[a]) / len(detect_lag[a])) if detect_lag[a] else float("nan")
        row["%s_dd_false_reroutes" % a] = sum(dd_false[a]) / SEEDS_PER_PAIR
    return row


def main() -> None:
    pairs = labeled_pairs_23()
    from src.monitor.link_capacity import _load_geant_graph
    graph = _load_geant_graph()
    print("Running stale-stats generalization over %d pairs..." % len(pairs))

    rows = []
    for group, (src, dst) in pairs:
        hops = nx.shortest_path_length(graph, src, dst)
        row = run_pair(src, dst, group, hops)
        rows.append(row)
        print(
            "%-8s noise_FP(static/dyn/prop)=%.1f/%.1f/%.1f  detect_rate(dyn/prop)=%.2f/%.2f  lag(dyn/prop)=%.1f/%.1f"
            % (row["pair"], row["static_noise_false_reroutes"], row["dynamic_noise_false_reroutes"], row["proposed_noise_false_reroutes"],
               row["dynamic_detect_rate"], row["proposed_detect_rate"],
               row["dynamic_detect_lag_samples"], row["proposed_detect_lag_samples"])
        )

    output_dir = Path("results/stale_stats_generalization")
    output_dir.mkdir(parents=True, exist_ok=True)
    fieldnames = list(rows[0].keys())
    with (output_dir / "summary.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(fieldnames)
        for row in rows:
            writer.writerow([row[k] for k in fieldnames])
    print("Wrote", output_dir / "summary.csv")

    mean = lambda vals: sum(vals) / len(vals)
    print("\nMean across %d pairs:" % len(rows))
    for a in ALGOS:
        lags = [r["%s_detect_lag_samples" % a] for r in rows if r["%s_detect_lag_samples" % a] == r["%s_detect_lag_samples" % a]]
        print(
            "  %-9s noise false-reroutes=%.2f  delayed-detection rate=%.2f  mean lag=%.2f samples  dd false-reroutes=%.2f"
            % (a, mean([r["%s_noise_false_reroutes" % a] for r in rows]),
               mean([r["%s_detect_rate" % a] for r in rows]),
               mean(lags) if lags else float("nan"),
               mean([r["%s_dd_false_reroutes" % a] for r in rows]))
        )


if __name__ == "__main__":
    main()
