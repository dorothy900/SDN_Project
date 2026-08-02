#!/usr/bin/env python3
"""
Scenario 3 - Link Failure and Recovery (Experiment C).

Runs two independent cases against fresh driver state each time: a stable
restoration (the failed link comes back and stays up) and an unstable
restoration (the link flaps once before settling). Both dynamic and proposed
bypass their normal stability gates on the initial failure; they differ in
how quickly (and how safely) they switch back once the link is restored.
"""

from __future__ import annotations

import csv
import time
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

FAILURE_SAMPLE = 4
RESTORE_SAMPLE = 8
FLAP_DOWN_SAMPLE = 9
FLAP_RESTORE_SAMPLE = 10


class FailureRecoveryScenario:
    """Simulate a timed link failure, then a stable or unstable restoration."""

    def __init__(self, output_dir: Optional[Path] = None):
        self.output_dir = output_dir or Path("results/pilot/scenario3")
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def run(
        self,
        flows: Sequence[FlowDefinition],
        run_index: int = 1,
        algorithms: Sequence[str] = ("static", "dynamic", "proposed"),
    ) -> Path:
        rows: List[Dict[str, object]] = []
        sample_offset = 0
        sample_offset = self._run_case(
            rows, case="stable", unstable=False, total_samples=14,
            flows=flows, run_index=run_index, algorithms=algorithms,
            sample_offset=sample_offset, seed=run_index * 20,
        )
        self._run_case(
            rows, case="unstable", unstable=True, total_samples=16,
            flows=flows, run_index=run_index, algorithms=algorithms,
            sample_offset=sample_offset, seed=run_index * 20 + 1,
        )

        output_path = self.output_dir / ("failure_recovery_run_%d.csv" % run_index)
        with output_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
        return output_path

    def _run_case(
        self,
        rows: List[Dict[str, object]],
        case: str,
        unstable: bool,
        total_samples: int,
        flows: Sequence[FlowDefinition],
        run_index: int,
        algorithms: Sequence[str],
        sample_offset: int,
        seed: int,
    ) -> int:
        state = build_network_state(self.output_dir, seed=seed)
        src, dst = PRIMARY_PAIR
        drivers = make_drivers(state, src, dst, threshold=0.7, persistence_required_samples=3)
        static, dynamic, proposed = drivers["static"], drivers["dynamic"], drivers["proposed"]
        failed_link = link_id(static.path[0], static.path[1])

        for sample in range(1, total_samples + 1):
            now_s = sample * SAMPLE_INTERVAL_S
            topology_changed = False
            failure_active = FAILURE_SAMPLE <= sample < RESTORE_SAMPLE
            if unstable:
                failure_active = failure_active or (sample == FLAP_DOWN_SAMPLE)

            if sample == RESTORE_SAMPLE:
                set_link_condition(state, failed_link, status="up")
                proposed.on_link_recovered(failed_link, now_s)
                topology_changed = True
            if unstable and sample == FLAP_DOWN_SAMPLE:
                set_link_condition(state, failed_link, status="down")
                proposed.on_link_flap()
                topology_changed = True
            if unstable and sample == FLAP_RESTORE_SAMPLE:
                set_link_condition(state, failed_link, status="up")
                proposed.on_link_recovered(failed_link, now_s)
                topology_changed = True

            results: Dict[str, Dict[str, object]] = {"static": static.step()}

            if sample == FAILURE_SAMPLE:
                set_link_condition(state, failed_link, status="down")
                results["dynamic"] = dynamic.step(now_s=now_s, topology_changed=True)
                t0 = time.perf_counter()
                proposed_action = proposed.on_link_failure(failed_link, now_s)
                results["proposed"] = {
                    "path": proposed.path,
                    "decision_time_ms": (time.perf_counter() - t0) * 1000.0,
                    **proposed_action,
                }
            else:
                results["dynamic"] = dynamic.step(now_s=now_s, topology_changed=topology_changed)
                results["proposed"] = proposed.step(now_s=now_s)

            for algorithm in algorithms:
                result = results[algorithm]
                for flow in flows:
                    metrics = compute_flow_metrics(state, result["path"], flow.offered_load_mbps)
                    rows.append(
                        {
                            "scenario": "failure_recovery",
                            "case": case,
                            "run": run_index,
                            "sample": sample_offset + sample,
                            "algorithm": algorithm,
                            "flow_id": flow.flow_id,
                            "service_type": flow.service_type,
                            "offered_load_mbps": round(flow.offered_load_mbps, 6),
                            "delay_ms": metrics["delay_ms"],
                            "throughput_mbps": metrics["throughput_mbps"],
                            "packet_loss": metrics["packet_loss"],
                            "reroute": bool(result["reroute"]),
                            "flow_updates": result["flow_updates"],
                            "decision_time_ms": result.get("decision_time_ms", 0.0),
                            "measurement_stale": False,
                            "failure_active": failure_active,
                            "recovered": sample >= RESTORE_SAMPLE,
                            "switched_back": result["path"] == static.path,
                            "failure_sample": FAILURE_SAMPLE,
                            "recovery_sample": RESTORE_SAMPLE,
                        }
                    )

        return sample_offset + total_samples
