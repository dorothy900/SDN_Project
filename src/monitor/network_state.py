#!/usr/bin/env python3
"""
Network State - Central network state manager
Combine topology, links, and port stats
"""
from .link_monitor import LinkMonitor
from .topology_state import TopologyState


class NetworkState:
    """Central manager for network state."""

    def __init__(self):
        self.topology = TopologyState()
        self.link_monitor = LinkMonitor()

    def get_link_stats(self, link_id: str):
        return self.link_monitor.get_link_stats(link_id)

    def get_all_link_stats(self):
        return self.link_monitor.get_all_link_stats()
