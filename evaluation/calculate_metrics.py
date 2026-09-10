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

    # Two-tailed 95% t-distribution critical values, keyed by degrees of
    # freedom (n-1). 1.96 (the normal approximation) is only valid as
    # n -> infinity; at the small trial counts this project actually uses
    # (pilot_experiments.py's default repeat=3, i.e. df=2) it understates
    # the interval by ~2.2x. scipy.stats.t.ppf would compute this exactly,
    # but scipy isn't installed in this environment (see requirements.txt's
    # own note that every other stats method here is hand-rolled for the
    # same reason) -- a table covering realistic repeat counts, falling
    # back to the normal approximation once df is large enough that the
    # two agree to 3 decimal places, avoids adding a runtime dependency.
    _T_CRITICAL_95: Dict[int, float] = {
        1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571,
        6: 2.447, 7: 2.365, 8: 2.306, 9: 2.262, 10: 2.228,
        15: 2.131, 20: 2.086, 30: 2.042, 40: 2.021, 60: 2.000, 120: 1.980,
    }

    @classmethod
    def _t_critical_95(cls, df: int) -> float:
        if df in cls._T_CRITICAL_95:
            return cls._T_CRITICAL_95[df]
        if df < 1:
            return cls._T_CRITICAL_95[1]
        if df > 120:
            return 1.96
        # Between table entries: linear-interpolate on the nearest pair
        # rather than snapping to one side -- the curve is smooth and
        # monotonically decreasing here, so this stays within ~0.01 of the
        # true value anywhere it matters (small df, where the gap to 1.96
        # is largest).
        known = sorted(cls._T_CRITICAL_95)
        lower = max(k for k in known if k < df)
        upper = min(k for k in known if k > df)
        frac = (df - lower) / (upper - lower)
        return cls._T_CRITICAL_95[lower] + frac * (cls._T_CRITICAL_95[upper] - cls._T_CRITICAL_95[lower])

    @classmethod
    def _ci95(cls, values: Sequence[float]) -> float:
        n = len(values)
        if n < 2:
            return 0.0
        t_critical = cls._t_critical_95(n - 1)
        return t_critical * statistics.stdev(values) / math.sqrt(n)
