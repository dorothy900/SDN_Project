#!/usr/bin/env python3
"""
Static Shortest Path - Baseline Algorithm 1
"""
import networkx as nx


class StaticShortestPath:
    """Compute deterministic hop-count shortest paths with fixed tie-breaking."""

    def __init__(self, graph: nx.Graph):
        self.graph = graph

    def compute_path(self, src, dst):
        """
        Return the same shortest path for identical topologies every time.

        If several equal-length shortest paths exist, the lexicographically
        smallest path is selected so the Week 3 Day 2 test is repeatable.
        """
        try:
            candidate_paths = list(nx.all_shortest_paths(self.graph, src, dst))
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            return None

        ranked_paths = sorted(
            candidate_paths,
            key=lambda path: (len(path), tuple(str(node) for node in path)),
        )
        return list(ranked_paths[0]) if ranked_paths else None
