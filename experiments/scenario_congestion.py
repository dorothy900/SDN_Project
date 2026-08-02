#!/usr/bin/env python3
"""
Scenario 2 - Local Link Congestion: Temporary vs Persistent (Experiment B).

Runs two independent phases against fresh driver state each time: a short
spike below the persistence window (should not trigger a reroute for the
proposed algorithm) and a sustained overload above the persistence window
(should trigger a reroute for both dynamic and proposed, but only after
persistence is satisfied for proposed).
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

BASELINE_UTILIZATION = 0.35
SPIKE_UTILIZATION = 0.88
PERSISTENCE_REQUIRED_SAMPLES = 3


class CongestionScenario:
    """Simulate a transient spike then a persistent overload on one hotspot link."""

    def __init__(self, output_dir: Optional[Path] = None):
        self.output_dir = output_dir or Path("results/pilot/scenario1-2")
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def run(
        self,
        flows: Sequence[FlowDefinition],
        run_index: int = 1,
        algorithms: Sequence[str] = ("static", "dynamic", "proposed"),
    ) -> Path:
        rows: List[Dict[str, object]] = []
        sample_offset = 0
        sample_offset = self._run_phase(
            rows=rows,
            phase="temporary",
            congestion_samples={4, 5},
            total_samples=8,
            flows=flows,
            run_index=run_index,
            algorithms=algorithms,
            sample_offset=sample_offset,
            seed=run_index * 10,
        )
        self._run_phase(
            rows=rows,
            phase="sustained",
            congestion_samples={4, 5, 6, 7, 8, 9},
            total_samples=12,
            flows=flows,
            run_index=run_index,
            algorithms=algorithms,
            sample_offset=sample_offset,
            seed=run_index * 10 + 1,
        )

        output_path = self.output_dir / ("congestion_run_%d.csv" % run_index)
        with output_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
        return output_path

    def _run_phase(
        self,
        rows: List[Dict[str, object]],
        phase: str,
        congestion_samples: set,
        total_samples: int,
        flows: Sequence[FlowDefinition],
        run_index: int,
        algorithms: Sequence[str],
        sample_offset: int,
        seed: int,
    ) -> int:
        state = build_network_state(self.output_dir, seed=seed)
        src, dst = PRIMARY_PAIR
        drivers = make_drivers(
            state, src, dst, threshold=0.7, persistence_required_samples=PERSISTENCE_REQUIRED_SAMPLES
        )
        hotspot_link = link_id(drivers["static"].path[0], drivers["static"].path[1])

        for sample in range(1, total_samples + 1):
            now_s = sample * SAMPLE_INTERVAL_S
            utilization = SPIKE_UTILIZATION if sample in congestion_samples else BASELINE_UTILIZATION
            set_link_condition(state, hotspot_link, utilization=utilization)

            for algorithm in algorithms:
                driver = drivers[algorithm]
                result = driver.step(now_s=now_s, hotspot_link=hotspot_link, hotspot_utilization=utilization)

                for flow in flows:
                    load = utilization + 0.05 if flow.service_type == "File Transfer" else utilization
                    metrics = compute_flow_metrics(state, result["path"], flow.offered_load_mbps * min(load, 1.0))
                    rows.append(
                        {
                            "scenario": "local_congestion",
                            "phase": phase,
                            "run": run_index,
                            "sample": sample_offset + sample,
                            "algorithm": algorithm,
                            "flow_id": flow.flow_id,
                            "service_type": flow.service_type,
                            "offered_load_mbps": round(flow.offered_load_mbps * min(load, 1.0), 6),
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

        return sample_offset + total_samples
