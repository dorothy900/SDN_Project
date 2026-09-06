#!/usr/bin/env python3
"""
Stale-Statistics Per-Sample Aggregation - companion to
stale_stats_generalization.py, which only kept each pair's collapsed
false-reroute-rate/detection-lag summary. This captures the full
per-sample REAL delay trajectory (computed from ground truth, exactly as
stale_stats.py itself does -- decisions are made on the observed/possibly-
stale state, performance is measured on the true one) across all
23 pairs x 5 seeds = 115 trials per phase, aggregated mean +/- std PER
SAMPLE, so both phases can be plotted as proper delay-vs-time curves
instead of a single collapsed rate.

Run as: python3 -m experiments.scenarios.stale_stats_persample
"""
from __future__ import annotations

import csv
import statistics as st
from pathlib import Path
from typing import Dict, List

from experiments.scenarios.failure_recovery_generalization import labeled_pairs_23
from experiments.common.simulation_common import (
    SAMPLE_INTERVAL_S,
    build_network_state,
    compute_flow_metrics,
    link_id,
    make_drivers,
    set_link_condition,
)

BASELINE_UTILIZATION = 0.35
CONGESTED_UTILIZATION = 0.88
GLITCH_UTILIZATION = 0.90
PERSISTENCE_REQUIRED_SAMPLES = 3
FLOW_MBPS = 24.0 * 0.72  # matches stale_stats.py's own flow.offered_load_mbps * 0.72
ALGOS = ("static", "dynamic", "proposed")


def _noise_series(src: str, dst: str, seed: int) -> Dict[str, List[float]]:
    total_samples, glitch_sample = 7, 4
    truth_state = build_network_state(Path("results/stale_stats_generalization"), seed=seed)
    observed_state = build_network_state(Path("results/stale_stats_generalization"), seed=seed)
    drivers = make_drivers(observed_state, src, dst, threshold=0.7, persistence_required_samples=PERSISTENCE_REQUIRED_SAMPLES)
    hotspot_link = link_id(drivers["static"].path[0], drivers["static"].path[1])
    delays = {a: [] for a in ALGOS}
    for sample in range(1, total_samples + 1):
        now_s = sample * SAMPLE_INTERVAL_S
        set_link_condition(truth_state, hotspot_link, utilization=BASELINE_UTILIZATION, now=now_s)
        observed_utilization = GLITCH_UTILIZATION if sample == glitch_sample else BASELINE_UTILIZATION
        set_link_condition(observed_state, hotspot_link, utilization=observed_utilization, now=now_s)
        for algo, driver in drivers.items():
            result = driver.step(now_s=now_s, hotspot_link=hotspot_link, hotspot_utilization=observed_utilization)
            delays[algo].append(compute_flow_metrics(truth_state, result["path"], FLOW_MBPS)["delay_ms"])
    return delays


def _delayed_detection_series(src: str, dst: str, seed: int) -> Dict[str, List[float]]:
    total_samples = 10
    congestion_samples = set(range(4, 10))
    delayed_samples = {5, 7}
    truth_state = build_network_state(Path("results/stale_stats_generalization"), seed=seed)
    observed_state = build_network_state(Path("results/stale_stats_generalization"), seed=seed)
    drivers = make_drivers(observed_state, src, dst, threshold=0.7, persistence_required_samples=PERSISTENCE_REQUIRED_SAMPLES)
    hotspot_link = link_id(drivers["static"].path[0], drivers["static"].path[1])
    delays = {a: [] for a in ALGOS}
    last_ground_truth = BASELINE_UTILIZATION
    for sample in range(1, total_samples + 1):
        now_s = sample * SAMPLE_INTERVAL_S
        ground_truth_utilization = CONGESTED_UTILIZATION if sample in congestion_samples else BASELINE_UTILIZATION
        set_link_condition(truth_state, hotspot_link, utilization=ground_truth_utilization, now=now_s)
        observed_utilization = last_ground_truth if sample in delayed_samples else ground_truth_utilization
        last_ground_truth = ground_truth_utilization
        set_link_condition(observed_state, hotspot_link, utilization=observed_utilization, now=now_s)
        for algo, driver in drivers.items():
            result = driver.step(now_s=now_s, hotspot_link=hotspot_link, hotspot_utilization=observed_utilization)
            delays[algo].append(compute_flow_metrics(truth_state, result["path"], FLOW_MBPS)["delay_ms"])
    return delays


def _aggregate(phase_fn, total_samples: int, pairs) -> List[Dict[str, object]]:
    by_sample = {i: {a: [] for a in ALGOS} for i in range(total_samples)}
    for _, (src, dst) in pairs:
        for trial in range(5):
            seed = int(src) * 1000 + int(dst) + trial
            series = phase_fn(src, dst, seed)
            for a in ALGOS:
                for i, v in enumerate(series[a]):
                    by_sample[i][a].append(v)
    rows = []
    for i in range(total_samples):
        row = {"sample": i + 1}
        for a in ALGOS:
            vals = by_sample[i][a]
            row["%s_mean" % a] = st.mean(vals)
            row["%s_std" % a] = st.stdev(vals)
        rows.append(row)
    return rows


def _write(rows, path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(list(rows[0].keys()))
        for row in rows:
            writer.writerow(list(row.values()))


def main() -> None:
    pairs = labeled_pairs_23()
    output_dir = Path("results/stale_stats_generalization")
    output_dir.mkdir(parents=True, exist_ok=True)

    print("Noise phase (7 samples, glitch at sample 4):")
    noise_rows = _aggregate(_noise_series, 7, pairs)
    for row in noise_rows:
        print("  sample %d  static=%.1f  dynamic=%.1f+/-%.1f  proposed=%.1f" % (
            row["sample"], row["static_mean"], row["dynamic_mean"], row["dynamic_std"], row["proposed_mean"]))
    _write(noise_rows, output_dir / "persample_noise.csv")

    print("\nDelayed-detection phase (10 samples, congestion samples 4-9, delayed polls 5 & 7):")
    dd_rows = _aggregate(_delayed_detection_series, 10, pairs)
    for row in dd_rows:
        print("  sample %2d  static=%.1f  dynamic=%.1f+/-%.1f  proposed=%.1f+/-%.1f" % (
            row["sample"], row["static_mean"], row["dynamic_mean"], row["dynamic_std"],
            row["proposed_mean"], row["proposed_std"]))
    _write(dd_rows, output_dir / "persample_delayed_detection.csv")

    print("\nWrote persample_noise.csv and persample_delayed_detection.csv (n=%d trials/sample)" % (len(pairs) * 5))


if __name__ == "__main__":
    main()
