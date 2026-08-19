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
from .delay_jitter_tracker import DelayJitterTracker
from .models import LinkStatistics
from ..routing.congestion_model import predicted_delay_ms


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
        self.delay_jitter = DelayJitterTracker()

        self.last_update_time: Optional[datetime] = None

    def record_link_churn(self, link_id: str, timestamp: Optional[float] = None) -> None:
        """Record that a link was just added to or removed from an installed path."""
        self.link_churn.record_change(link_id, now=timestamp)

    def get_link_churn_score(self, link_id: str, now: Optional[float] = None) -> float:
        """
        Normalized [0.0, 1.0] instability score -- see LinkChurnTracker.

        now defaults to real wall-clock time (LinkChurnTracker.get_churn_score's
        own default) -- fine for real deployment, where record_link_churn's
        timestamp= is also normally left to default to real time. But before
        2026-08-12 this method had no `now` parameter at all, while
        record_link_churn already accepted an explicit timestamp -- any
        caller recording churn against a synthetic clock (e.g. an offline
        experiment using now_s starting from 0, not real time.time()) would
        record correctly but then always read back 0.0, since the window-
        eviction check inside LinkChurnTracker.get_churn_score compared that
        small synthetic timestamp against real wall-clock time and evicted it
        as (falsely) 60+ seconds stale on every read. Found by
        experiments/decision_churn_independence.py, which needs exactly this
        to test delta/epsilon offline the same way the rest of this project's
        scenario experiments (experiments/*.py) already drive DecisionEngine
        with a synthetic now_s clock instead of real sleeps.
        """
        return self.link_churn.get_churn_score(link_id, now=now)

    def record_delay_residual(self, link_id: str, residual_ms: float, now: Optional[float] = None) -> None:
        """Record a fresh delay-residual observation for jitter tracking."""
        self.delay_jitter.record_delay_residual(link_id, residual_ms, now=now)

    def get_delay_jitter_score(self, link_id: str, now: Optional[float] = None) -> float:
        """
        Normalized [0.0, 1.0] instability score -- see DelayJitterTracker.

        now behaves the same way get_link_churn_score's does: pass the same
        clock (synthetic or real) used elsewhere in a given caller's flow so
        the rolling window is evaluated consistently, not silently against
        real wall-clock time when the rest of a scenario runs on a synthetic
        now_s.
        """
        return self.delay_jitter.get_jitter_score(link_id, now=now)

    def update_link_statistics(self, link_stats: LinkStatistics, now: Optional[float] = None) -> None:
        """
        Update the state with new link statistics.

        Args:
            link_stats: New statistics for a single link
            now: clock value to feed the jitter tracker with (see
                get_delay_jitter_score's docstring on why this needs to
                match whatever clock a caller later reads jitter against).
                Defaults to link_stats.timestamp converted to an epoch
                float -- correct for real deployment, where both recording
                and reading naturally use real wall-clock time. An offline
                experiment driving everything off a synthetic now_s clock
                (as this project's scenario experiments do -- see
                experiments/simulation_common.py's set_link_condition)
                must pass that same now_s here, or every sample gets
                recorded against real time while later reads use a tiny
                synthetic value -- the eviction cutoff then never exceeds
                any real timestamp, so the "rolling window" never actually
                rolls; it silently accumulates every sample for the whole
                experiment instead of reflecting only the last
                window_seconds. (Same root cause as the churn `now`-facade
                bug fixed 2026-08-12, mirror-imaged: that one made an
                offline signal always read 0 by over-evicting; this one
                would make it never evict.)
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

        # Feed the jitter tracker with this sample's delay residual, so
        # get_delay_jitter_score() reflects real, current per-link
        # dispersion (see DelayJitterTracker's docstring for why this
        # exists).
        if link_stats.delay_ms is not None:
            residual_ms = float(link_stats.delay_ms) - predicted_delay_ms(float(link_stats.utilization))
            jitter_now = now if now is not None else link_stats.timestamp.timestamp()
            self.record_delay_residual(link_stats.link_id, residual_ms, now=jitter_now)

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
