#!/usr/bin/env python3
"""
Increasing-Load Per-Sample Aggregation - companion to
increasing_load_generalization.py, which only kept each pair's *mean*
delay across the ramp. This captures the full per-sample trajectory
(delay at each of the 12 utilization steps, 0.10 -> 0.90) across all
23 pairs x 5 seeds = 115 trials, then aggregates mean +/- std PER SAMPLE
so the result can be plotted as a proper "delay vs. offered load" curve
(the standard networking-paper form) instead of a single collapsed bar.

Run as: python3 -m experiments.increasing_load_persample
"""
from __future__ import annotations

import csv
import statistics as st
from pathlib import Path
from typing import Dict, List

from .failure_recovery_generalization import labeled_pairs_23
from .increasing_load_generalization import SAMPLES, _run_case


def main() -> None:
    pairs = labeled_pairs_23()
    by_sample: Dict[int, Dict[str, List[float]]] = {
        i: {"static": [], "dynamic": [], "proposed": []} for i in range(SAMPLES)
    }
    load_factors = None

    for _, (src, dst) in pairs:
        for trial in range(5):
            seed = int(src) * 1000 + int(dst) + trial
            series = _run_case(src, dst, seed, return_series=True)
            load_factors = series["load_factors"]
            for algo in ("static", "dynamic", "proposed"):
                for i, v in enumerate(series["delays"][algo]):
                    by_sample[i][algo].append(v)

    output_dir = Path("results/increasing_load_generalization")
    output_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for i in range(SAMPLES):
        row = {"sample": i + 1, "load_factor": round(load_factors[i], 4)}
        for algo in ("static", "dynamic", "proposed"):
            vals = by_sample[i][algo]
            row["%s_mean" % algo] = st.mean(vals)
            row["%s_std" % algo] = st.stdev(vals)
        rows.append(row)
        print(
            "sample %2d  load=%.2f  static=%.1f+/-%.1f  dynamic=%.1f+/-%.1f  proposed=%.1f+/-%.1f"
            % (row["sample"], row["load_factor"],
               row["static_mean"], row["static_std"],
               row["dynamic_mean"], row["dynamic_std"],
               row["proposed_mean"], row["proposed_std"])
        )

    with (output_dir / "persample.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(list(rows[0].keys()))
        for row in rows:
            writer.writerow(list(row.values()))
    print("\nWrote", output_dir / "persample.csv", "(n=%d trials per sample)" % (len(pairs) * 5))


if __name__ == "__main__":
    main()
