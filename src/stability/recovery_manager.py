#!/usr/bin/env python3
"""
Recovery Manager - Protect against unstable switch-back after restoration.
"""

from __future__ import annotations

import time
from typing import Dict, Optional


class RecoveryManager:
    """Observe restored links until they remain stable for a recovery window."""

    def __init__(self, recovery_window_seconds: float = 5.0):
        self.recovery_window_seconds = recovery_window_seconds
        self.recovering_links: Dict[str, float] = {}

    def start_recovery(self, link_id: str, now: Optional[float] = None) -> None:
        """Begin monitoring a restored link."""
        start_time = time.time() if now is None else now
        self.recovering_links[link_id] = start_time

    def invalidate_recovery(self, link_id: str) -> None:
        """Cancel recovery observation because the link flapped again."""
        self.recovering_links.pop(link_id, None)

    def complete_recovery(self, link_id: str) -> None:
        """Stop tracking a link after switch-back is allowed or discarded."""
        self.recovering_links.pop(link_id, None)

    def is_recovering(self, link_id: str) -> bool:
        """Return whether a link is still under recovery observation."""
        return link_id in self.recovering_links

    def is_eligible_for_switchback(self, link_id: str, now: Optional[float] = None) -> bool:
        """Return True once the recovery window has elapsed without another failure."""
        if link_id not in self.recovering_links:
            return False
        check_time = time.time() if now is None else now
        return (check_time - self.recovering_links[link_id]) >= self.recovery_window_seconds

    def get_recovery_age(self, link_id: str, now: Optional[float] = None) -> Optional[float]:
        """Return how long a link has been stable since restoration."""
        if link_id not in self.recovering_links:
            return None
        check_time = time.time() if now is None else now
        return check_time - self.recovering_links[link_id]
