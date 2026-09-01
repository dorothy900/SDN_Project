#!/usr/bin/env python3
"""
History Store - store historical network data in a rolling window,
exposing current/mean/max/trend summaries.
"""
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional


class LinkHistory:
    """
    Per-link history buffer with statistical calculations.
    Maintains a rolling window and provides current, mean, max, trend.
    """
    
    def __init__(self, window_size: int = 60):
        self.window_size = window_size
        self.utilization_window: deque = deque(maxlen=window_size)
        self.rx_mbps_window: deque = deque(maxlen=window_size)
        self.tx_mbps_window: deque = deque(maxlen=window_size)
        self.timestamps: deque = deque(maxlen=window_size)
    
    def add_sample(
        self,
        timestamp: datetime,
        utilization: float,
        rx_mbps: float,
        tx_mbps: float,
    ) -> None:
        """
        Add a new sample to the history window.
        
        Args:
            timestamp: Sample timestamp
            utilization: Link utilization (0.0-1.0)
            rx_mbps: Receive rate in Mbps
            tx_mbps: Transmit rate in Mbps
        """
        self.timestamps.append(timestamp)
        self.utilization_window.append(utilization)
        self.rx_mbps_window.append(rx_mbps)
        self.tx_mbps_window.append(tx_mbps)
    
    @property
    def current_utilization(self) -> Optional[float]:
        """Get most recent utilization value."""
        return self.utilization_window[-1] if self.utilization_window else None
    
    @property
    def mean_utilization(self) -> Optional[float]:
        """Get mean utilization in the window."""
        if not self.utilization_window:
            return None
        return sum(self.utilization_window) / len(self.utilization_window)
    
    @property
    def max_utilization(self) -> Optional[float]:
        """Get maximum utilization in the window."""
        return max(self.utilization_window) if self.utilization_window else None
    
    @property
    def utilization_trend(self) -> Optional[float]:
        """
        Calculate trend: positive if recent values are increasing.
        Compares last 20% vs first 20% of the window.
        """
        window = list(self.utilization_window)
        n = len(window)
        if n < 5:
            return None

        segment = max(1, n // 5)
        first_avg = sum(window[:segment]) / segment
        last_avg = sum(window[-segment:]) / segment
        
        return last_avg - first_avg
    
    # Same 3-sigma shift-detection convention as LossJitterTracker.get_abnormal_loss_score
    # (Shewhart-control-chart style: a shift of 3+ baseline standard deviations is the
    # standard statistical-process-control threshold for "real anomaly, not normal
    # variation"). Kept as its own constant rather than importing LossJitterTracker's --
    # same value, deliberately not coupled across these two otherwise-independent modules.
    SIGMA_CAP = 3.0

    @property
    def traffic_growth_score(self) -> float:
        """
        Normalized [0.0, 1.0] "abnormal traffic growth" score: splits tx_mbps_window into
        an earlier baseline half and a later recent half, then scores how many baseline
        standard deviations the recent half's mean has shifted upward (see SIGMA_CAP) --
        not a fixed relative-growth ratio, and scale-free by construction (the shift is
        measured in units of the link's own baseline variability, not a hand-picked Mbps or
        percentage cutoff). Floored at 0 (only growth counts, not decline). Returns
        0.0 with fewer than 2 samples in either half.

        When the baseline half is perfectly flat (std ~= 0) the z-score is
        undefined, so the score falls back to the fraction of the recent half that
        sits above the baseline -- a single spike poll scores ~1/len(recent), a
        sustained ramp still scores near 1.0. Same single-poll false-positive guard
        as LossJitterTracker.get_abnormal_loss_score.
        """
        window = list(self.tx_mbps_window)
        half = len(window) // 2
        if half < 2:
            return 0.0
        baseline, recent = window[:half], window[half:]
        baseline_mean = sum(baseline) / len(baseline)
        baseline_variance = sum((v - baseline_mean) ** 2 for v in baseline) / (len(baseline) - 1)
        baseline_std = baseline_variance ** 0.5
        recent_mean = sum(recent) / len(recent)
        shift = recent_mean - baseline_mean
        if shift <= 0:
            return 0.0
        if baseline_std < 1e-9:
            above = sum(1 for v in recent if v > baseline_mean + 1e-9)
            return above / len(recent)
        z = shift / baseline_std
        return min(z / self.SIGMA_CAP, 1.0)

    @property
    def current_rx_mbps(self) -> Optional[float]:
        return self.rx_mbps_window[-1] if self.rx_mbps_window else None
    
    @property
    def current_tx_mbps(self) -> Optional[float]:
        return self.tx_mbps_window[-1] if self.tx_mbps_window else None
    
    def get_summary(self) -> Dict:
        """Get a summary dict of all statistics."""
        return {
            "current": self.current_utilization,
            "mean": self.mean_utilization,
            "max": self.max_utilization,
            "trend": self.utilization_trend,
            "sample_count": len(self.utilization_window),
        }


class HistoryStore:
    """Store historical network state data across all links."""

    def __init__(self, window_size: int = 60, output_dir: Path = Path("results/network_state")):
        self.window_size = window_size
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        self.link_histories: Dict[str, LinkHistory] = {}
        self.global_history: List[Dict] = []
    
    def get_or_create_history(self, link_id: str) -> LinkHistory:
        """Get or create history buffer for a link."""
        if link_id not in self.link_histories:
            self.link_histories[link_id] = LinkHistory(window_size=self.window_size)
        return self.link_histories[link_id]
    
    def add_link_sample(
        self,
        link_id: str,
        timestamp: datetime,
        utilization: float,
        rx_mbps: float,
        tx_mbps: float,
        status: str = "up",
    ) -> None:
        """
        Add a sample for a specific link.
        
        Args:
            link_id: Link identifier
            timestamp: Sample time
            utilization: Link utilization
            rx_mbps: RX rate
            tx_mbps: TX rate
            status: Link status ("up" or "down")
        """
        history = self.get_or_create_history(link_id)
        history.add_sample(timestamp, utilization, rx_mbps, tx_mbps)
        
        # Add to global history for CSV export
        self.global_history.append({
            "timestamp": timestamp,
            "link_id": link_id,
            "utilization": utilization,
            "rx_mbps": rx_mbps,
            "tx_mbps": tx_mbps,
            "status": status,
        })
    
    def get_link_summary(self, link_id: str) -> Optional[Dict]:
        """Get statistical summary for a link."""
        if link_id not in self.link_histories:
            return None
        return self.link_histories[link_id].get_summary()
    
    def get_all_summaries(self) -> Dict[str, Dict]:
        """Get summaries for all links."""
        return {
            link_id: hist.get_summary()
            for link_id, hist in self.link_histories.items()
        }
    
    def save_history_to_csv(self, filename: str = "history_window_test.csv") -> None:
        """Save global history to CSV for verification."""
        import csv
        
        filepath = self.output_dir / filename
        
        if not self.global_history:
            return
        
        with filepath.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=[
                "timestamp", "link_id", "utilization", 
                "rx_mbps", "tx_mbps", "status"
            ])
            writer.writeheader()
            
            for entry in self.global_history:
                # Convert datetime to string
                row = entry.copy()
                row["timestamp"] = entry["timestamp"].strftime("%Y-%m-%d %H:%M:%S.%f")
                writer.writerow(row)
