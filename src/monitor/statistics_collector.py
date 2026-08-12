#!/usr/bin/env python3
"""
Statistics Collector - Gather data from OVS and save to CSV
Week 2: Rate calculation, link utilization, and more
"""

import csv
import re
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple, Optional

import yaml

from .models import PortStatistics, LinkStatistics


class StatisticsCollector:
    """Collect port statistics from OVS using ovs-ofctl."""

    def __init__(self, output_dir: Path = Path("results/network_state"), config_path: str = "config/topology.yaml"):
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.previous_stats: Dict[Tuple[str, int], Tuple[float, int, int]] = {}
        
        # Load configuration
        self.link_capacities: Dict[str, float] = {}  # switch-port -> capacity in Mbps
        self._load_config(config_path)
    
    def _load_config(self, config_path: str) -> None:
        """Load topology and link capacity configuration."""
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                config = yaml.safe_load(f)
                # Default capacity if not specified
                self.default_capacity = config.get("mininet", {}).get("link_bandwidth_mbps", 100)
        except Exception:
            self.default_capacity = 100.0  # Fallback to 100 Mbps
    
    def set_link_capacity(self, switch: str, port: int, capacity_mbps: float) -> None:
        """Set capacity for a specific switch-port."""
        self.link_capacities[(switch, port)] = capacity_mbps
    
    def get_link_capacity(self, switch: str, port: int) -> float:
        """Get capacity for a specific switch-port (default to configured capacity)."""
        return self.link_capacities.get((switch, port), self.default_capacity)

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
        rx_dropped = 0
        timestamp = datetime.now()

        for line in output.splitlines():
            # Real `ovs-ofctl dump-ports` output includes a drop= counter on
            # both the rx and tx lines (verified against a live OVS bridge,
            # e.g. "rx pkts=0, bytes=0, drop=0, errs=0, frame=0, over=0,
            # crc=0") -- previously unparsed, so packet_loss had no real data
            # source anywhere in this pipeline and always defaulted to None.
            port_match = re.search(
                r"port\s+(\d+):\s+rx pkts=(\d+), bytes=(\d+), drop=(\d+)",
                line,
            )

            if port_match:
                current_port = int(port_match.group(1))
                rx_packets = int(port_match.group(2))
                rx_bytes = int(port_match.group(3))
                rx_dropped = int(port_match.group(4))
                continue

            tx_match = re.search(r"tx pkts=(\d+), bytes=(\d+), drop=(\d+)", line)
            if tx_match and current_port is not None:
                tx_packets = int(tx_match.group(1))
                tx_bytes = int(tx_match.group(2))
                tx_dropped = int(tx_match.group(3))

                stats = PortStatistics(
                    timestamp=timestamp,
                    switch=switch_name,
                    port=current_port,
                    rx_packets=rx_packets,
                    rx_bytes=rx_bytes,
                    tx_packets=tx_packets,
                    tx_bytes=tx_bytes,
                    rx_dropped=rx_dropped,
                    tx_dropped=tx_dropped,
                )
                records.append(stats)
                current_port = None

        return records

    def calculate_rates(
        self,
        stats_list: List[PortStatistics],
        sample_time: Optional[float] = None,
    ) -> List[PortStatistics]:
        """
        Week 2 Day 1: Calculate RX/TX rates using actual time interval.
        Uses real query time difference between samples, not fixed assumptions.
        
        Args:
            stats_list: Current port statistics
            sample_time: Optional explicit sample time (if None, uses current time)
        
        Returns:
            Port statistics with rate fields calculated
        """
        result = []
        current_time = sample_time if sample_time is not None else time.time()

        for stats in stats_list:
            key = (stats.switch, stats.port)
            prev = self.previous_stats.get(key)

            rx_mbps = 0.0
            tx_mbps = 0.0

            if prev is not None:
                prev_time, prev_rx, prev_tx = prev
                delta_t = current_time - prev_time

                if delta_t > 0.001:  # Avoid division by near-zero
                    # Calculate byte delta (handle counter wraps gracefully)
                    delta_rx = stats.rx_bytes - prev_rx
                    delta_tx = stats.tx_bytes - prev_tx
                    
                    # If counter appears to wrap (unlikely in short intervals), use 0
                    if delta_rx < 0:
                        delta_rx = 0
                    if delta_tx < 0:
                        delta_tx = 0
                    
                    # Convert bytes to Mbps (bits per second / 1e6)
                    rx_mbps = delta_rx * 8 / delta_t / 1_000_000
                    tx_mbps = delta_tx * 8 / delta_t / 1_000_000

            # Save current state for next calculation
            self.previous_stats[key] = (current_time, stats.rx_bytes, stats.tx_bytes)

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
                rx_dropped=stats.rx_dropped,
                tx_dropped=stats.tx_dropped,
            )
            result.append(updated_stats)

        return result
    
    def calculate_utilization(
        self,
        port_stats: PortStatistics,
    ) -> float:
        """
        Week 2 Day 2: Calculate link utilization for a port.
        Utilization is max(rx_mbps, tx_mbps) / link capacity.
        
        Args:
            port_stats: Port statistics with rates calculated
        
        Returns:
            Utilization value (0.0 to 1.0)
        """
        capacity = self.get_link_capacity(port_stats.switch, port_stats.port)
        if capacity <= 0:
            return 0.0
        
        # Use the maximum of RX and TX as the direction carrying traffic
        traffic_rate = max(port_stats.rx_mbps, port_stats.tx_mbps)
        
        return min(traffic_rate / capacity, 1.0)

    def calculate_loss_rate(self, port_stats: PortStatistics) -> float:
        """
        Real packet loss rate for a port, from OVS's own drop counters
        (rx_dropped/tx_dropped -- see parse_ovs_port_stats). Uses the tx side
        (packets this port tried to send out that were dropped, e.g. from
        queue overflow) since that's the direct signal of egress congestion
        on this link; rx_dropped reflects drops on the *other* interface's
        send path, not this one's.
        """
        attempted = port_stats.tx_packets + port_stats.tx_dropped
        if attempted <= 0:
            return 0.0
        return min(port_stats.tx_dropped / attempted, 1.0)

    def aggregate_link_statistics(
        self,
        port_stats_list: List[PortStatistics],
        link_mapper,
    ) -> List[LinkStatistics]:
        """
        Week 2 Day 2: Aggregate port statistics to link-level statistics.
        Maps port stats to bidirectional link stats.
        
        Args:
            port_stats_list: Port-level statistics
            link_mapper: LinkMapper instance with port-to-link mapping
        
        Returns:
            List of link-level statistics
        """
        link_stats: Dict[str, LinkStatistics] = {}
        
        for port_stats in port_stats_list:
            # Find which link this port belongs to
            link_info = link_mapper.get_link_for_port(port_stats.switch, port_stats.port)
            if link_info is None:
                continue
            
            link_id, is_direction_a = link_info
            utilization = self.calculate_utilization(port_stats)
            loss_rate = self.calculate_loss_rate(port_stats)

            if link_id not in link_stats:
                # Initialize new link statistics
                link_stats[link_id] = LinkStatistics(
                    timestamp=port_stats.timestamp,
                    link_id=link_id,
                    utilization=utilization,
                    rx_mbps=port_stats.rx_mbps,
                    tx_mbps=port_stats.tx_mbps,
                    status="up",
                    packet_loss=loss_rate,
                )
            else:
                # Update existing link stats - combine both directions.
                # packet_loss takes the worse (max) of the two ports' loss
                # rates, same reasoning as utilization: either end dropping
                # packets means the link is lossy.
                existing = link_stats[link_id]
                existing_loss = existing.packet_loss if existing.packet_loss is not None else 0.0
                link_stats[link_id] = LinkStatistics(
                    timestamp=port_stats.timestamp,
                    link_id=link_id,
                    utilization=max(existing.utilization, utilization),
                    rx_mbps=existing.rx_mbps + port_stats.rx_mbps,
                    tx_mbps=existing.tx_mbps + port_stats.tx_mbps,
                    status="up",
                    packet_loss=max(existing_loss, loss_rate),
                )

        return list(link_stats.values())

    def save_to_csv(
        self,
        stats_list: List[PortStatistics],
        filename: str = "port_statistics.csv",
    ) -> None:
        """Save port statistics to CSV file."""
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
    
    def save_link_stats_to_csv(
        self,
        link_stats_list: List[LinkStatistics],
        filename: str = "link_statistics.csv",
    ) -> None:
        """
        Week 2: Save link-level statistics to CSV.
        Includes timestamp, link_id, utilization, rates, status, delay, loss.
        """
        filepath = self.output_dir / filename
        file_exists = filepath.exists()
        
        with filepath.open("a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            
            if not file_exists:
                writer.writerow([
                    "timestamp",
                    "link_id",
                    "utilization",
                    "rx_mbps",
                    "tx_mbps",
                    "status",
                    "delay_ms",
                    "packet_loss",
                ])
            
            for stats in link_stats_list:
                writer.writerow([
                    stats.timestamp.strftime("%Y-%m-%d %H:%M:%S.%f"),
                    stats.link_id,
                    f"{stats.utilization:.4f}",
                    f"{stats.rx_mbps:.4f}",
                    f"{stats.tx_mbps:.4f}",
                    stats.status,
                    f"{stats.delay_ms:.2f}" if stats.delay_ms is not None else "",
                    f"{stats.packet_loss:.6f}" if stats.packet_loss is not None else "",
                ])
