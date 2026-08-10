#!/usr/bin/env python3
"""
Calculate Metrics - Compute performance and stability metrics.
"""

from __future__ import annotations

import math
import statistics
from typing import Dict, List, Sequence


class MetricsCalculator:
    """Calculate evaluation metrics from structured scenario records."""

    def __init__(self):
        pass

    def calculate_network_performance(self, data: Sequence[Dict[str, object]]) -> Dict[str, float]:
        """Compute average delay, throughput, and packet loss."""
        if not data:
            raise ValueError("calculate_network_performance() received no rows to average")
        delays = [float(row["delay_ms"]) for row in data]
        throughputs = [float(row["throughput_mbps"]) for row in data]
        losses = [float(row["packet_loss"]) for row in data]
        return {
            "avg_delay_ms": round(statistics.mean(delays), 6),
            "avg_throughput_mbps": round(statistics.mean(throughputs), 6),
            "avg_packet_loss": round(statistics.mean(losses), 6),
        }

    def calculate_routing_stability(self, data: Sequence[Dict[str, object]]) -> Dict[str, float]:
        """Compute reroute counts and total flow updates."""
        reroute_count = sum(1 for row in data if row.get("reroute"))
        flow_updates = sum(int(row.get("flow_updates", 0)) for row in data)
        return {
            "reroute_count": reroute_count,
            "flow_update_count": flow_updates,
        }

    def calculate_controller_efficiency(self, data: Sequence[Dict[str, object]]) -> Dict[str, float]:
        """Compute average controller decision time."""
        if not data:
            raise ValueError("calculate_controller_efficiency() received no rows to average")
        times = [float(row["decision_time_ms"]) for row in data]
        return {
            "decision_time_avg_ms": round(statistics.mean(times), 6),
        }

    def calculate_summary(
        self,
        data: Sequence[Dict[str, object]],
        scenario: str,
        algorithm: str,
    ) -> Dict[str, object]:
        """Combine all metric families into one summary row."""
        if not data:
            raise ValueError(
                "No rows to summarize for scenario=%r algorithm=%r -- check the scenario script "
                "actually emitted rows for this algorithm before it reached the metrics pipeline."
                % (scenario, algorithm)
            )
        summary = {"scenario": scenario, "algorithm": algorithm, "sample_count": len(data)}
        summary.update(self.calculate_network_performance(data))
        summary.update(self.calculate_routing_stability(data))
        summary.update(self.calculate_controller_efficiency(data))
        return summary

    def aggregate_repeated_runs(
        self,
        summary_rows: Sequence[Dict[str, object]],
    ) -> List[Dict[str, object]]:
        """Aggregate repeated-run summaries with mean and 95% confidence interval."""
        grouped: Dict[tuple, Dict[str, List[float]]] = {}
        metric_names = [
            "avg_delay_ms",
            "avg_throughput_mbps",
            "avg_packet_loss",
            "reroute_count",
            "flow_update_count",
            "decision_time_avg_ms",
        ]
        for row in summary_rows:
            key = (row["scenario"], row["algorithm"])
            grouped.setdefault(key, {name: [] for name in metric_names})
            for metric in metric_names:
                grouped[key][metric].append(float(row[metric]))

        rows: List[Dict[str, object]] = []
        for (scenario, algorithm), buckets in sorted(grouped.items()):
            output: Dict[str, object] = {"scenario": scenario, "algorithm": algorithm, "trials": len(next(iter(buckets.values())))}
            for metric, values in buckets.items():
                output["%s_mean" % metric] = round(statistics.mean(values), 6)
                output["%s_ci95" % metric] = round(self._ci95(values), 6)
            rows.append(output)
        return rows

    @staticmethod
    def _ci95(values: Sequence[float]) -> float:
        if len(values) < 2:
            return 0.0
        return 1.96 * statistics.stdev(values) / math.sqrt(len(values))
