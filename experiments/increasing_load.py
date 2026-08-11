#!/usr/bin/env python3
"""
Scenario 1 - Increasing Traffic Demand (Experiment A).

Ramps utilization on the primary path's first link from ~10% to ~90% of
capacity and drives all three algorithms (static, dynamic, proposed) through
their real implementations (src/routing, src/decision, src/stability) against
the same seeded network state.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from .simulation_common import (
    PRIMARY_PAIR,
    SAMPLE_INTERVAL_S,
    build_network_state,
    compute_flow_metrics,
    link_id,
    make_drivers,
    set_link_condition,
)
from .traffic_generator import FlowDefinition

SAMPLES = 12


class IncreasingLoadScenario:
    """Ramp offered load past the utilization threshold and record real reroute behavior."""

    def __init__(self, output_dir: Optional[Path] = None):
        self.output_dir = output_dir or Path("results/pilot/scenario1-2")
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def run(
        self,
        flows: Sequence[FlowDefinition],
        run_index: int = 1,
        algorithms: Sequence[str] = ("static", "dynamic", "proposed"),
    ) -> Path:
        state = build_network_state(self.output_dir, seed=run_index)
        src, dst = PRIMARY_PAIR
        drivers = make_drivers(state, src, dst, threshold=0.7, persistence_required_samples=3)
        hotspot_link = link_id(drivers["static"].path[0], drivers["static"].path[1])

        rows: List[Dict[str, object]] = []
        for sample in range(1, SAMPLES + 1):
            now_s = sample * SAMPLE_INTERVAL_S
            load_factor = 0.10 + (0.90 - 0.10) * (sample - 1) / (SAMPLES - 1)
            set_link_condition(state, hotspot_link, utilization=load_factor)

            for algorithm in algorithms:
                driver = drivers[algorithm]
                result = driver.step(now_s=now_s, hotspot_link=hotspot_link, hotspot_utilization=load_factor)

                for flow in flows:
                    if not (flow.start_s < sample <= flow.start_s + flow.duration_s):
                        continue
                    metrics = compute_flow_metrics(state, result["path"], flow.offered_load_mbps * load_factor)
                    rows.append(
                        {
                            "scenario": "increasing_load",
                            "run": run_index,
                            "sample": sample,
                            "algorithm": algorithm,
                            "flow_id": flow.flow_id,
                            "service_type": flow.service_type,
                            "offered_load_mbps": round(flow.offered_load_mbps * load_factor, 6),
                            "delay_ms": metrics["delay_ms"],
                            "throughput_mbps": metrics["throughput_mbps"],
                            "packet_loss": metrics["packet_loss"],
                            "reroute": result["reroute"],
                            "flow_updates": result["flow_updates"],
                            "decision_time_ms": result["decision_time_ms"],
                            "measurement_stale": False,
                            "failure_active": False,
                        }
                    )

        output_path = self.output_dir / ("increasing_load_run_%d.csv" % run_index)
        with output_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
        return output_path
