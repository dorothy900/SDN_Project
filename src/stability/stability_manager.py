#!/usr/bin/env python3
"""
Stability Manager - Coordinate hysteresis and hold-down control.
"""

from __future__ import annotations

import time
from typing import Dict, Optional, Tuple


class StabilityManager:
    """Manage congestion state transitions and hold-down timing together."""

    def __init__(
        self,
        hold_down_seconds: float = 10.0,
        enter_threshold: float = 0.7,
        release_threshold: float = 0.65,
    ):
        if hold_down_seconds < 0:
            raise ValueError("hold_down_seconds must be >= 0, got %r" % hold_down_seconds)
        if not (0.0 <= enter_threshold <= 1.0):
            raise ValueError("enter_threshold must be in [0.0, 1.0], got %r" % enter_threshold)
        if not (0.0 <= release_threshold <= 1.0):
            raise ValueError("release_threshold must be in [0.0, 1.0], got %r" % release_threshold)
        if release_threshold > enter_threshold:
            raise ValueError(
                "release_threshold (%r) must be <= enter_threshold (%r) -- otherwise a link can "
                "release from the congested state at a utilization still above enter_threshold, "
                "flapping instead of the damping hysteresis is meant to provide"
                % (release_threshold, enter_threshold)
            )
        self.hold_down_seconds = hold_down_seconds
        self.enter_threshold = enter_threshold
        self.release_threshold = release_threshold
        self.hold_down_end: Dict[Tuple[str, str], float] = {}
        self.congestion_state: Dict[str, str] = {}

    def update_congestion_state(self, key: str, utilization: float) -> Dict[str, object]:
        """
        Apply an enter/release state machine so near-threshold oscillation is damped.
        """
        previous = self.congestion_state.get(key, "normal")
        current = previous
        transition = "steady"

        if previous == "normal" and utilization >= self.enter_threshold:
            current = "congested"
            transition = "enter_congestion"
        elif previous == "congested" and utilization <= self.release_threshold:
            current = "normal"
            transition = "release_congestion"

        self.congestion_state[key] = current
        return {
            "key": key,
            "previous_state": previous,
            "current_state": current,
            "utilization": round(utilization, 6),
            "transition": transition,
        }

    def is_congested(self, key: str) -> bool:
        """Return whether the state machine currently marks the key as congested."""
        return self.congestion_state.get(key, "normal") == "congested"

    def is_in_hold_down(self, src_dst: Tuple[str, str], now: Optional[float] = None) -> bool:
        """Check whether reroutes for this pair are still blocked by hold-down."""
        if src_dst not in self.hold_down_end:
            return False
        check_time = time.time() if now is None else now
        return check_time < self.hold_down_end[src_dst]

    def start_hold_down(self, src_dst: Tuple[str, str], now: Optional[float] = None) -> None:
        """Start or refresh hold-down after a successful ordinary reroute."""
        start_time = time.time() if now is None else now
        self.hold_down_end[src_dst] = start_time + self.hold_down_seconds

    def clear_hold_down(self, src_dst: Tuple[str, str]) -> None:
        """Clear hold-down state for a pair."""
        if src_dst in self.hold_down_end:
            del self.hold_down_end[src_dst]

    def allow_reroute(
        self,
        src_dst: Tuple[str, str],
        emergency: bool = False,
        now: Optional[float] = None,
    ) -> bool:
        """
        Allow reroute immediately for emergencies, otherwise enforce hold-down.
        """
        if emergency:
            return True
        return not self.is_in_hold_down(src_dst, now=now)

    def record_reroute(
        self,
        src_dst: Tuple[str, str],
        emergency: bool = False,
        now: Optional[float] = None,
    ) -> None:
        """Record a reroute and begin hold-down for ordinary changes."""
        if not emergency:
            self.start_hold_down(src_dst, now=now)
