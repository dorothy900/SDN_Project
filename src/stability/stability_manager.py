#!/usr/bin/env python3
"""
Stability Manager - Coordinate all stability mechanisms
"""
import time
from typing import Dict


class StabilityManager:
    """Manage all stability mechanisms together."""

    def __init__(self, hold_down_seconds: float = 10.0):
        self.hold_down_seconds = hold_down_seconds
        self.hold_down_end: Dict[tuple, float] = {}

    def is_in_hold_down(self, src_dst: tuple) -> bool:
        if src_dst not in self.hold_down_end:
            return False
        return time.time() < self.hold_down_end[src_dst]

    def start_hold_down(self, src_dst: tuple):
        self.hold_down_end[src_dst] = time.time() + self.hold_down_seconds

    def clear_hold_down(self, src_dst: tuple):
        if src_dst in self.hold_down_end:
            del self.hold_down_end[src_dst]
