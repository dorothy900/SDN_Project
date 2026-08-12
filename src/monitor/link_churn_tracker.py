#!/usr/bin/env python3
"""
Link Churn Tracker - Track how recently/often each link has been part of an
installed path change.

Feeds the path-cost formula's previously-inert "priority" term (see
src/routing/graph_builder.py): a link that keeps getting added to or removed
from installed paths is a real stability signal a cost comparison can use --
"prefer this candidate over that one partly because that one's links have
been getting churned" -- distinct from (and complementary to) the existing
timing gates (persistence, hold-down, change budget), which control *when*
a reroute is allowed, not *which* candidate looks better once one is.
"""
from __future__ import annotations

import time
from collections import deque
from typing import Deque, Dict, Optional


class LinkChurnTracker:
    """
    Rolling-window count of how often each link has recently changed status
    (added to or removed from an installed path). Same sliding-window
    pattern src/decision/change_budget.py already uses for its own rate
    limiting (timestamps in a deque, evict anything older than the window on
    each check) -- not a new technique, applied to a new signal.
    """

    def __init__(self, window_seconds: float = 60.0, saturation_count: int = 5):
        if window_seconds <= 0:
            raise ValueError("window_seconds must be > 0, got %r" % window_seconds)
        if saturation_count <= 0:
            raise ValueError("saturation_count must be > 0, got %r" % saturation_count)
        self.window_seconds = window_seconds
        self.saturation_count = saturation_count
        self._change_times: Dict[str, Deque[float]] = {}

    def record_change(self, link_id: str, now: Optional[float] = None) -> None:
        """Record that this link was just added to or removed from an installed path."""
        ts = now if now is not None else time.time()
        self._change_times.setdefault(link_id, deque()).append(ts)

    def get_churn_score(self, link_id: str, now: Optional[float] = None) -> float:
        """
        Normalized instability score in [0.0, 1.0]: how many times this link
        has changed within the rolling window, capped at saturation_count --
        more than that many recent changes is "maximally unstable", not
        unboundedly worse (mirrors ChangeBudget's burst_allowance idea of a
        saturating rather than unbounded penalty).
        """
        times = self._change_times.get(link_id)
        if not times:
            return 0.0
        ts = now if now is not None else time.time()
        cutoff = ts - self.window_seconds
        while times and times[0] < cutoff:
            times.popleft()
        if not times:
            return 0.0
        return min(len(times) / self.saturation_count, 1.0)

    def reset(self) -> None:
        """Clear all tracked history (mainly for test isolation)."""
        self._change_times.clear()
