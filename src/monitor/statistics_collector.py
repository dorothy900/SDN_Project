#!/usr/bin/env python3
"""
Statistics Collector - Gather data from OVS and save to CSV
"""

import csv
import re
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple

from .models import PortStatistics, LinkStatistics


class StatisticsCollector:
    """Collect port statistics from OVS using ovs-ofctl."""

    def __init__(self, output_dir: Path = Path("results/stage1")):
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.previous_stats: Dict[Tuple[str, int], Tuple[float, int, int]] = {}

    @staticmethod
    def _run_command(command: List[str]) -> str:
        """Run shell command safely and return output."""
        try:
            result = subprocess.run(
                command,
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            return result.stdout if result.returncode == 0 else ""
        except Exception:
            return ""

    def parse_ovs_port_stats(self, switch_name: str) -> List[PortStatistics]:
        """Parse output from ovs-ofctl dump-ports."""
        output = self._run_command([
            "ovs-ofctl",
            "-O",
            "OpenFlow13",
            "dump-ports",
            switch_name,
        ])

        records: List[PortStatistics] = []
        current_port = None
        rx_packets = 0
        rx_bytes = 0
        timestamp = datetime.now()

        for line in output.splitlines():
            port_match = re.search(
                r"port\s+(\d+):\s+rx pkts=(\d+), bytes=(\d+)",
                line,
            )

            if port_match:
                current_port = int(port_match.group(1))
                rx_packets = int(port_match.group(2))
                rx_bytes = int(port_match.group(3))
                continue

            tx_match = re.search(r"tx pkts=(\d+), bytes=(\d+)", line)
            if tx_match and current_port is not None:
                tx_packets = int(tx_match.group(1))
                tx_bytes = int(tx_match.group(2))

                stats = PortStatistics(
                    timestamp=timestamp,
                    switch=switch_name,
                    port=current_port,
                    rx_packets=rx_packets,
                    rx_bytes=rx_bytes,
                    tx_packets=tx_packets,
                    tx_bytes=tx_bytes,
                )
                records.append(stats)
                current_port = None

        return records

    def calculate_rates(
        self,
        stats_list: List[PortStatistics],
        sample_time: float,
    ) -> List[PortStatistics]:
        """Calculate RX/TX rates using previous sample."""
        result = []

        for stats in stats_list:
            key = (stats.switch, stats.port)
            prev = self.previous_stats.get(key)

            rx_mbps = 0.0
            tx_mbps = 0.0

            if prev is not None:
                prev_time, prev_rx, prev_tx = prev
                delta_t = sample_time - prev_time

                if delta_t > 0:
                    rx_mbps = (stats.rx_bytes - prev_rx) * 8 / delta_t / 1_000_000
                    tx_mbps = (stats.tx_bytes - prev_tx) * 8 / delta_t / 1_000_000

            self.previous_stats[key] = (sample_time, stats.rx_bytes, stats.tx_bytes)

            updated_stats = PortStatistics(
                timestamp=stats.timestamp,
                switch=stats.switch,
                port=stats.port,
                rx_packets=stats.rx_packets,
                rx_bytes=stats.rx_bytes,
                tx_packets=stats.tx_packets,
                tx_bytes=stats.tx_bytes,
                rx_mbps=max(rx_mbps, 0.0),
                tx_mbps=max(tx_mbps, 0.0),
            )
            result.append(updated_stats)

        return result

    def save_to_csv(
        self,
        stats_list: List[PortStatistics],
        filename: str = "port_statistics.csv",
    ) -> None:
        """Save statistics to CSV file."""
        filepath = self.output_dir / filename
        file_exists = filepath.exists()

        with filepath.open("a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)

            if not file_exists:
                writer.writerow([
                    "timestamp",
                    "switch",
                    "port",
                    "rx_packets",
                    "rx_bytes",
                    "tx_packets",
                    "tx_bytes",
                    "rx_mbps",
                    "tx_mbps",
                ])

            for stats in stats_list:
                writer.writerow([
                    stats.timestamp.strftime("%Y-%m-%d %H:%M:%S.%f"),
                    stats.switch,
                    stats.port,
                    stats.rx_packets,
                    stats.rx_bytes,
                    stats.tx_packets,
                    stats.tx_bytes,
                    f"{stats.rx_mbps:.4f}",
                    f"{stats.tx_mbps:.4f}",
                ])
