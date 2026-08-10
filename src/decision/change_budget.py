#!/usr/bin/env python3
"""
Change Budget - Limit rate of routing changes
Prevent excessive OpenFlow updates and controller load
"""

import time
from collections import deque
from typing import Deque


class ChangeBudget:
    """Track and limit rate of routing changes."""

    def __init__(self, max_updates_per_minute: int = 10,
                 max_path_changes_per_minute: int = 5,
                 burst_allowance: int = 3):
        if max_updates_per_minute < 0:
            raise ValueError("max_updates_per_minute must be >= 0, got %r" % max_updates_per_minute)
        if max_path_changes_per_minute < 0:
            raise ValueError("max_path_changes_per_minute must be >= 0, got %r" % max_path_changes_per_minute)
        if burst_allowance < 0:
            raise ValueError("burst_allowance must be >= 0, got %r" % burst_allowance)
        if max_path_changes_per_minute > max_updates_per_minute:
            raise ValueError(
                "max_path_changes_per_minute (%r) cannot exceed max_updates_per_minute (%r) -- "
                "a path change is itself an update"
                % (max_path_changes_per_minute, max_updates_per_minute)
            )
        self.max_updates_per_minute = max_updates_per_minute
        self.max_path_changes_per_minute = max_path_changes_per_minute
        self.burst_allowance = burst_allowance

        self.update_times: Deque[float] = deque()
        self.path_change_times: Deque[float] = deque()

    def _clean_old_entries(self, deque_obj: Deque):
        """Remove entries older than 1 minute."""
        now = time.time()
        cutoff = now - 60

        while deque_obj and deque_obj[0] < cutoff:
            deque_obj.popleft()

    def can_update(self) -> bool:
        """Check if a flow update is within budget."""
        self._clean_old_entries(self.update_times)
        return len(self.update_times) < self.max_updates_per_minute

    def can_change_path(self) -> bool:
        """Check if a path change is within budget."""
        self._clean_old_entries(self.path_change_times)

        current = len(self.path_change_times)
        allowed = self.max_path_changes_per_minute

        if current < allowed:
            return True

        if current < allowed + self.burst_allowance:
            # Allow burst if we haven't used it recently
            if len(self.path_change_times) >= 2:
                time_since_last = time.time() - self.path_change_times[-1]
                return time_since_last > 5  # Don't burst too quickly

        return False

    def record_update(self):
        """Record that an update occurred."""
        self.update_times.append(time.time())
        self._clean_old_entries(self.update_times)

    def record_path_change(self):
        """Record that a path change occurred."""
        self.path_change_times.append(time.time())
        self.record_update()
        self._clean_old_entries(self.path_change_times)

    def get_update_count(self) -> int:
        """Get number of updates in last minute."""
        self._clean_old_entries(self.update_times)
        return len(self.update_times)

    def get_path_change_count(self) -> int:
        """Get number of path changes in last minute."""
        self._clean_old_entries(self.path_change_times)
        return len(self.path_change_times)

    def get_available_updates(self) -> int:
        """Get remaining update budget."""
        return max(0, self.max_updates_per_minute - self.get_update_count())

    def reset(self):
        """Reset all budget tracking."""
        self.update_times.clear()
        self.path_change_times.clear()

    def get_budget_state(self) -> dict:
        """Return a serializable snapshot of the current rolling budget state."""
        return {
            "update_count": self.get_update_count(),
            "path_change_count": self.get_path_change_count(),
            "available_updates": self.get_available_updates(),
            "can_change_path": self.can_change_path(),
        }
