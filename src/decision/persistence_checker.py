#!/usr/bin/env python3
"""
Persistence Checker - Hysteresis mechanism.
Require congestion to persist across a configurable number of samples.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass
from typing import Deque, Dict, List, Optional


@dataclass
class PersistenceWindow:
    link_id: str
    metric: str
    start_time: float
    violations: Deque[tuple]


class PersistenceChecker:
    """Check if violations persist long enough (hysteresis)."""

    def __init__(
        self,
        persistence_seconds: float = 5.0,
        cooldown_seconds: float = 10.0,
        required_samples: int = 3,
    ):
        self.persistence_seconds = persistence_seconds
        self.cooldown_seconds = cooldown_seconds
        self.required_samples = required_samples

        self.active_windows: Dict[tuple, PersistenceWindow] = {}  # (link, metric) -> window
        self.last_reroute_time: Dict[tuple, float] = {}  # (link, metric) -> timestamp

    def start_window(self, link_id: str, metric: str):
        """Start tracking a potential violation."""
        key = (link_id, metric)
        now = time.time()

        # Check cooldown
        last_reroute = self.last_reroute_time.get(key, 0)
        if now - last_reroute < self.cooldown_seconds:
            return False  # In cooldown, don't track

        if key not in self.active_windows:
            self.active_windows[key] = PersistenceWindow(
                link_id=link_id,
                metric=metric,
                start_time=now,
                violations=deque(maxlen=100)
            )
        return True

    def record_violation(self, link_id: str, metric: str, value: float) -> bool:
        """Record a violating sample and return True once persistence is met."""
        key = (link_id, metric)

        if key not in self.active_windows:
            if not self.start_window(link_id, metric):
                return False

        window = self.active_windows[key]
        window.violations.append((time.time(), value))
        return self.check_persistence(link_id, metric)

    def check_persistence(self, link_id: str, metric: str) -> bool:
        """Check if condition has persisted long enough."""
        key = (link_id, metric)
        if key not in self.active_windows:
            return False

        window = self.active_windows[key]
        elapsed = time.time() - window.start_time
        enough_samples = len(window.violations) >= self.required_samples
        return enough_samples and elapsed >= self.persistence_seconds

    def evaluate_sample(
        self,
        link_id: str,
        metric: str,
        value: float,
        is_violation: bool,
    ) -> Dict[str, object]:
        """
        Process one sample and expose whether the violation is accepted.

        This mirrors the Week 4 Day 2 validation method of comparing short spikes
        versus sustained overload across a configurable number of samples.
        """
        if not is_violation:
            self.clear_window(link_id, metric)
            return {
                "accepted": False,
                "sample_count": 0,
                "window_age": 0.0,
                "reason": "below_threshold",
            }

        accepted = self.record_violation(link_id, metric, value)
        sample_count = self.get_violation_count(link_id, metric)
        window_age = self.get_window_age(link_id, metric) or 0.0
        return {
            "accepted": accepted,
            "sample_count": sample_count,
            "window_age": round(window_age, 6),
            "reason": "persistent" if accepted else "collecting_samples",
        }

    def clear_window(self, link_id: str, metric: str):
        """Clear tracking window for a condition."""
        key = (link_id, metric)
        if key in self.active_windows:
            del self.active_windows[key]

    def record_reroute(self, link_id: str, metric: str):
        """Record that a reroute occurred for cooldown tracking."""
        key = (link_id, metric)
        self.last_reroute_time[key] = time.time()
        self.clear_window(link_id, metric)

    def in_cooldown(self, link_id: str, metric: str) -> bool:
        """Check if in cooldown period after reroute."""
        key = (link_id, metric)
        last_reroute = self.last_reroute_time.get(key, 0)
        return (time.time() - last_reroute) < self.cooldown_seconds

    def get_window_age(self, link_id: str, metric: str) -> Optional[float]:
        """Get age of active tracking window in seconds."""
        key = (link_id, metric)
        if key not in self.active_windows:
            return None
        return time.time() - self.active_windows[key].start_time

    def get_violation_count(self, link_id: str, metric: str) -> int:
        """Return the number of violating samples recorded in the current window."""
        key = (link_id, metric)
        if key not in self.active_windows:
            return 0
        return len(self.active_windows[key].violations)

    def get_window_samples(self, link_id: str, metric: str) -> List[float]:
        """Return recorded sample values for debugging and CSV export."""
        key = (link_id, metric)
        if key not in self.active_windows:
            return []
        return [float(value) for _, value in self.active_windows[key].violations]
