#!/usr/bin/env python3
"""
Failure/Recovery Per-Seed Aggregation - companion to
failure_recovery_generalization.py, which collapses each pair's 5 seeds
straight to a mean inside run_pair() and never writes the individual
per-seed values anywhere (increasing_load and stale_stats already have
this companion; this closes the same gap for failure_recovery).

Writes one row per (pair, case, seed) instead of one row per (pair, case)
-- 23 pairs x 2 cases x 5 seeds = 230 rows -- so a reader can compute a
real per-pair standard deviation/CI across seeds, or build a genuine
seed-level box plot, instead of only ever seeing the already-collapsed
mean failure_recovery_generalization.py reports.

Run as: python3 -m experiments.scenarios.failure_recovery_perseed
"""
from __future__ import annotations

import csv
from pathlib import Path
from typing import List

from experiments.scenarios.failure_recovery_generalization import (
    FLAP_DOWN_SAMPLE,
    FLAP_RESTORE_SAMPLE,
    FAILURE_SAMPLE,
    RESTORE_SAMPLE,
    SEEDS_PER_PAIR,
    _run_case,
    labeled_pairs_23,
)

FIELDS = [
    "pair", "group", "hops", "case", "seed", "trial",
    "static_delay_ms", "dynamic_delay_ms", "proposed_delay_ms",
    "dynamic_reroutes", "proposed_reroutes",
    "dynamic_flow_updates", "proposed_flow_updates",
    "dynamic_switched_back", "proposed_switched_back",
]


def main() -> None:
    import networkx as nx
    from src.monitor.link_capacity import _load_geant_graph

    graph = _load_geant_graph()
    pairs = labeled_pairs_23(graph)
    print("Running failure/recovery per-seed aggregation over %d pairs x %d seeds..." % (len(pairs), SEEDS_PER_PAIR))

    rows: List[dict] = []
    for group, (src, dst) in pairs:
        hops = nx.shortest_path_length(graph, src, dst)
        for case, unstable, total_samples in (("stable", False, 14), ("unstable", True, 16)):
            case_offset = 0 if case == "stable" else 5000
            for trial in range(SEEDS_PER_PAIR):
                seed = int(src) * 1000 + int(dst) + case_offset + trial
                result = _run_case(src, dst, case, unstable, total_samples, seed)
                rows.append({
                    "pair": "%s->%s" % (src, dst), "group": group, "hops": hops,
                    "case": case, "seed": seed, "trial": trial,
                    "static_delay_ms": result["static_delay_ms"],
                    "dynamic_delay_ms": result["dynamic_delay_ms"],
                    "proposed_delay_ms": result["proposed_delay_ms"],
                    "dynamic_reroutes": result["dynamic_reroutes"],
                    "proposed_reroutes": result["proposed_reroutes"],
                    "dynamic_flow_updates": result["dynamic_flow_updates"],
                    "proposed_flow_updates": result["proposed_flow_updates"],
                    "dynamic_switched_back": result["dynamic_switched_back"],
                    "proposed_switched_back": result["proposed_switched_back"],
                })

    output_dir = Path("results/failure_recovery_generalization")
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / "perseed.csv"
    with out_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    print("Wrote", out_path, "(%d rows = %d pairs x 2 cases x %d seeds)" % (len(rows), len(pairs), SEEDS_PER_PAIR))

    import statistics as st
    for case in ("stable", "unstable"):
        for algo in ("static", "dynamic", "proposed"):
            key = "%s_delay_ms" % algo
            by_pair: dict = {}
            for row in rows:
                if row["case"] != case:
                    continue
                by_pair.setdefault(row["pair"], []).append(row[key])
            per_pair_std = [st.stdev(v) for v in by_pair.values() if len(v) > 1]
            if per_pair_std:
                print(
                    "%s / %-9s mean within-pair seed std = %.3fms (n=%d pairs)"
                    % (case, algo, sum(per_pair_std) / len(per_pair_std), len(per_pair_std))
                )


if __name__ == "__main__":
    main()
