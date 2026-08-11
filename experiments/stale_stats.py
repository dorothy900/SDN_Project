#!/usr/bin/env python3
"""
Scenario 4 - Delayed/Stale Controller Statistics (Experiment D).

Drives the real algorithms against a *controller-observed* NetworkState that
can diverge from ground truth in two distinct, independently-tested ways:

  * "noise" phase: ground truth never changes, but one sample's reported
    value is momentarily corrupted/spiked -- a pure measurement artifact with
    no real congestion behind it. Tests whether an algorithm reroutes purely
    on a stale/bad sample (it shouldn't).
  * "delayed_detection" phase: a genuine sustained-congestion event occurs,
    but some polls during it lag by one sample (the controller sees the
    previous sample's true value instead of the current one). Tests whether
    detection is still correct, just delayed.

Each phase uses fresh driver/network state so a false reaction in one phase
cannot contaminate the other. Flow performance (delay/throughput/loss) is
always computed from the true, undelayed link conditions.
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
CONGESTED_UTILIZATION = 0.88
GLITCH_UTILIZATION = 0.90
PERSISTENCE_REQUIRED_SAMPLES = 3


class StaleStatsScenario:
    """Compare algorithm robustness under noisy/delayed statistics polling."""

    def __init__(self, output_dir: Optional[Path] = None):
        self.output_dir = output_dir or Path("results/pilot/scenario4")
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def run(
        self,
        flows: Sequence[FlowDefinition],
        run_index: int = 1,
        algorithms: Sequence[str] = ("static", "dynamic", "proposed"),
    ) -> Path:
        rows: List[Dict[str, object]] = []
        offset = self._run_noise_phase(rows, flows, run_index, algorithms, sample_offset=0, seed=run_index * 30)
        self._run_delayed_detection_phase(
            rows, flows, run_index, algorithms, sample_offset=offset, seed=run_index * 30 + 1
        )

        output_path = self.output_dir / ("stale_stats_run_%d.csv" % run_index)
        with output_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
        return output_path

    def _run_noise_phase(
        self,
        rows: List[Dict[str, object]],
        flows: Sequence[FlowDefinition],
        run_index: int,
        algorithms: Sequence[str],
        sample_offset: int,
        seed: int,
    ) -> int:
        """Ground truth stays flat; one sample's reading is a pure artifact."""
        total_samples = 7
        glitch_sample = 4
        truth_state = build_network_state(self.output_dir, seed=seed)
        observed_state = build_network_state(self.output_dir, seed=seed)
        src, dst = PRIMARY_PAIR
        drivers = make_drivers(
            observed_state, src, dst, threshold=0.7, persistence_required_samples=PERSISTENCE_REQUIRED_SAMPLES
        )
        hotspot_link = link_id(drivers["static"].path[0], drivers["static"].path[1])

        for sample in range(1, total_samples + 1):
            now_s = sample * SAMPLE_INTERVAL_S
            set_link_condition(truth_state, hotspot_link, utilization=BASELINE_UTILIZATION)
            stale = sample == glitch_sample
            observed_utilization = GLITCH_UTILIZATION if stale else BASELINE_UTILIZATION
            set_link_condition(observed_state, hotspot_link, utilization=observed_utilization)

            self._emit_sample_rows(
                rows=rows,
                phase="noise",
                drivers=drivers,
                hotspot_link=hotspot_link,
                observed_utilization=observed_utilization,
                ground_truth_utilization=BASELINE_UTILIZATION,
                truth_state=truth_state,
                stale=stale,
                flows=flows,
                run_index=run_index,
                algorithms=algorithms,
                sample=sample,
                sample_offset=sample_offset,
                now_s=now_s,
            )

        return sample_offset + total_samples

    def _run_delayed_detection_phase(
        self,
        rows: List[Dict[str, object]],
        flows: Sequence[FlowDefinition],
        run_index: int,
        algorithms: Sequence[str],
        sample_offset: int,
        seed: int,
    ) -> int:
        """A genuine sustained event occurs; some polls during it lag by one sample."""
        total_samples = 10
        congestion_samples = set(range(4, 10))  # samples 4-9: real, sustained
        delayed_samples = {5, 7}
        truth_state = build_network_state(self.output_dir, seed=seed)
        observed_state = build_network_state(self.output_dir, seed=seed)
        src, dst = PRIMARY_PAIR
        drivers = make_drivers(
            observed_state, src, dst, threshold=0.7, persistence_required_samples=PERSISTENCE_REQUIRED_SAMPLES
        )
        hotspot_link = link_id(drivers["static"].path[0], drivers["static"].path[1])

        last_ground_truth = BASELINE_UTILIZATION
        for sample in range(1, total_samples + 1):
            now_s = sample * SAMPLE_INTERVAL_S
            ground_truth_utilization = CONGESTED_UTILIZATION if sample in congestion_samples else BASELINE_UTILIZATION
            set_link_condition(truth_state, hotspot_link, utilization=ground_truth_utilization)

            stale = sample in delayed_samples
            observed_utilization = last_ground_truth if stale else ground_truth_utilization
            set_link_condition(observed_state, hotspot_link, utilization=observed_utilization)
            last_ground_truth = ground_truth_utilization

            self._emit_sample_rows(
                rows=rows,
                phase="delayed_detection",
                drivers=drivers,
                hotspot_link=hotspot_link,
                observed_utilization=observed_utilization,
                ground_truth_utilization=ground_truth_utilization,
                truth_state=truth_state,
                stale=stale,
                flows=flows,
                run_index=run_index,
                algorithms=algorithms,
                sample=sample,
                sample_offset=sample_offset,
                now_s=now_s,
            )

        return sample_offset + total_samples

    @staticmethod
    def _emit_sample_rows(
        rows: List[Dict[str, object]],
        phase: str,
        drivers: Dict[str, object],
        hotspot_link: str,
        observed_utilization: float,
        ground_truth_utilization: float,
        truth_state,
        stale: bool,
        flows: Sequence[FlowDefinition],
        run_index: int,
        algorithms: Sequence[str],
        sample: int,
        sample_offset: int,
        now_s: float,
    ) -> None:
        for algorithm in algorithms:
            driver = drivers[algorithm]
            result = driver.step(now_s=now_s, hotspot_link=hotspot_link, hotspot_utilization=observed_utilization)
            decision_error = bool(result["reroute"]) and ground_truth_utilization <= 0.7

            for flow in flows:
                metrics = compute_flow_metrics(truth_state, result["path"], flow.offered_load_mbps * 0.72)
                rows.append(
                    {
                        "scenario": "stale_statistics",
                        "phase": phase,
                        "run": run_index,
                        "sample": sample_offset + sample,
                        "algorithm": algorithm,
                        "flow_id": flow.flow_id,
                        "service_type": flow.service_type,
                        "offered_load_mbps": round(flow.offered_load_mbps * 0.72, 6),
                        "delay_ms": metrics["delay_ms"],
                        "throughput_mbps": metrics["throughput_mbps"],
                        "packet_loss": metrics["packet_loss"],
                        "reroute": result["reroute"],
                        "flow_updates": result["flow_updates"],
                        "decision_time_ms": result["decision_time_ms"],
                        "measurement_stale": stale,
                        "failure_active": False,
                        "ground_truth_utilization": round(ground_truth_utilization, 6),
                        "observed_utilization": round(observed_utilization, 6),
                        "decision_error": decision_error,
                    }
                )
