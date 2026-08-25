#!/usr/bin/env python3
"""
Priority-Policy Node-Pair Generalization - was this scenario's claim
("VoIP/Video, marked reroute_immediate in config/policies.yaml, reroute
faster than Web/File Transfer under the same congestion") ever verified
beyond PRIMARY_PAIR? No -- same gap already closed for the other three
scenarios this session (see compliance_check.md, 2026-08-24).

Reuses the identical 23-pair sample (`labeled_pairs_23`) drawn for the
failure/recovery check.

Structurally different from the other three generalizations: priority_
policy.py never compares proposed against static/dynamic -- it runs only
"proposed", split into 4 independent per-class DecisionEngines sharing one
physical path, and compares the 4 traffic classes' reroute timing against
EACH OTHER. Reimplemented per-pair since the original hardcodes
PRIMARY_PAIR. Metric: first_reroute_sample per service_type (does the
reroute_immediate ordering -- VoIP/Video before Web/File Transfer --
hold?), 5 deterministic seeds per pair.

Run as: python3 -m experiments.priority_policy_generalization
"""
from __future__ import annotations

import csv
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List

import networkx as nx

from src.decision.decision_engine import DecisionEngine
from src.monitor.models import LinkStatistics

from .failure_recovery_generalization import labeled_pairs_23
from .simulation_common import SAMPLE_INTERVAL_S, build_network_state, link_id
from .traffic_generator import FlowDefinition

UTILIZATION_TRACE = [0.68, 0.71, 0.75, 0.79, 0.83, 0.88, 0.92, 0.96, 0.97, 0.98, 0.35, 0.35, 0.35, 0.35]
PERSISTENCE_REQUIRED_SAMPLES = 3
SEEDS_PER_PAIR = 5

FLOWS = [
    FlowDefinition("flow-voip-1", "VoIP", "UDP", "hA", "hB", 0, 12, 12.0, 256),
    FlowDefinition("flow-video-1", "Video", "UDP", "hA", "hB", 0, 12, 24.0, 1200),
    FlowDefinition("flow-web-1", "Web", "TCP", "hA", "hB", 0, 12, 18.0, 1024),
    FlowDefinition("flow-file-1", "File Transfer", "TCP", "hA", "hB", 0, 12, 30.0, 1460),
]


def _make_stats(hotspot_link_id: str, utilization: float, now_s: float) -> LinkStatistics:
    return LinkStatistics(
        timestamp=datetime(2026, 8, 24, 12, 0, 0) + timedelta(seconds=now_s),
        link_id=hotspot_link_id, utilization=utilization,
        rx_mbps=round(20.0 + utilization * 80.0, 4), tx_mbps=round(18.0 + utilization * 75.0, 4),
        status="up", delay_ms=6.0, packet_loss=0.001,
    )


def _run_case(src: str, dst: str, seed: int) -> Dict[str, object]:
    state = build_network_state(Path("results/priority_policy_generalization"), seed=seed)
    engines: Dict[str, DecisionEngine] = {}
    paths: Dict[str, List[str]] = {}
    for flow in FLOWS:
        engine = DecisionEngine(state)
        engine.persistence_checker.persistence_seconds = 0.0
        engine.persistence_checker.cooldown_seconds = 0.0
        engine.persistence_checker.required_samples = PERSISTENCE_REQUIRED_SAMPLES
        initial_path = engine.path_cost.find_best_path(src, dst)
        engine.current_paths[(src, dst)] = list(initial_path)
        engines[flow.service_type] = engine
        paths[flow.service_type] = initial_path

    hotspot_link = link_id(paths[FLOWS[0].service_type][0], paths[FLOWS[0].service_type][1])
    first_reroute_sample: Dict[str, object] = {flow.service_type: None for flow in FLOWS}

    for sample, utilization in enumerate(UTILIZATION_TRACE, start=1):
        now_s = sample * SAMPLE_INTERVAL_S
        state.update_link_statistics(_make_stats(hotspot_link, utilization, now_s))
        for flow in FLOWS:
            service_type = flow.service_type
            engine = engines[service_type]
            current_path = paths[service_type]
            candidate = engine.path_cost.find_best_path(src, dst)
            if candidate and candidate != current_path:
                action = engine.evaluate_service_congestion(
                    src, dst, current_path, candidate, hotspot_link, utilization, service_type, now=now_s,
                )
                if action:
                    paths[service_type] = candidate
                    if first_reroute_sample[service_type] is None:
                        first_reroute_sample[service_type] = sample

    return first_reroute_sample


def run_pair(src: str, dst: str, group: str, hops: int) -> Dict[str, object]:
    service_types = [f.service_type for f in FLOWS]
    samples = {s: [] for s in service_types}
    reacted = {s: [] for s in service_types}
    for trial in range(SEEDS_PER_PAIR):
        seed = int(src) * 1000 + int(dst) + trial
        result = _run_case(src, dst, seed)
        for s in service_types:
            reacted[s].append(int(result[s] is not None))
            if result[s] is not None:
                samples[s].append(result[s])

    row = {"pair": "%s->%s" % (src, dst), "group": group, "hops": hops}
    for s in service_types:
        key = s.lower().replace(" ", "_")
        row["%s_reroute_rate" % key] = sum(reacted[s]) / SEEDS_PER_PAIR
        row["%s_mean_sample" % key] = (sum(samples[s]) / len(samples[s])) if samples[s] else float("nan")
    # immediate classes (VoIP/Video) should react no later than the
    # deferred classes (Web/File Transfer) on average, every pair
    immediate_mean = [row["voip_mean_sample"], row["video_mean_sample"]]
    deferred_mean = [row["web_mean_sample"], row["file_transfer_mean_sample"]]
    row["immediate_before_deferred"] = (
        max(v for v in immediate_mean if v == v) <= min(v for v in deferred_mean if v == v)
        if all(v == v for v in immediate_mean + deferred_mean) else None
    )
    return row


def main() -> None:
    pairs = labeled_pairs_23()
    from src.monitor.link_capacity import _load_geant_graph
    graph = _load_geant_graph()
    print("Running priority-policy generalization over %d pairs..." % len(pairs))

    rows = []
    for group, (src, dst) in pairs:
        hops = nx.shortest_path_length(graph, src, dst)
        row = run_pair(src, dst, group, hops)
        rows.append(row)
        print(
            "%-8s first-reroute sample: voip=%.1f video=%.1f web=%.1f file=%.1f  immediate-before-deferred=%s"
            % (row["pair"], row["voip_mean_sample"], row["video_mean_sample"],
               row["web_mean_sample"], row["file_transfer_mean_sample"], row["immediate_before_deferred"])
        )

    output_dir = Path("results/priority_policy_generalization")
    output_dir.mkdir(parents=True, exist_ok=True)
    fieldnames = list(rows[0].keys())
    with (output_dir / "summary.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(fieldnames)
        for row in rows:
            writer.writerow([row[k] for k in fieldnames])
    print("Wrote", output_dir / "summary.csv")

    n_ok = sum(1 for r in rows if r["immediate_before_deferred"])
    print("\nimmediate-before-deferred ordering holds in %d/%d pairs" % (n_ok, len(rows)))
    mean = lambda vals: sum(vals) / len(vals)
    for key in ("voip_mean_sample", "video_mean_sample", "web_mean_sample", "file_transfer_mean_sample"):
        vals = [r[key] for r in rows if r[key] == r[key]]
        print("  mean first-reroute sample, %-20s = %.2f (n=%d/%d pairs reacted)" % (key, mean(vals), len(vals), len(rows)))


if __name__ == "__main__":
    main()
