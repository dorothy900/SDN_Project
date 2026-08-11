#!/usr/bin/env python3
"""
Traffic Monitor - Main monitoring loop combining data collection
"""

import threading
import time
from pathlib import Path
from typing import List

from .statistics_collector import StatisticsCollector
from .odl_client import ODLClient


class TrafficMonitor:
    """Main traffic monitoring class."""

    def __init__(
        self,
        switch_names: List[str],
        output_dir: Path = Path("results/topology"),
        interval: int = 2,
        duration: int = 60,
    ):
        self.switch_names = switch_names
        self.interval = interval
        self.duration = duration
        self.collector = StatisticsCollector(output_dir)
        self.odl_client = ODLClient()
        self._running = False
        self._thread = None

    def _monitor_loop(self) -> None:
        """Internal monitoring loop."""
        start_time = time.time()
        self._running = True

        while self._running and (time.time() - start_time) < self.duration:
            sample_time = time.time()
            all_stats = []

            for switch in self.switch_names:
                stats = self.collector.parse_ovs_port_stats(switch)
                stats_with_rates = self.collector.calculate_rates(stats, sample_time)
                all_stats.extend(stats_with_rates)

            if all_stats:
                self.collector.save_to_csv(all_stats)

            elapsed = time.time() - sample_time
            sleep_time = max(0, self.interval - elapsed)
            time.sleep(sleep_time)

    def start(self) -> None:
        """Start monitoring in background thread."""
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._monitor_loop, daemon=True)
            self._thread.start()

    def stop(self) -> None:
        """Stop monitoring."""
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=5)

    def wait(self) -> None:
        """Wait for monitoring to complete."""
        if self._thread is not None:
            self._thread.join()
