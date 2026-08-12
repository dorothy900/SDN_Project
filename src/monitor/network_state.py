#!/usr/bin/env python3
"""
Network State - Central network state manager
Week 2 Day 5: get_network_state() interface for routing modules
"""
import json
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, List

from .link_monitor import LinkMonitor
from .topology_state import TopologyState
from .history_store import HistoryStore
from .link_churn_tracker import LinkChurnTracker
from .models import LinkStatistics


class NetworkState:
    """
    Central manager for all network state.
    Combines topology, link monitoring, and history into a single interface.
    Exports get_network_state() for use by routing and decision modules.
    """

    def __init__(
        self,
        output_dir: Path = Path("results/network_state"),
        history_window_size: int = 60,
    ):
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self.topology = TopologyState()
        self.link_monitor = LinkMonitor(output_dir=output_dir)
        self.history = HistoryStore(window_size=history_window_size, output_dir=output_dir)
        self.link_churn = LinkChurnTracker()

        self.last_update_time: Optional[datetime] = None

    def record_link_churn(self, link_id: str, timestamp: Optional[float] = None) -> None:
        """Record that a link was just added to or removed from an installed path."""
        self.link_churn.record_change(link_id, now=timestamp)

    def get_link_churn_score(self, link_id: str) -> float:
        """Normalized [0.0, 1.0] instability score -- see LinkChurnTracker."""
        return self.link_churn.get_churn_score(link_id)
    
    def update_link_statistics(self, link_stats: LinkStatistics) -> None:
        """
        Update the state with new link statistics.
        
        Args:
            link_stats: New statistics for a single link
        """
        self.link_monitor.update_link_stats(link_stats)
        
        # Add to history
        self.history.add_link_sample(
            link_id=link_stats.link_id,
            timestamp=link_stats.timestamp,
            utilization=link_stats.utilization,
            rx_mbps=link_stats.rx_mbps,
            tx_mbps=link_stats.tx_mbps,
            status=link_stats.status,
        )
        
        self.last_update_time = link_stats.timestamp
    
    def set_link_status(self, link_id: str, is_up: bool) -> None:
        """
        Explicitly set a link's status (up/down).
        
        Args:
            link_id: Link identifier (e.g., "s1-s2")
            is_up: True for up, False for down
        """
        status = "up" if is_up else "down"
        self.link_monitor.set_link_status(link_id, status)
        
        # Also update topology graph if needed
        if "-" in link_id:
            u, v = link_id.split("-", 1)
            self.topology.set_link_status(u, v, is_up)
    
    def get_network_state(self) -> Dict:
        """
        Week 2 Day 5: Expose complete network state to routing modules.
        No need for REST calls - all metrics available locally!
        
        Returns:
            Dictionary containing all network state:
            - timestamp: Last update time
            - topology: Node/link structure
            - links: Current link stats and status
            - history: Statistical summaries
        """
        now = datetime.now()
        
        # Build complete state
        state = {
            "timestamp": now.isoformat(),
            "last_update": self.last_update_time.isoformat() if self.last_update_time else None,
            
            "topology": {
                "nodes": self.topology.get_nodes(),
                "active_links": self.topology.get_active_links(),
                "failed_links": self.topology.get_failed_links(),
            },
            
            "links": {},
            
            "history_summaries": {},
        }
        
        # Add current link statistics
        for link_id, stats in self.link_monitor.get_all_link_stats().items():
            if stats:
                state["links"][link_id] = {
                    "status": stats.status,
                    "utilization": stats.utilization,
                    "rx_mbps": stats.rx_mbps,
                    "tx_mbps": stats.tx_mbps,
                    "delay_ms": stats.delay_ms,
                    "packet_loss": stats.packet_loss,
                }
        
        # Add history summaries
        state["history_summaries"] = self.history.get_all_summaries()
        
        return state
    
    def get_link_state(self, link_id: str) -> Optional[Dict]:
        """Get state for a single link."""
        full_state = self.get_network_state()
        return full_state["links"].get(link_id)

    def get_link_stats(self, link_id: str) -> Optional[LinkStatistics]:
        """Return the latest typed statistics object for a link."""
        return self.link_monitor.get_link_stats(link_id)

    def get_active_graph(self):
        """Expose the currently active topology graph to routing modules."""
        return self.topology.get_active_graph()
    
    def save_state_snapshot(self, filename: str = "network_state_snapshot.json") -> None:
        """
        Save current network state snapshot to JSON.
        Week 2 Day 5 output - print annotated snapshot.
        """
        state = self.get_network_state()
        filepath = self.output_dir / filename
        
        with filepath.open("w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, default=str)
    
    def get_active_links(self) -> List[str]:
        """Get list of active link IDs."""
        return [
            link_id
            for link_id, status in self.link_monitor.get_all_statuses().items()
            if status == "up"
        ]
