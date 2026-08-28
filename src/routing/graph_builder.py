#!/usr/bin/env python3
"""
Graph Builder - Build deterministic routing graphs and candidate paths.
"""
from __future__ import annotations

from itertools import islice
import json
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import networkx as nx

from ..monitor.network_state import NetworkState
from .congestion_model import predicted_delay_ms, predicted_loss


class GraphBuilder:
    """Build weighted graphs from topology and the current network state."""

    # A negative-weight edge is a hard correctness bug, not a calibration
    # detail -- Dijkstra (used below and by PathCost/networkx) is undefined
    # for negative weights. beta/gamma's residual terms are deliberately
    # signed (see _calculate_edge_cost's docstring), so this floor is what
    # keeps that design safe regardless of how any curve is calibrated.
    MIN_EDGE_COST = 0.001

    def __init__(self, network_state: NetworkState, weights: Optional[Dict[str, float]] = None):
        self.network_state = network_state
        self.weights = weights or {
            "alpha": 0.4,
            "beta": 0.3,
            "gamma": 0.2,
            "delta": 0.05,
            "epsilon": 0.05,
            "zeta": 0.05,
            "eta": 0.05,
        }

    def build_weighted_graph(self, now: Optional[float] = None) -> nx.Graph:
        """
        Build a graph where every edge has a deterministic routing weight.

        When no live statistics are available, a low default cost keeps the
        edge usable so baselines can still be verified offline.

        now is forwarded to delta's churn lookup (see _calculate_edge_cost) --
        pass the same synthetic clock a caller is driving DecisionEngine with
        (e.g. an offline experiment's now_s) so churn is evaluated against
        that clock instead of silently defaulting to real wall-clock time.
        """
        graph = self.network_state.get_active_graph()

        for u, v in sorted(graph.edges(), key=self._canonical_edge):
            link_id = self._get_link_id(u, v)
            stats = self.network_state.get_link_stats(link_id)
            graph[u][v]["weight"] = self._calculate_edge_cost(stats, link_id, now=now)
            graph[u][v]["link_id"] = link_id

        return graph

    def get_candidate_paths(
        self, src: str, dst: str, max_paths: int = 3, now: Optional[float] = None
    ) -> List[List[str]]:
        """
        Enumerate deterministic candidate paths for a source-destination pair.

        Paths are ordered by total weight, then hop count, then lexicographic
        path order so repeated calls on the same topology always return the same
        candidates.
        """
        graph = self.build_weighted_graph(now=now)
        if src not in graph or dst not in graph:
            return []

        try:
            path_generator = nx.shortest_simple_paths(graph, src, dst, weight="weight")
            raw_paths = list(islice(path_generator, max(max_paths * 4, max_paths)))
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            return []

        ranked_paths = sorted(
            raw_paths,
            key=lambda path: (
                round(self.get_path_cost(path, graph), 8),
                len(path),
                tuple(str(node) for node in path),
            ),
        )
        return [list(path) for path in ranked_paths[:max_paths]]

    def enumerate_candidate_paths(
        self,
        pairs: Iterable[Tuple[str, str]],
        max_paths: int = 3,
        now: Optional[float] = None,
    ) -> Dict[str, Dict[str, object]]:
        """Return candidate path metadata for all requested pairs."""
        graph = self.build_weighted_graph(now=now)
        result: Dict[str, Dict[str, object]] = {}
        for src, dst in pairs:
            paths = self.get_candidate_paths(src, dst, max_paths=max_paths, now=now)
            result[f"{src}->{dst}"] = {
                "source": src,
                "destination": dst,
                "path_count": len(paths),
                "paths": [
                    {
                        "nodes": path,
                        "hop_count": len(path) - 1,
                        "total_cost": round(self.get_path_cost(path, graph), 6),
                    }
                    for path in paths
                ],
            }
        return result

    def save_candidate_paths(
        self,
        pairs: Iterable[Tuple[str, str]],
        output_path: Path,
        max_paths: int = 3,
    ) -> Dict[str, Dict[str, object]]:
        """Persist candidate paths to JSON."""
        data = self.enumerate_candidate_paths(pairs, max_paths=max_paths)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2)
        return data

    def get_path_cost(
        self, path: Sequence[str], graph: Optional[nx.Graph] = None, now: Optional[float] = None
    ) -> float:
        """Calculate the total weight for a path on the supplied graph."""
        if len(path) < 2:
            return 0.0

        working_graph = graph or self.build_weighted_graph(now=now)
        total = 0.0
        for u, v in zip(path, path[1:]):
            total += float(working_graph[u][v].get("weight", 1.0))
        return total

    def select_test_pairs(self, limit: int = 4, min_candidate_paths: int = 2) -> List[Tuple[str, str]]:
        """
        Pick representative source-destination pairs with multiple valid
        paths, deterministically, proving the graph builder can surface
        alternate paths where they exist.
        """
        graph = self.build_weighted_graph()
        nodes = sorted(str(node) for node in graph.nodes())
        selected: List[Tuple[str, str]] = []

        for index, src in enumerate(nodes):
            for dst in nodes[index + 1 :]:
                paths = self.get_candidate_paths(src, dst, max_paths=min_candidate_paths)
                if len(paths) >= min_candidate_paths:
                    selected.append((src, dst))
                if len(selected) >= limit:
                    return selected
        return selected

    def _calculate_edge_cost(self, link_stats, link_id: str, now: Optional[float] = None) -> float:
        """
        Combine one edge's real-measured signals into a single routing weight.

        Cost = alpha*utilization + beta*delay_residual + gamma*loss_residual
             + delta*churn + epsilon*reliability + zeta*delay_jitter
             + eta*loss_jitter, floored at MIN_EDGE_COST.
        """
        if link_stats is None:
            return 1.0

        utilization = float(link_stats.utilization)

        # beta/gamma price residuals (measured minus utilization-predicted),
        # not raw delay/loss: delay and loss are largely symptoms of
        # utilization, so pricing their raw values alongside alpha double-
        # counts the same congestion signal. Signed, not clamped at zero --
        # a link doing better than utilization predicts is rewarded, not
        # just never penalized.
        delay_residual_ms = (
            float(link_stats.delay_ms) - predicted_delay_ms(utilization)
            if link_stats.delay_ms is not None
            else 0.0
        )
        loss_residual = (
            float(link_stats.packet_loss) - predicted_loss(utilization)
            if link_stats.packet_loss is not None
            else 0.0
        )
        # delta: how often this link has recently entered/left an installed
        # path (NetworkState.get_link_churn_score(), updated by
        # DecisionEngine._execute_reroute() on every real reroute) -- a
        # link-level instability signal, complementary to the timing gates
        # that control *when* a reroute is allowed.
        instability = self.network_state.get_link_churn_score(link_id, now=now)
        reliability_penalty = 0.0 if link_stats.status == "up" else 1.0
        # zeta/eta: rolling-window volatility of this link's own delay/loss
        # residuals. Prices heteroscedasticity (variance depends on
        # utilization, not just the mean) directly, since beta/gamma's
        # mean-residual pricing cannot remove that dependence no matter how
        # congestion_model's curve is refit.
        jitter = self.network_state.get_delay_jitter_score(link_id, now=now)
        loss_jitter = self.network_state.get_loss_jitter_score(link_id, now=now)

        raw_cost = (
            self.weights["alpha"] * utilization
            + self.weights["beta"] * (delay_residual_ms / 1000.0)
            + self.weights["gamma"] * loss_residual
            + self.weights["delta"] * instability
            + self.weights["epsilon"] * reliability_penalty
            + self.weights.get("zeta", 0.0) * jitter
            + self.weights.get("eta", 0.0) * loss_jitter
            + 0.001
        )
        # Residuals can be negative, so the raw sum can go negative too --
        # floor it: networkx's Dijkstra requires non-negative edge weights,
        # and this floor must hold regardless of how well the residual
        # curves are calibrated.
        return max(raw_cost, self.MIN_EDGE_COST)

    def _get_link_id(self, u: str, v: str) -> str:
        nodes = sorted([str(u), str(v)])
        return f"{nodes[0]}-{nodes[1]}"

    @staticmethod
    def _canonical_edge(edge: Tuple[str, str]) -> Tuple[str, str]:
        """Sort edges by canonical endpoint order for deterministic traversal."""
        return tuple(sorted(str(node) for node in edge))
