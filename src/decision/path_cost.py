#!/usr/bin/env python3
"""
Path Cost Calculator - Compute composite path cost
Calculate path cost using our weighted formula
"""

from dataclasses import replace
from typing import Dict, FrozenSet, List, Optional

import networkx as nx

from ..monitor.link_capacity import resolve_link_capacity_mbps
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
            'epsilon': 0.05,
            'zeta': 0.05,
            'eta': 0.05,
        }
        self.graph_builder = GraphBuilder(network_state, weights)

    def calculate_path_cost(
        self,
        path: List[str],
        now: Optional[float] = None,
        offered_load_mbps: Optional[float] = None,
        exclude_edges: Optional[FrozenSet[str]] = None,
    ) -> float:
        """
        Calculate total cost of a path.

        now is forwarded to GraphBuilder's delta/churn lookup -- pass the
        same synthetic clock a caller is driving DecisionEngine with (e.g.
        an offline experiment's now_s), otherwise churn is evaluated against
        real wall-clock time even when the rest of the decision is happening
        on a synthetic one, and a synthetic-clock-recorded churn event will
        never be seen (see NetworkState.get_link_churn_score's docstring).

        offered_load_mbps: this flow's raw bandwidth demand -- self-
        influence / offered-load accounting. If given, converted to a
        utilization bump *per edge* using that edge's own real capacity
        (resolve_link_capacity_mbps) and added to the edge's measured
        utilization before pricing: models what this path would cost if a
        flow demanding this much bandwidth were actually routed across it.
        Bumping by a single capacity-agnostic fraction would misprice any
        edge whose real capacity differs from whichever link the fraction
        was computed against, so the bump is resolved per edge instead.
        Pass this for a *candidate* path that doesn't carry the flow yet;
        leave it None (default) for a path that already reflects the
        flow's real current state.

        exclude_edges: edges to leave unbumped even when offered_load_mbps
        is given -- for edges the candidate already shares with the
        *current* path, whose measured stats already include this flow's
        real contribution there; bumping them again would double-count it.
        See compare_paths()/is_improvement(), which apply both
        asymmetrically (new_path only) for this reason -- this method
        itself is symmetric and just does what it's told for whichever
        path it's given.
        """
        if not path or len(path) < 2:
            return float('inf')

        if offered_load_mbps is None:
            graph = self.graph_builder.build_weighted_graph(now=now)
            total_cost = 0.0
            for i in range(len(path) - 1):
                u, v = path[i], path[i + 1]
                if not graph.has_edge(u, v):
                    return float('inf')
                total_cost += graph[u][v]['weight']
            return total_cost

        # offered_load_mbps given: every non-excluded edge on this path
        # needs its utilization bumped before pricing, so the precomputed
        # whole-graph weights (built from unmodified utilization) can't be
        # reused -- recompute each edge's cost directly against a loaded
        # copy of its real stats instead.
        exclude_edges = exclude_edges or frozenset()
        active_graph = self.network_state.get_active_graph()
        total_cost = 0.0
        for i in range(len(path) - 1):
            u, v = path[i], path[i + 1]
            if not active_graph.has_edge(u, v):
                return float('inf')
            link_id = self._get_link_id(u, v)
            stats = self.network_state.get_link_stats(link_id)
            if stats is None:
                total_cost += 1.0  # matches GraphBuilder._calculate_edge_cost's own no-data default
                continue
            if link_id in exclude_edges:
                loaded_stats = stats
            else:
                bump = offered_load_mbps / resolve_link_capacity_mbps(link_id)
                loaded_stats = replace(
                    stats, utilization=min(float(stats.utilization) + bump, 1.0)
                )
            total_cost += self.graph_builder._calculate_edge_cost(loaded_stats, link_id, now=now)
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

    def find_best_path(self, src: str, dst: str, now: Optional[float] = None) -> Optional[List[str]]:
        """Find lowest cost path between two nodes."""
        try:
            graph = self.graph_builder.build_weighted_graph(now=now)
            if not (graph.has_node(src) and graph.has_node(dst)):
                return None
            return nx.shortest_path(graph, source=src, target=dst, weight='weight')
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            return None

    def is_improvement(self, old_path: List[str], new_path: List[str],
                      min_abs_reduction: float = 0.1,
                      min_rel_reduction: float = 0.15,
                      now: Optional[float] = None,
                      offered_load_mbps: Optional[float] = None) -> bool:
        """
        Check if new path is a significant improvement.

        offered_load_mbps is applied to new_path only, not old_path -- see
        calculate_path_cost's docstring for why (old_path already reflects
        this flow's real current state; new_path doesn't yet) -- and is
        never applied to edges new_path shares with old_path (same
        docstring, exclude_edges), since old_path's measured stats on
        those already include this flow's real contribution.
        """
        old_cost = self.calculate_path_cost(old_path, now=now)
        shared_edges = self._path_edges(old_path) & self._path_edges(new_path)
        new_cost = self.calculate_path_cost(
            new_path, now=now, offered_load_mbps=offered_load_mbps, exclude_edges=shared_edges
        )

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
        now: Optional[float] = None,
        offered_load_mbps: Optional[float] = None,
    ) -> Dict[str, float]:
        """
        Return comparable cost and gain metrics for two candidate paths.

        offered_load_mbps is applied to new_path only, and never to edges
        shared with old_path -- see calculate_path_cost's docstring.
        """
        old_cost = self.calculate_path_cost(old_path, now=now)
        shared_edges = self._path_edges(old_path) & self._path_edges(new_path)
        new_cost = self.calculate_path_cost(
            new_path, now=now, offered_load_mbps=offered_load_mbps, exclude_edges=shared_edges
        )
        abs_improvement = old_cost - new_cost
        rel_improvement = abs_improvement / old_cost if old_cost > 0 else 0.0
        accepted = self.is_improvement(
            old_path,
            new_path,
            min_abs_reduction=min_abs_reduction,
            min_rel_reduction=min_rel_reduction,
            now=now,
            offered_load_mbps=offered_load_mbps,
        )
        return {
            "old_cost": round(old_cost, 6),
            "new_cost": round(new_cost, 6),
            "absolute_improvement": round(abs_improvement, 6),
            "relative_improvement": round(rel_improvement, 6),
            "accepted": accepted,
        }

    def _path_edges(self, path: List[str]) -> FrozenSet[str]:
        return frozenset(self._get_link_id(u, v) for u, v in zip(path, path[1:]))

    def _get_link_id(self, u: str, v: str) -> str:
        nodes = sorted([str(u), str(v)])
        return f"{nodes[0]}-{nodes[1]}"
