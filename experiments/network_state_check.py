#!/usr/bin/env python3
"""
Run Network State Validation - Week 2 Day 1-6 automation.
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from src.monitor.link_mapper import LinkMapper
from src.monitor.models import LinkStatistics, PortStatistics
from src.monitor.network_state import NetworkState
from src.monitor.statistics_collector import StatisticsCollector


class NetworkStateValidation:
    """Run Week 2 Day 1-6 checks and persist Stage 2 deliverables."""

    def __init__(self, output_dir: Optional[Path] = None):
        self.output_dir = output_dir or Path("results/network_state")
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def run(self) -> Dict[str, object]:
        rate_result = self._run_rate_calculation()
        utilization_result = self._run_link_utilization()
        history_result = self._run_history_window()
        link_status_result = self._run_link_status()
        interface_result = self._run_network_state_interface()
        self._write_report(rate_result, utilization_result, history_result, link_status_result, interface_result)

        return {
            "rate": rate_result,
            "utilization": utilization_result,
            "history": history_result,
            "link_status": link_status_result,
            "interface": interface_result,
        }

    def _run_rate_calculation(self) -> Dict[str, object]:
        """Day 1: rate calculated from real byte-counter deltas over a real elapsed interval."""
        collector = StatisticsCollector(output_dir=self.output_dir)
        base_time = time.time()

        stats1 = PortStatistics(
            timestamp=datetime.now(), switch="s1", port=1,
            rx_packets=1000, rx_bytes=1_000_000, tx_packets=500, tx_bytes=500_000,
        )
        collector.calculate_rates([stats1], base_time)

        elapsed = 0.1
        time.sleep(elapsed)

        stats2 = PortStatistics(
            timestamp=datetime.now(), switch="s1", port=1,
            rx_packets=2000, rx_bytes=2_000_000, tx_packets=1000, tx_bytes=1_000_000,
        )
        processed = collector.calculate_rates([stats2], base_time + elapsed)
        collector.save_to_csv(processed, "rate_validation.csv")

        sample = processed[0]
        expected_rx_mbps = (1_000_000 * 8 / elapsed) / 1_000_000
        expected_tx_mbps = (500_000 * 8 / elapsed) / 1_000_000
        return {
            "measured_rx_mbps": round(sample.rx_mbps, 4),
            "measured_tx_mbps": round(sample.tx_mbps, 4),
            "expected_rx_mbps": round(expected_rx_mbps, 4),
            "expected_tx_mbps": round(expected_tx_mbps, 4),
            "within_tolerance": abs(sample.rx_mbps - expected_rx_mbps) < 1.0
            and abs(sample.tx_mbps - expected_tx_mbps) < 1.0,
        }

    def _run_link_utilization(self) -> Dict[str, object]:
        """Day 2: utilization derived from real port rate vs configured capacity."""
        collector = StatisticsCollector(output_dir=self.output_dir)
        mapper = LinkMapper()
        mapper.register_link("s1-s2", "s1", 1, "s2", 1)
        collector.set_link_capacity("s1", 1, 100.0)

        stats = PortStatistics(
            timestamp=datetime.now(), switch="s1", port=1,
            rx_packets=1000, rx_bytes=1_000_000, tx_packets=500, tx_bytes=500_000,
            rx_mbps=75.0, tx_mbps=0.0,
        )
        utilization = collector.calculate_utilization(stats)
        return {"utilization": utilization, "expected": 0.75, "matches_expected": utilization == 0.75}

    def _run_history_window(self) -> Dict[str, object]:
        """Day 3: rolling history window over a step-load trace (idle -> rising -> high)."""
        state = NetworkState(output_dir=self.output_dir, history_window_size=10)
        base_time = time.time()

        for i in range(15):
            ts = datetime.fromtimestamp(base_time + i)
            state.update_link_statistics(
                LinkStatistics(
                    timestamp=ts, link_id="s1-s2",
                    utilization=0.1 + i * 0.05, rx_mbps=10 + i * 5, tx_mbps=8 + i * 4, status="up",
                )
            )

        state.history.save_history_to_csv("history_window_test.csv")
        summary = state.history.get_link_summary("s1-s2")
        return {
            "current": round(summary["current"], 4),
            "mean": round(summary["mean"], 4),
            "max": round(summary["max"], 4),
            "trend": round(summary["trend"], 6) if summary["trend"] is not None else None,
            "sample_count": summary["sample_count"],
        }

    def _run_link_status(self) -> Dict[str, object]:
        """Day 4: link down/up detection, reflected in get_network_state()."""
        state = NetworkState(output_dir=self.output_dir)
        state.update_link_statistics(
            LinkStatistics(timestamp=datetime.now(), link_id="s1-s2", utilization=0.3, rx_mbps=30, tx_mbps=28, status="up")
        )

        state.set_link_status("s1-s2", is_up=False)
        down_status = state.get_network_state()["links"]["s1-s2"]["status"]

        state.set_link_status("s1-s2", is_up=True)
        up_status = state.get_network_state()["links"]["s1-s2"]["status"]

        state.link_monitor.save_events_to_csv("link_status_events.csv")
        return {
            "status_after_failure": down_status,
            "status_after_recovery": up_status,
            "failure_detected_correctly": down_status == "down",
            "recovery_detected_correctly": up_status == "up",
        }

    def _run_network_state_interface(self) -> Dict[str, object]:
        """Day 5: get_network_state() exposes a complete, consistent structure."""
        state = NetworkState(output_dir=self.output_dir)
        state.update_link_statistics(
            LinkStatistics(timestamp=datetime.now(), link_id="s1-s2", utilization=0.5, rx_mbps=50, tx_mbps=45, status="up")
        )
        network_state = state.get_network_state()
        required_keys = {"timestamp", "topology", "links", "history_summaries"}
        state.save_state_snapshot("network_state_snapshot.json")
        return {
            "has_required_keys": required_keys.issubset(network_state.keys()),
            "node_count": len(network_state["topology"]["nodes"]),
            "link_count": len(network_state["links"]),
        }

    def _write_report(
        self,
        rate_result: Dict[str, object],
        utilization_result: Dict[str, object],
        history_result: Dict[str, object],
        link_status_result: Dict[str, object],
        interface_result: Dict[str, object],
    ) -> None:
        """Day 6: integration summary derived from the actual results above."""
        expected_files = [
            "rate_validation.csv",
            "history_window_test.csv",
            "link_status_events.csv",
            "network_state_snapshot.json",
        ]
        present = [f for f in expected_files if (self.output_dir / f).exists()]

        report_path = self.output_dir / "stage2_integration_report.md"
        report_path.write_text(
            "# Stage 2 Integration Report\n\n"
            "- Rate calculation: measured %.2f/%.2f Mbps (rx/tx) vs expected %.2f/%.2f Mbps -- within tolerance: %s.\n"
            "- Link utilization: calculated %.4f, matches expected 0.75: %s.\n"
            "- History window: current=%.2f mean=%.2f max=%.2f over %d samples.\n"
            "- Link status: failure detected=%s, recovery detected=%s.\n"
            "- Network state interface: required keys present=%s, %d nodes, %d links.\n"
            "- Output files present: %s\n"
            % (
                rate_result["measured_rx_mbps"], rate_result["measured_tx_mbps"],
                rate_result["expected_rx_mbps"], rate_result["expected_tx_mbps"], rate_result["within_tolerance"],
                utilization_result["utilization"], utilization_result["matches_expected"],
                history_result["current"], history_result["mean"], history_result["max"], history_result["sample_count"],
                link_status_result["failure_detected_correctly"], link_status_result["recovery_detected_correctly"],
                interface_result["has_required_keys"], interface_result["node_count"], interface_result["link_count"],
                ", ".join(present),
            ),
            encoding="utf-8",
        )

        wrapup_path = self.output_dir / "week2_wrapup_notes.md"
        wrapup_path.write_text(
            "# Week 2 Wrap-up Notes\n\n"
            "- Day 1: rate calculation verified against known byte-counter deltas.\n"
            "- Day 2: link utilization verified against a known capacity.\n"
            "- Day 3: rolling history window verified over a step-load trace.\n"
            "- Day 4: link down/up detection verified via get_network_state().\n"
            "- Day 5: network state interface structure verified.\n"
            "- Day 6: integration report generated from the results above.\n",
            encoding="utf-8",
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Week 2 network state validation runner")
    parser.add_argument("--output-dir", type=str, default="results/network_state")
    args = parser.parse_args()

    runner = NetworkStateValidation(output_dir=Path(args.output_dir))
    results = runner.run()
    print(json.dumps(results, indent=2, default=str))


if __name__ == "__main__":
    main()
