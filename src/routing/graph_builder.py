#!/usr/bin/env python3
"""
Graph Builder - Build weighted graph for routing
"""
import networkx as nx

from ..monitor.network_state import NetworkState


class GraphBuilder:
    """Build weighted graph from network state."""

    def __init__(self, network_state: NetworkState, weights: dict = None):
        self.network_state = network_state
        self.weights = weights or {
            'alpha': 0.4, 'beta': 0.3, 'gamma': 0.2, 'delta': 0.05, 'epsilon': 0.05
        }

    def build_weighted_graph(self):
        graph = self.network_state.topology.graph.copy()

        for u, v in graph.edges():
            link_id = self._get_link_id(u, v)
            stats = self.network_state.get_link_stats(link_id)

            if stats:
                cost = self._calculate_edge_cost(stats)
            else:
                cost = 1.0

            graph[u][v]['weight'] = cost

        return graph

    def _calculate_edge_cost(self, link_stats) -> float:
        util = link_stats.utilization
        delay = link_stats.delay_ms / 1000 if link_stats.delay_ms else 0
        loss = link_stats.loss_rate if link_stats.loss_rate else 0

        return (self.weights['alpha'] * util +
                self.weights['beta'] * delay +
                self.weights['gamma'] * loss +
                self.weights['delta'] * 0 +  # Priority
                self.weights['epsilon'] * 1)  # Reliability

    def _get_link_id(self, u, v) -> str:
        nodes = sorted([str(u), str(v)])
        return f"{nodes[0]}-{nodes[1]}"
