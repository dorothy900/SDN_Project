#!/usr/bin/env python3
"""
Traffic Generator - Build deterministic traffic timelines for pilot experiments.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, List, Optional


@dataclass
class FlowDefinition:
    """Compact flow description used by offline scenario runners."""

    flow_id: str
    service_type: str
    protocol: str
    src: str
    dst: str
    start_s: int
    duration_s: int
    offered_load_mbps: float
    packet_size_bytes: int


class TrafficGenerator:
    """Generate repeatable concurrent traffic mixes for Week 6 experiments."""

    def __init__(self, output_dir: Optional[Path] = None):
        self.output_dir = output_dir or Path("results/pilot")
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def generate_concurrent_flows(self) -> List[FlowDefinition]:
        """Return a deterministic set of mixed-priority flows."""
        return [
            FlowDefinition("flow-voip-1", "VoIP", "UDP", "h1", "h2", 0, 12, 12.0, 256),
            FlowDefinition("flow-video-1", "Video", "UDP", "h3", "h8", 0, 12, 24.0, 1200),
            FlowDefinition("flow-web-1", "Web", "TCP", "h5", "h12", 1, 10, 18.0, 1024),
            FlowDefinition("flow-file-1", "File Transfer", "TCP", "h7", "h15", 2, 9, 30.0, 1460),
        ]

    def timeline_for_samples(self, sample_count: int = 12) -> List[Dict[str, object]]:
        """Expand flows into a sample-by-sample activation timeline."""
        timeline: List[Dict[str, object]] = []
        for sample in range(sample_count):
            for flow in self.generate_concurrent_flows():
                active = flow.start_s <= sample < (flow.start_s + flow.duration_s)
                timeline.append(
                    {
                        "sample": sample + 1,
                        "flow_id": flow.flow_id,
                        "service_type": flow.service_type,
                        "protocol": flow.protocol,
                        "src": flow.src,
                        "dst": flow.dst,
                        "active": active,
                        "offered_load_mbps": flow.offered_load_mbps if active else 0.0,
                    }
                )
        return timeline

    def write_log(self, output_path: Optional[Path] = None) -> Path:
        """Persist a readable generator log for the Week 6 Day 1 deliverable."""
        log_path = output_path or (self.output_dir / "traffic_generator.log")
        lines = ["Traffic Generator Log", "====================", "Concurrent flows:"]
        for flow in self.generate_concurrent_flows():
            lines.append(
                "%s %s %s %s->%s start=%ss duration=%ss load=%.1fMbps"
                % (
                    flow.flow_id,
                    flow.service_type,
                    flow.protocol,
                    flow.src,
                    flow.dst,
                    flow.start_s,
                    flow.duration_s,
                    flow.offered_load_mbps,
                )
            )
        lines.append("")
        lines.append("Timeline samples:")
        for row in self.timeline_for_samples():
            if row["active"]:
                lines.append(
                    "sample=%d flow=%s load=%.1fMbps"
                    % (row["sample"], row["flow_id"], row["offered_load_mbps"])
                )
        log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return log_path

    def start_iperf_flow(
        self,
        client: str,
        server: str,
        duration: int = 30,
        bandwidth: str = "10M",
    ) -> Dict[str, object]:
        """Return a simulated iperf command description for offline experiments."""
        return {
            "tool": "iperf",
            "client": client,
            "server": server,
            "duration": duration,
            "bandwidth": bandwidth,
        }

    def start_ping_flow(self, src: str, dst: str, count: int = 100) -> Dict[str, object]:
        """Return a simulated ping command description for offline experiments."""
        return {
            "tool": "ping",
            "src": src,
            "dst": dst,
            "count": count,
        }

    @staticmethod
    def to_dicts(flows: List[FlowDefinition]) -> List[Dict[str, object]]:
        """Convert flow dataclasses into JSON/CSV friendly dictionaries."""
        return [asdict(flow) for flow in flows]
