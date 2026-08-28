#!/usr/bin/env python3
"""
Increasing-Load Node-Pair Generalization - generalizes increasing_load.py's
proposed-vs-baseline comparison from a single monitored pair (PRIMARY_PAIR)
to the same 23-pair sample used across the other generalized scenarios.

Reuses the identical 23-pair sample (`labeled_pairs_23`) already drawn for
the failure/recovery check, so results are directly comparable pair-for-
pair across scenarios rather than each scenario drawing its own sample.

Method: same ramp as increasing_load.py itself (hotspot link utilization
0.10 -> 0.90 across 12 samples), reimplemented per-pair since the original
script hardcodes PRIMARY_PAIR. 5 deterministic seeds per pair. Metrics:
mean delay/throughput across the full ramp, and total reroute count, for
all three algorithms.

Run as: python3 -m experiments.increasing_load_generalization
"""
from __future__ import annotations

import csv
from pathlib import Path
from typing import Dict, List

from .failure_recovery_generalization import labeled_pairs_23
from .simulation_common import (
    SAMPLE_INTERVAL_S,
    build_network_state,
    compute_flow_metrics,
    link_id,
    make_drivers,
    set_link_condition,
)

SAMPLES = 12
SEEDS_PER_PAIR = 5
FLOW_MBPS = 24.0


def _run_case(src: str, dst: str, seed: int, return_series: bool = False) -> Dict[str, object]:
    state = build_network_state(Path("results/increasing_load_generalization"), seed=seed)
    drivers = make_drivers(state, src, dst, threshold=0.7, persistence_required_samples=3)
    hotspot_link = link_id(drivers["static"].path[0], drivers["static"].path[1])

    delays = {"static": [], "dynamic": [], "proposed": []}
    reroutes = {"static": 0, "dynamic": 0, "proposed": 0}
    load_factors = []
    for sample in range(1, SAMPLES + 1):
        now_s = sample * SAMPLE_INTERVAL_S
        load_factor = 0.10 + (0.90 - 0.10) * (sample - 1) / (SAMPLES - 1)
        load_factors.append(load_factor)
        set_link_condition(state, hotspot_link, utilization=load_factor, now=now_s)
        for algo, driver in drivers.items():
            result = driver.step(now_s=now_s, hotspot_link=hotspot_link, hotspot_utilization=load_factor)
            metrics = compute_flow_metrics(state, result["path"], FLOW_MBPS * load_factor)
            delays[algo].append(metrics["delay_ms"])
            reroutes[algo] += int(bool(result["reroute"]))

    if return_series:
        return {"load_factors": load_factors, "delays": delays}

    return {
        "static_delay_ms": sum(delays["static"]) / len(delays["static"]),
        "dynamic_delay_ms": sum(delays["dynamic"]) / len(delays["dynamic"]),
        "proposed_delay_ms": sum(delays["proposed"]) / len(delays["proposed"]),
        "static_reroutes": reroutes["static"],
        "dynamic_reroutes": reroutes["dynamic"],
        "proposed_reroutes": reroutes["proposed"],
    }


def run_pair(src: str, dst: str, group: str, hops: int) -> Dict[str, object]:
    agg = {k: [] for k in (
        "static_delay_ms", "dynamic_delay_ms", "proposed_delay_ms",
        "static_reroutes", "dynamic_reroutes", "proposed_reroutes",
    )}
    for trial in range(SEEDS_PER_PAIR):
        seed = int(src) * 1000 + int(dst) + trial
        result = _run_case(src, dst, seed)
        for k in agg:
            agg[k].append(result[k])
    return {
        "pair": "%s->%s" % (src, dst), "group": group, "hops": hops,
        **{k: sum(v) / len(v) for k, v in agg.items()},
    }


def main() -> None:
    pairs = labeled_pairs_23()
    print("Running increasing-load generalization over %d pairs..." % len(pairs))

    import networkx as nx
    from src.monitor.link_capacity import _load_geant_graph
    graph = _load_geant_graph()

    rows = []
    for group, (src, dst) in pairs:
        hops = nx.shortest_path_length(graph, src, dst)
        row = run_pair(src, dst, group, hops)
        rows.append(row)
        print(
            "%-8s delay(static/dyn/prop)=%.1f/%.1f/%.1f reroutes(static/dyn/prop)=%.1f/%.1f/%.1f"
            % (row["pair"], row["static_delay_ms"], row["dynamic_delay_ms"], row["proposed_delay_ms"],
               row["static_reroutes"], row["dynamic_reroutes"], row["proposed_reroutes"])
        )

    output_dir = Path("results/increasing_load_generalization")
    output_dir.mkdir(parents=True, exist_ok=True)
    fieldnames = list(rows[0].keys())
    with (output_dir / "summary.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(fieldnames)
        for row in rows:
            writer.writerow([row[k] for k in fieldnames])
    print("Wrote", output_dir / "summary.csv")

    mean = lambda vals: sum(vals) / len(vals)
    print(
        "\nMean across %d pairs -- delay static=%.1f dynamic=%.1f proposed=%.1f | "
        "reroutes static=%.2f dynamic=%.2f proposed=%.2f"
        % (
            len(rows),
            mean([r["static_delay_ms"] for r in rows]),
            mean([r["dynamic_delay_ms"] for r in rows]),
            mean([r["proposed_delay_ms"] for r in rows]),
            mean([r["static_reroutes"] for r in rows]),
            mean([r["dynamic_reroutes"] for r in rows]),
            mean([r["proposed_reroutes"] for r in rows]),
        )
    )


if __name__ == "__main__":
    main()
