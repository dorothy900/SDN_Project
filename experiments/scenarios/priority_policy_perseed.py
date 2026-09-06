#!/usr/bin/env python3
"""
Priority-Policy Per-Seed Aggregation - companion to
priority_policy_generalization.py, which collapses each pair's 5 seeds
straight to a mean inside run_pair() and never writes the individual
per-seed first-reroute-sample values anywhere (finding F3 in this
session's scenario design audit -- failure_recovery had the identical
gap, see failure_recovery_perseed.py).

Writes one row per (pair, seed, service_type) -- 23 pairs x 5 seeds x 4
classes = 460 rows -- so a reader can compute a real per-pair/per-class
standard deviation across seeds instead of only the already-collapsed
mean priority_policy_generalization.py reports.

Run as: python3 -m experiments.scenarios.priority_policy_perseed
"""
from __future__ import annotations

import csv
from pathlib import Path
from typing import List

from experiments.scenarios.failure_recovery_generalization import labeled_pairs_23
from experiments.scenarios.priority_policy_generalization import FLOWS, SEEDS_PER_PAIR, _run_case

FIELDS = ["pair", "group", "hops", "seed", "trial", "service_type", "first_reroute_sample", "reacted"]


def main() -> None:
    import networkx as nx
    from src.monitor.link_capacity import _load_geant_graph

    graph = _load_geant_graph()
    pairs = labeled_pairs_23(graph)
    service_types = [f.service_type for f in FLOWS]
    print("Running priority-policy per-seed aggregation over %d pairs x %d seeds..." % (len(pairs), SEEDS_PER_PAIR))

    rows: List[dict] = []
    for group, (src, dst) in pairs:
        hops = nx.shortest_path_length(graph, src, dst)
        for trial in range(SEEDS_PER_PAIR):
            seed = int(src) * 1000 + int(dst) + trial
            result = _run_case(src, dst, seed)
            for service_type in service_types:
                sample = result[service_type]
                rows.append({
                    "pair": "%s->%s" % (src, dst), "group": group, "hops": hops,
                    "seed": seed, "trial": trial, "service_type": service_type,
                    "first_reroute_sample": sample if sample is not None else "",
                    "reacted": int(sample is not None),
                })

    output_dir = Path("results/priority_policy_generalization")
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / "perseed.csv"
    with out_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    print("Wrote", out_path, "(%d rows = %d pairs x %d seeds x %d classes)" % (
        len(rows), len(pairs), SEEDS_PER_PAIR, len(service_types)))

    import statistics as st
    for service_type in service_types:
        by_pair: dict = {}
        for row in rows:
            if row["service_type"] != service_type or row["first_reroute_sample"] == "":
                continue
            by_pair.setdefault(row["pair"], []).append(row["first_reroute_sample"])
        per_pair_std = [st.stdev(v) for v in by_pair.values() if len(v) > 1]
        if per_pair_std:
            print(
                "%-14s mean within-pair seed std = %.3f samples (n=%d pairs with variance)"
                % (service_type, sum(per_pair_std) / len(per_pair_std), len(per_pair_std))
            )
        else:
            print("%-14s zero variance across seeds in every pair" % service_type)


if __name__ == "__main__":
    main()
