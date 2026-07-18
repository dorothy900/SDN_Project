#!/usr/bin/env python3
"""
Static Shortest Path - Baseline Algorithm 1
"""
import networkx as nx


class StaticShortestPath:
    """Compute static hop-count shortest paths."""

    def __init__(self, graph: nx.Graph):
        self.graph = graph

    def compute_path(self, src, dst):
        try:
            return nx.shortest_path(self.graph, src, dst)
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            return None
