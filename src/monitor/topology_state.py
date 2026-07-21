#!/usr/bin/env python3
"""
Topology State - Maintain topology information
Week 2 Day 4: Detect link up/down and update internal graph
"""
from pathlib import Path
from typing import Dict, Set, Optional, Tuple

import networkx as nx


class TopologyState:
    """
    Maintain current network topology state.
    Tracks which links are active and updates graph structure dynamically.
    """

    def __init__(self, graphml_path: str = "data/Geant2012.graphml"):
        self.graphml_path = Path(graphml_path)
        self.base_graph = self._load_topology()
        self.active_graph = self.base_graph.copy()
        self.failed_links: Set[Tuple] = set()  # (u, v) tuples
    
    def _load_topology(self) -> nx.Graph:
        """Load base topology from GraphML file."""
        if not self.graphml_path.exists():
            return nx.Graph()
        graph = nx.read_graphml(self.graphml_path)
        return nx.Graph(graph)
    
    def mark_link_failed(self, u: str, v: str) -> None:
        """
        Mark a link as failed, remove from active graph.
        
        Args:
            u: First node ID
            v: Second node ID
        """
        # Store in canonical order
        link = tuple(sorted([u, v]))
        
        if link not in self.failed_links:
            self.failed_links.add(link)
            if self.active_graph.has_edge(u, v):
                self.active_graph.remove_edge(u, v)
    
    def mark_link_recovered(self, u: str, v: str) -> None:
        """
        Mark a link as recovered, restore to active graph.
        
        Args:
            u: First node ID
            v: Second node ID
        """
        link = tuple(sorted([u, v]))
        
        if link in self.failed_links:
            self.failed_links.remove(link)
            if self.base_graph.has_edge(u, v):
                self.active_graph.add_edge(u, v)
    
    def set_link_status(self, u: str, v: str, is_up: bool) -> None:
        """
        Set a link's status (convenience method).
        
        Args:
            u: First node ID
            v: Second node ID
            is_up: True if link should be up, False if down
        """
        if is_up:
            self.mark_link_recovered(u, v)
        else:
            self.mark_link_failed(u, v)
    
    def is_link_active(self, u: str, v: str) -> bool:
        """Check if a link is currently active."""
        link = tuple(sorted([u, v]))
        return link not in self.failed_links
    
    def get_active_graph(self) -> nx.Graph:
        """Get the current graph with only active links."""
        return self.active_graph.copy()
    
    def get_nodes(self) -> list:
        """Get all nodes in the topology."""
        return list(self.base_graph.nodes())
    
    def get_all_links(self) -> list:
        """Get all links (including failed ones)."""
        return list(self.base_graph.edges())
    
    def get_active_links(self) -> list:
        """Get only active links."""
        return list(self.active_graph.edges())
    
    def get_failed_links(self) -> list:
        """Get list of failed links."""
        return list(self.failed_links)
