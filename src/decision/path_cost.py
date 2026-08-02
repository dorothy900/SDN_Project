#!/usr/bin/env python3
"""
Path Cost Calculator - Compute composite path cost
Calculate path cost using our weighted formula
"""

from typing import Dict, List, Optional

import networkx as nx

from ..monitor.network_state import NetworkState
from ..routing.graph_builder import GraphBuilder


class PathCost:
    """Calculate and compare path costs."""

    def __init__(self, network_state: NetworkState, weights: Optional[Dict] = None):
        self.network_state = network_state
        self.weights = weights or {
            'alpha': 0.4,
            'beta': 0.3,
            'gamma': 0.2,
            'delta': 0.05,
            'epsilon': 0.05
        }
        self.graph_builder = GraphBuilder(network_state, weights)

    def calculate_path_cost(self, path: List[str]) -> float:
        """Calculate total cost of a path."""
        if not path or len(path) < 2:
            return float('inf')

        graph = self.graph_builder.build_weighted_graph()
        total_cost = 0.0

        for i in range(len(path) - 1):
            u, v = path[i], path[i + 1]
            if not graph.has_edge(u, v):
                return float('inf')
            total_cost += graph[u][v]['weight']

        return total_cost

    def calculate_path_metrics(self, path: List[str]) -> Dict[str, float]:
        """Break down path into individual metrics."""
        if not path or len(path) < 2:
            return {}

        metrics = {
            'total_utilization': 0.0,
            'total_delay_ms': 0.0,
            'total_loss': 0.0,
            'max_utilization': 0.0,
            'max_delay_ms': 0.0,
            'hop_count': len(path) - 1
        }

        for i in range(len(path) - 1):
            u, v = path[i], path[i + 1]
            link_id = self._get_link_id(u, v)
            stats = self.network_state.get_link_stats(link_id)

            if stats:
                metrics['total_utilization'] += stats.utilization
                metrics['max_utilization'] = max(metrics['max_utilization'], stats.utilization)

                if stats.delay_ms:
                    metrics['total_delay_ms'] += stats.delay_ms
                    metrics['max_delay_ms'] = max(metrics['max_delay_ms'], stats.delay_ms)

                if stats.packet_loss:
                    metrics['total_loss'] += stats.packet_loss

        return metrics

    def find_best_path(self, src: str, dst: str) -> Optional[List[str]]:
        """Find lowest cost path between two nodes."""
        try:
            graph = self.graph_builder.build_weighted_graph()
            if not (graph.has_node(src) and graph.has_node(dst)):
                return None
            return nx.shortest_path(graph, source=src, target=dst, weight='weight')
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            return None

    def is_improvement(self, old_path: List[str], new_path: List[str],
                      min_abs_reduction: float = 0.1,
                      min_rel_reduction: float = 0.15) -> bool:
        """Check if new path is a significant improvement."""
        old_cost = self.calculate_path_cost(old_path)
        new_cost = self.calculate_path_cost(new_path)

        if new_cost >= old_cost:
            return False

        abs_improvement = old_cost - new_cost
        rel_improvement = abs_improvement / old_cost if old_cost > 0 else 0

        return (abs_improvement >= min_abs_reduction or
                rel_improvement >= min_rel_reduction)

    def compare_paths(
        self,
        old_path: List[str],
        new_path: List[str],
        min_abs_reduction: float = 0.1,
        min_rel_reduction: float = 0.15,
    ) -> Dict[str, float]:
        """Return comparable cost and gain metrics for two candidate paths."""
        old_cost = self.calculate_path_cost(old_path)
        new_cost = self.calculate_path_cost(new_path)
        abs_improvement = old_cost - new_cost
        rel_improvement = abs_improvement / old_cost if old_cost > 0 else 0.0
        accepted = self.is_improvement(
            old_path,
            new_path,
            min_abs_reduction=min_abs_reduction,
            min_rel_reduction=min_rel_reduction,
        )
        return {
            "old_cost": round(old_cost, 6),
            "new_cost": round(new_cost, 6),
            "absolute_improvement": round(abs_improvement, 6),
            "relative_improvement": round(rel_improvement, 6),
            "accepted": accepted,
        }

    def _get_link_id(self, u: str, v: str) -> str:
        nodes = sorted([str(u), str(v)])
        return f"{nodes[0]}-{nodes[1]}"
