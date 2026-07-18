#!/usr/bin/env python3
"""
Link Monitor - Monitor individual links
"""
from typing import Dict, Optional

from .models import LinkStatistics


class LinkMonitor:
    """Monitor individual link statistics."""

    def __init__(self):
        self.link_stats: Dict[str, Optional[LinkStatistics]] = {}

    def update_link_stats(self, link_stats: LinkStatistics):
        self.link_stats[link_stats.link_id] = link_stats

    def get_link_stats(self, link_id: str) -> Optional[LinkStatistics]:
        return self.link_stats.get(link_id)

    def get_all_link_stats(self):
        return self.link_stats
