#!/usr/bin/env python3
"""
Persistence Checker - Hysteresis mechanism
Verify that violations persist before acting
"""

import time
from collections import deque
from dataclasses import dataclass
from typing import Dict, Optional


@dataclass
class PersistenceWindow:
    link_id: str
    metric: str
    start_time: float
    violations: deque


class PersistenceChecker:
    """Check if violations persist long enough (hysteresis)."""

    def __init__(self, persistence_seconds: float = 5.0, cooldown_seconds: float = 10.0):
        self.persistence_seconds = persistence_seconds
        self.cooldown_seconds = cooldown_seconds

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
        """Record a violation and check if persistence requirement is met."""
        key = (link_id, metric)

        if key not in self.active_windows:
            if not self.start_window(link_id, metric):
                return False

        window = self.active_windows[key]
        window.violations.append((time.time(), value))

        elapsed = time.time() - window.start_time
        return elapsed >= self.persistence_seconds

    def check_persistence(self, link_id: str, metric: str) -> bool:
        """Check if condition has persisted long enough."""
        key = (link_id, metric)
        if key not in self.active_windows:
            return False

        elapsed = time.time() - self.active_windows[key].start_time
        return elapsed >= self.persistence_seconds

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
