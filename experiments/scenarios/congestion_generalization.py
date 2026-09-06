#!/usr/bin/env python3
"""
Congestion Node-Pair Generalization - generalizes congestion.py's transient-
spike-vs-sustained-overload check from a single monitored pair (PRIMARY_PAIR)
to the same 23-pair sample used by the other generalized scenarios.

Run as: python3 -m experiments.scenarios.congestion_generalization
"""
from __future__ import annotations

import csv
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
from experiments.common.traffic_generator import FlowDefinition

BASELINE_UTILIZATION = 0.35
SPIKE_UTILIZATION = 0.88
PERSISTENCE_REQUIRED_SAMPLES = 3
SEEDS_PER_PAIR = 5

# Same monitored flow congestion.py itself tracks (video-1's own service_type
# reaches the proposed driver, so it gets the same reroute_immediate benefit
# priority_policy.py's own path gives it).
MONITORED_FLOW = FlowDefinition("flow-video-1", "Video", "UDP", "hA", "hB", 0, 12, 24.0, 1200)


def _run_phase(
    src: str, dst: str, congestion_samples: set, total_samples: int, seed: int,
) -> Dict[str, object]:
    state = build_network_state(Path("results/congestion_generalization"), seed=seed)
    drivers = make_drivers(
        state, src, dst, threshold=0.7, persistence_required_samples=PERSISTENCE_REQUIRED_SAMPLES,
        service_type=MONITORED_FLOW.service_type,
    )
    hotspot_link = link_id(drivers["static"].path[0], drivers["static"].path[1])

    delays = {"static": [], "dynamic": [], "proposed": []}
    reroutes = {"static": 0, "dynamic": 0, "proposed": 0}
    for sample in range(1, total_samples + 1):
        now_s = sample * SAMPLE_INTERVAL_S
        utilization = SPIKE_UTILIZATION if sample in congestion_samples else BASELINE_UTILIZATION
        set_link_condition(state, hotspot_link, utilization=utilization, now=now_s)
        for algo, driver in drivers.items():
            result = driver.step(now_s=now_s, hotspot_link=hotspot_link, hotspot_utilization=utilization)
            metrics = compute_flow_metrics(
                state, result["path"], MONITORED_FLOW.offered_load_mbps * min(utilization, 1.0)
            )
            delays[algo].append(metrics["delay_ms"])
            reroutes[algo] += int(bool(result["reroute"]))

    return {
        "static_delay_ms": sum(delays["static"]) / len(delays["static"]),
        "dynamic_delay_ms": sum(delays["dynamic"]) / len(delays["dynamic"]),
        "proposed_delay_ms": sum(delays["proposed"]) / len(delays["proposed"]),
        "static_reroutes": reroutes["static"],
        "dynamic_reroutes": reroutes["dynamic"],
        "proposed_reroutes": reroutes["proposed"],
    }


# (phase label, violating samples, total samples, seed offset) -- matches congestion.py's
# own two phases exactly: an 8-sample transient spike too short for proposed's 3-sample
# persistence gate, and a 12-sample sustained overload long enough to satisfy it.
PHASES = [
    ("temporary", frozenset({4, 5}), 8, 0),
    ("sustained", frozenset({4, 5, 6, 7, 8, 9}), 12, 5000),
]


def run_pair(src: str, dst: str, group: str = "", hops: int = 0) -> tuple:
    summary = {"pair": "%s->%s" % (src, dst), "group": group, "hops": hops}
    perseed_rows: List[Dict[str, object]] = []
    for phase, congestion_samples, total_samples, seed_offset in PHASES:
        agg = {k: [] for k in (
            "static_delay_ms", "dynamic_delay_ms", "proposed_delay_ms",
            "static_reroutes", "dynamic_reroutes", "proposed_reroutes",
        )}
        for trial in range(SEEDS_PER_PAIR):
            # Deterministic, same scheme as failure_recovery_generalization.py's seeding.
            seed = int(src) * 1000 + int(dst) + seed_offset + trial
            result = _run_phase(src, dst, congestion_samples, total_samples, seed)
            for k in agg:
                agg[k].append(result[k])
            perseed_rows.append({"pair": summary["pair"], "phase": phase, "seed_trial": trial, **result})
        summary[phase] = {k: sum(v) / len(v) for k, v in agg.items()}
    return summary, perseed_rows


def main() -> None:
    import networkx as nx
    from src.monitor.link_capacity import _load_geant_graph

    graph = _load_geant_graph()
    labeled_pairs = labeled_pairs_23(graph)
    print("Selected %d pairs total:" % len(labeled_pairs))

    rows = []
    perseed_all: List[Dict[str, object]] = []
    for group, (src, dst) in labeled_pairs:
        hops = nx.shortest_path_length(graph, src, dst)
        summary, perseed_rows = run_pair(src, dst, group=group, hops=hops)
        rows.append(summary)
        perseed_all.extend(perseed_rows)
        t, s = summary["temporary"], summary["sustained"]
        print(
            "%-8s temporary: reroutes(static/dyn/prop)=%.1f/%.1f/%.1f delay=%.1f/%.1f/%.1f | "
            "sustained: reroutes=%.1f/%.1f/%.1f delay=%.1f/%.1f/%.1f"
            % (summary["pair"],
               t["static_reroutes"], t["dynamic_reroutes"], t["proposed_reroutes"],
               t["static_delay_ms"], t["dynamic_delay_ms"], t["proposed_delay_ms"],
               s["static_reroutes"], s["dynamic_reroutes"], s["proposed_reroutes"],
               s["static_delay_ms"], s["dynamic_delay_ms"], s["proposed_delay_ms"])
        )

    output_dir = Path("results/congestion_generalization")
    output_dir.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "pair", "group", "hops",
        "temporary_static_reroutes", "temporary_dynamic_reroutes", "temporary_proposed_reroutes",
        "temporary_static_delay_ms", "temporary_dynamic_delay_ms", "temporary_proposed_delay_ms",
        "sustained_static_reroutes", "sustained_dynamic_reroutes", "sustained_proposed_reroutes",
        "sustained_static_delay_ms", "sustained_dynamic_delay_ms", "sustained_proposed_delay_ms",
    ]
    with (output_dir / "summary.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(fieldnames)
        for row in rows:
            t, s = row["temporary"], row["sustained"]
            writer.writerow([
                row["pair"], row["group"], row["hops"],
                t["static_reroutes"], t["dynamic_reroutes"], t["proposed_reroutes"],
                t["static_delay_ms"], t["dynamic_delay_ms"], t["proposed_delay_ms"],
                s["static_reroutes"], s["dynamic_reroutes"], s["proposed_reroutes"],
                s["static_delay_ms"], s["dynamic_delay_ms"], s["proposed_delay_ms"],
            ])
    print("Wrote", output_dir / "summary.csv")

    with (output_dir / "perseed.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(perseed_all[0].keys()))
        writer.writeheader()
        writer.writerows(perseed_all)
    print("Wrote", output_dir / "perseed.csv")

    n = len(rows)
    mean = lambda vals: sum(vals) / len(vals)
    print(
        "\nMean temporary-phase reroutes across %d pairs -- static=%.2f dynamic=%.2f proposed=%.2f"
        % (n, mean([r["temporary"]["static_reroutes"] for r in rows]),
           mean([r["temporary"]["dynamic_reroutes"] for r in rows]),
           mean([r["temporary"]["proposed_reroutes"] for r in rows]))
    )
    print(
        "Mean sustained-phase reroutes across %d pairs -- static=%.2f dynamic=%.2f proposed=%.2f"
        % (n, mean([r["sustained"]["static_reroutes"] for r in rows]),
           mean([r["sustained"]["dynamic_reroutes"] for r in rows]),
           mean([r["sustained"]["proposed_reroutes"] for r in rows]))
    )
    print(
        "Mean temporary-phase delay across %d pairs -- static=%.1f dynamic=%.1f proposed=%.1f"
        % (n, mean([r["temporary"]["static_delay_ms"] for r in rows]),
           mean([r["temporary"]["dynamic_delay_ms"] for r in rows]),
           mean([r["temporary"]["proposed_delay_ms"] for r in rows]))
    )
    print(
        "Mean sustained-phase delay across %d pairs -- static=%.1f dynamic=%.1f proposed=%.1f"
        % (n, mean([r["sustained"]["static_delay_ms"] for r in rows]),
           mean([r["sustained"]["dynamic_delay_ms"] for r in rows]),
           mean([r["sustained"]["proposed_delay_ms"] for r in rows]))
    )


if __name__ == "__main__":
    main()
