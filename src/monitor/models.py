#!/usr/bin/env python3
"""
Data Models - Representations of network statistics and link status
"""

from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional

@dataclass
class PortStatistics:
    """Port-level statistics from OVS/OpenDaylight."""
    timestamp: datetime
    switch: str
    port: int
    rx_packets: int
    rx_bytes: int
    tx_packets: int
    tx_bytes: int
    rx_mbps: float = 0.0
    tx_mbps: float = 0.0


@dataclass
class LinkStatistics:
    """Aggregated link statistics for monitoring."""
    timestamp: datetime
    link_id: str  # "s1-s2" format
    utilization: float  # 0.0 to 1.0
    rx_mbps: float
    tx_mbps: float
    status: str = "up"
    delay_ms: Optional[float] = None
    packet_loss: Optional[float] = None


@dataclass
class RoutingDecision:
    """Record of routing change decisions."""
    timestamp: datetime
    reason: str  # "congestion", "link_failure", "optimization"
    old_path: List[str]
    new_path: List[str]
    triggered_by_stability: bool = False
