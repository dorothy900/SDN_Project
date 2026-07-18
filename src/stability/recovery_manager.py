#!/usr/bin/env python3
"""
Recovery Manager - Manage network recovery after failure
"""
class RecoveryManager:
    """Manage recovery process."""

    def __init__(self):
        self.recovering_links = set()

    def start_recovery(self, link_id: str):
        self.recovering_links.add(link_id)

    def complete_recovery(self, link_id: str):
        if link_id in self.recovering_links:
            self.recovering_links.remove(link_id)
