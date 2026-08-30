#!/usr/bin/env python3
"""
Network State - central network state manager, exposing
get_network_state() as the shared read interface for routing modules.
"""
import json
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, List

from .link_monitor import LinkMonitor
from .topology_state import TopologyState
from .history_store import HistoryStore
from .link_churn_tracker import LinkChurnTracker
from .link_flap_tracker import LinkFlapTracker
from .delay_jitter_tracker import DelayJitterTracker
from .loss_jitter_tracker import LossJitterTracker
from .models import LinkStatistics
from ..routing.congestion_model import predicted_delay_ms, predicted_loss


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
        jitter_settle_window_seconds: float = 10.0,
    ):
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self.topology = TopologyState()
        self.link_monitor = LinkMonitor(output_dir=output_dir)
        self.history = HistoryStore(window_size=history_window_size, output_dir=output_dir)
        self.link_churn = LinkChurnTracker()
        self.link_flap = LinkFlapTracker()
        self.delay_jitter = DelayJitterTracker()
        self.loss_jitter = LossJitterTracker()
        # How long after a link was last churned (added to/removed from an
        # installed path) to exclude its delay samples from jitter tracking
        # -- see update_link_statistics. Defaults to the same magnitude as
        # config/decision.yaml's hold_down.duration_seconds (10s), the
        # closest existing "how long until things settle after a switch"
        # concept in this project, though not literally coupled to it --
        # hold_down is scoped per src-dst flow pair inside
        # DecisionEngine/StabilityManager, not per link, and NetworkState
        # (monitor layer) shouldn't reach up into the decision layer's
        # state to read it.
        self.jitter_settle_window_seconds = jitter_settle_window_seconds

        self.last_update_time: Optional[datetime] = None

    def record_link_churn(self, link_id: str, timestamp: Optional[float] = None) -> None:
        """Record that a link was just added to or removed from an installed path."""
        self.link_churn.record_change(link_id, now=timestamp)

    def get_link_churn_score(self, link_id: str, now: Optional[float] = None) -> float:
        """
        Normalized [0.0, 1.0] instability score -- see LinkChurnTracker.

        now defaults to real wall-clock time, fine for real deployment. A
        caller driving DecisionEngine on a synthetic clock (e.g. an offline
        experiment using now_s starting from 0) must pass that same now_s
        here -- otherwise the window-eviction check inside
        LinkChurnTracker.get_churn_score compares a small synthetic
        timestamp against real wall-clock time and evicts every sample as
        falsely stale, always reading back 0.0.
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

    def record_loss_residual(self, link_id: str, residual: float, now: Optional[float] = None) -> None:
        """Record a fresh loss-residual observation for jitter tracking."""
        self.loss_jitter.record_loss_residual(link_id, residual, now=now)

    def get_loss_jitter_score(self, link_id: str, now: Optional[float] = None) -> float:
        """Normalized [0.0, 1.0] instability score -- see LossJitterTracker."""
        return self.loss_jitter.get_jitter_score(link_id, now=now)

    def get_link_flap_score(self, link_id: str, now: Optional[float] = None) -> float:
        """Normalized [0.0, 1.0] real up/down flapping score -- see LinkFlapTracker."""
        return self.link_flap.get_flap_score(link_id, now=now)

    def get_abnormal_loss_score(self, link_id: str, now: Optional[float] = None) -> float:
        """Normalized [0.0, 1.0] sustained-high-loss score -- see LossJitterTracker.get_abnormal_loss_score."""
        return self.loss_jitter.get_abnormal_loss_score(link_id, now=now)

    def get_traffic_growth_score(self, link_id: str) -> float:
        """Normalized [0.0, 1.0] abnormal-traffic-growth score -- see LinkHistory.traffic_growth_score."""
        return self.history.get_or_create_history(link_id).traffic_growth_score

    def get_resilience_score(self, link_id: str, now: Optional[float] = None) -> float:
        """
        Normalized [0.0, 1.0] combined resilience/anomaly score: the worst of flapping,
        sustained abnormal loss, and abnormal traffic growth -- max, not sum, so multiple
        simultaneous signals don't over-penalize past what any one of them alone already
        means ("avoid this link"). Independent of the alpha..eta path-cost formula (see
        GraphBuilder.build_weighted_graph's resilience-avoidance filter) -- this is a
        structural avoidance signal, not another cost-formula term.
        """
        return max(
            self.get_link_flap_score(link_id, now=now),
            self.get_abnormal_loss_score(link_id, now=now),
            self.get_traffic_growth_score(link_id),
        )

    def update_link_statistics(self, link_stats: LinkStatistics, now: Optional[float] = None) -> None:
        """
        Update the state with new link statistics.

        Args:
            link_stats: New statistics for a single link
            now: clock value to feed the jitter tracker with (see
                get_delay_jitter_score's docstring on why this needs to
                match whatever clock a caller later reads jitter against).
                Defaults to link_stats.timestamp converted to an epoch
                float -- correct for real deployment. An offline experiment
                driving everything off a synthetic now_s clock (see
                experiments/simulation_common.py's set_link_condition) must
                pass that same now_s here, or every sample is recorded
                against real time while later reads use a tiny synthetic
                value -- the eviction cutoff then never exceeds any real
                timestamp, so the rolling window never actually rolls; it
                silently accumulates every sample for the whole run instead
                of reflecting only the last window_seconds.
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
        # dispersion -- but skip samples taken while this link is still
        # within its post-switch settle window: a switch event itself can
        # cause a real, transient delay blip that has nothing to do with
        # steady-state jitter, and recording it here would let delta
        # (control-plane churn) and zeta (data-plane jitter) partly
        # double-count the same underlying reroute event.
        if link_stats.delay_ms is not None or link_stats.packet_loss is not None:
            jitter_now = now if now is not None else link_stats.timestamp.timestamp()
            settling = self.link_churn.has_changed_recently(
                link_stats.link_id, within_seconds=self.jitter_settle_window_seconds, now=jitter_now
            )
            if not settling:
                if link_stats.delay_ms is not None:
                    residual_ms = float(link_stats.delay_ms) - predicted_delay_ms(float(link_stats.utilization))
                    self.record_delay_residual(link_stats.link_id, residual_ms, now=jitter_now)
                if link_stats.packet_loss is not None:
                    # Same settle-window exclusion as delay jitter, and the
                    # same rationale: a switch event's transient blip
                    # shouldn't count as steady-state loss dispersion either.
                    loss_residual = float(link_stats.packet_loss) - predicted_loss(float(link_stats.utilization))
                    self.record_loss_residual(link_stats.link_id, loss_residual, now=jitter_now)

        self.last_update_time = link_stats.timestamp
    
    def set_link_status(self, link_id: str, is_up: bool, now: Optional[float] = None) -> None:
        """
        Explicitly set a link's status (up/down).

        Args:
            link_id: Link identifier (e.g., "s1-s2")
            is_up: True for up, False for down
            now: clock value for the flap tracker (see get_delay_jitter_score's docstring on
                why a synthetic-clock caller must pass its own now_s here). Defaults to real
                wall-clock time.
        """
        status = "up" if is_up else "down"
        # A real transition, not the link's first-ever status report -- get_link_flap_score
        # should only count actual flaps, and every link's very first observation would
        # otherwise register as a spurious "recovery" or "failure" against nothing.
        previous_status = self.link_monitor.get_link_status(link_id)
        if previous_status is not None and previous_status != status:
            self.link_flap.record_transition(link_id, now=now)

        self.link_monitor.set_link_status(link_id, status)

        # Also update topology graph if needed
        if "-" in link_id:
            u, v = link_id.split("-", 1)
            self.topology.set_link_status(u, v, is_up)
    
    def get_network_state(self) -> Dict:
        """
        Expose complete network state to routing modules, in-process --
        no REST calls needed since routing and monitoring share this state
        directly.

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
        """Save current network state snapshot to JSON."""
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
