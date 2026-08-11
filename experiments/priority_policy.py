#!/usr/bin/env python3
"""
Scenario 5 - Priority-Aware Traffic Policy (Experiment E).

All four traffic classes (VoIP, Video, Web, File Transfer) are evaluated
concurrently against the same shared congestion trace on the same physical
path, each through its own DecisionEngine so hold-down/persistence bookkeeping
never crosses between classes -- but every engine sees the identical NetworkState
and utilization trace at the identical sample times, i.e. genuinely concurrent.
Per-class reroute timing comes from DecisionEngine.evaluate_service_congestion(),
which applies each class's real config/policies.yaml effective threshold and
"reroute_immediate" flag -- nothing about which class reacts first is hardcoded.
"""

from __future__ import annotations

import csv
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from src.decision.decision_engine import DecisionEngine
from src.monitor.models import LinkStatistics

from .simulation_common import (
    PRIMARY_PAIR,
    SAMPLE_INTERVAL_S,
    build_network_state,
    compute_flow_metrics,
    link_id,
)
from .traffic_generator import FlowDefinition

# A monotonic ramp into sustained overload, then relief -- long enough that
# even File Transfer's high effective threshold (base 0.70 + its qos_threshold
# tolerance of 0.25 = 0.95) is eventually crossed for 3 consecutive samples,
# satisfying persistence, same as every other class.
UTILIZATION_TRACE = [0.68, 0.71, 0.75, 0.79, 0.83, 0.88, 0.92, 0.96, 0.97, 0.98, 0.35, 0.35, 0.35, 0.35]
PERSISTENCE_REQUIRED_SAMPLES = 3


class PriorityPolicyScenario:
    """Measure per-class reroute latency and post-congestion performance."""

    def __init__(self, output_dir: Optional[Path] = None):
        self.output_dir = output_dir or Path("results/pilot/scenario5")
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def run(
        self,
        flows: Sequence[FlowDefinition],
        run_index: int = 1,
        algorithms: Sequence[str] = ("proposed",),
    ) -> Path:
        state = build_network_state(self.output_dir, seed=run_index * 40)
        src, dst = PRIMARY_PAIR

        engines: Dict[str, DecisionEngine] = {}
        paths: Dict[str, List[str]] = {}
        for flow in flows:
            engine = DecisionEngine(state)
            engine.persistence_checker.persistence_seconds = 0.0
            engine.persistence_checker.cooldown_seconds = 0.0
            engine.persistence_checker.required_samples = PERSISTENCE_REQUIRED_SAMPLES
            initial_path = engine.path_cost.find_best_path(src, dst)
            engine.current_paths[(src, dst)] = list(initial_path)
            engines[flow.service_type] = engine
            paths[flow.service_type] = initial_path

        hotspot_link = link_id(paths[flows[0].service_type][0], paths[flows[0].service_type][1])
        first_reroute_sample: Dict[str, Optional[int]] = {flow.service_type: None for flow in flows}
        rows: List[Dict[str, object]] = []

        for sample, utilization in enumerate(UTILIZATION_TRACE, start=1):
            now_s = sample * SAMPLE_INTERVAL_S
            state.update_link_statistics(
                self._make_stats(state, hotspot_link, utilization, now_s)
            )

            for flow in flows:
                service_type = flow.service_type
                engine = engines[service_type]
                current_path = paths[service_type]
                t0 = time.perf_counter()
                candidate = engine.path_cost.find_best_path(src, dst)
                reroute = False
                flow_updates = 0
                if candidate and candidate != current_path:
                    action = engine.evaluate_service_congestion(
                        src, dst, current_path, candidate, hotspot_link, utilization, service_type, now=now_s
                    )
                    if action:
                        flow_updates = len(engine.flow_installer.build_flow_rules(candidate))
                        paths[service_type] = candidate
                        reroute = True
                        if first_reroute_sample[service_type] is None:
                            first_reroute_sample[service_type] = sample
                decision_time_ms = (time.perf_counter() - t0) * 1000.0

                metrics = compute_flow_metrics(state, paths[service_type], flow.offered_load_mbps)
                rows.append(
                    {
                        "scenario": "priority_policy",
                        "run": run_index,
                        "sample": sample,
                        "algorithm": "proposed",
                        "flow_id": flow.flow_id,
                        "service_type": service_type,
                        "utilization": round(utilization, 6),
                        "offered_load_mbps": round(flow.offered_load_mbps, 6),
                        "delay_ms": metrics["delay_ms"],
                        "throughput_mbps": metrics["throughput_mbps"],
                        "packet_loss": metrics["packet_loss"],
                        "reroute": reroute,
                        "flow_updates": flow_updates,
                        "decision_time_ms": round(decision_time_ms, 6),
                        "measurement_stale": False,
                        "failure_active": False,
                        "first_reroute_sample": first_reroute_sample[service_type] or "",
                    }
                )

        output_path = self.output_dir / ("priority_policy_run_%d.csv" % run_index)
        with output_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
        return output_path

    @staticmethod
    def _make_stats(state, hotspot_link_id: str, utilization: float, now_s: float):
        old = state.get_link_stats(hotspot_link_id)
        base_delay = float(old.delay_ms) if old and old.delay_ms else 6.0
        base_loss = float(old.packet_loss) if old and old.packet_loss else 0.001
        return LinkStatistics(
            timestamp=datetime(2026, 8, 24, 12, 0, 0) + timedelta(seconds=now_s),
            link_id=hotspot_link_id,
            utilization=utilization,
            rx_mbps=round(20.0 + utilization * 80.0, 4),
            tx_mbps=round(18.0 + utilization * 75.0, 4),
            status="up",
            delay_ms=round(base_delay, 4),
            packet_loss=round(base_loss, 6),
        )
