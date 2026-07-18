#!/usr/bin/env python3
"""
Dynamic Baseline - Baseline Algorithm 2
Reacts immediately to any changes, no stability mechanisms
"""
import networkx as nx


class DynamicBaseline:
    """Dynamic routing that reacts immediately."""

    def __init__(self, graph_builder):
        self.graph_builder = graph_builder

    def compute_path(self, src, dst):
        graph = self.graph_builder.build_weighted_graph()
        try:
            return nx.shortest_path(graph, src, dst, weight='weight')
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            return None

    def should_reroute(self, old_path, new_path) -> bool:
        # Dynamic baseline reroutes whenever path changes
        return old_path != new_path
