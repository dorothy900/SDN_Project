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

        When no live statistics are available, a low default cost keeps the edge
        usable so Week 3 baselines can still be verified offline.

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
        """Persist candidate paths to JSON for the Week 3 Day 1 deliverable."""
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
        Pick representative source-destination pairs with multiple valid paths.

        This keeps Week 3 verification deterministic while still proving the
        graph builder can surface alternate paths where they exist.
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
        Translate the dissertation cost function into a single edge weight.

        delta's term was "priority", hardcoded to 0.0 (a dead weight -- never
        connected to anything, since priority is inherently a per-flow
        concept and this graph is shared across all flows). Replaced
        2026-08-12 with a per-link instability/churn score instead: how
        often this link has recently been added to or removed from an
        installed path (NetworkState.get_link_churn_score(), fed by
        DecisionEngine._execute_reroute() every time a reroute actually
        happens). Unlike priority, this is a genuine link-level property, so
        it fits the one-shared-graph design without needing a separate graph
        per flow/service type.

        beta/gamma use *residuals* against congestion_model.predicted_delay_ms/
        predicted_loss (fixed 2026-08-12), not raw delay_ms/packet_loss. In a
        real network, delay and loss are largely symptoms of utilization
        (queueing/buffer overflow), so feeding their raw values into an
        additive formula alongside alpha*utilization double-counts the same
        congestion signal (quantified: at u=0.9 the true d(cost)/du was ~1.7x
        the alpha term alone -- see compliance_check.md). Subtracting each
        link's utilization-predicted delay/loss leaves only the part beta/
        gamma should actually be pricing: congestion *beyond* what this
        link's utilization already explains (a physically longer link, a
        real queueing anomaly). A link performing exactly as utilization
        predicts contributes zero extra cost from these two terms; residuals
        are signed, not clamped at zero, so a link doing *better* than its
        utilization predicts is rewarded, not just never penalized.

        Because delay_residual_ms/loss_residual can be negative, the sum
        below is floored at MIN_EDGE_COST rather than returned as-is (fixed
        2026-08-12, after a real run: a background link's raw delay fell far
        enough below congestion_model's prediction at high utilization that
        the total went negative, and networkx's Dijkstra correctly raised
        rather than silently misroute on a negative-weight graph). The floor
        is a correctness property of this being an edge *weight*, not a
        calibration fix -- it should hold regardless of how well-fit
        congestion_model's curve is, since no amount of curve-fitting can
        guarantee every real (delay, loss) sample stays within the model's
        assumptions.

        zeta*jitter (added 2026-08-19): a Breusch-Pagan test formally
        confirmed delay_residual is heteroscedastic (its variance, not just
        its mean, depends on utilization -- LM=21.231, p=0.0005, see
        compliance_check.md) -- a dependence beta's mean-residual pricing
        cannot remove by construction, no matter how the curve is refit.
        Rather than keep chasing that out of delay_residual, zeta prices it
        directly via NetworkState.get_delay_jitter_score(): a rolling-window
        std of this link's own recent delay residuals, a real, observable
        data-plane instability signal distinct from delta (which measures
        control-plane reroute activity, not measurement volatility -- a link
        can be jittery without ever having been rerouted around).
        zeta=0.05 is a starting default matching delta/epsilon's magnitude,
        not yet tuned by weight_search_comparison.py (still open, see
        pending task notes) -- weights now sum to 1.10, not 1.0 (with eta).

        eta*loss_jitter (added 2026-08-19, same day): same rationale as
        zeta but for loss_residual -- a separate Breusch-Pagan test
        confirmed it's heteroscedastic too (LM=14.965, p=0.0009 on a
        LOESS-fitted residual, see compliance_check.md). Kept as its own
        weight/tracker (NetworkState.get_loss_jitter_score(), a rolling-
        window std of this link's recent loss residuals) rather than merged
        into zeta -- delay and loss jitter are measured on different scales
        via separate diagnostics, and this project found no real evidence
        yet for how to normalize them into one combined signal.

        Not a composite: a PCA merge of utilization/delay_residual/
        loss_residual/churn into one term was tried and reverted the same
        day (see compliance_check.md's "Composite congestion indicator"
        section) -- PC1's loadings inverted the sign of both churn and
        delay_residual's contribution (more churn / worse delay --> LOWER
        cost), directly contradicting invariants this formula is built to
        guarantee (test_churned_link_costs_more_than_an_identical_untouched_link,
        test_link_with_anomalous_delay_beyond_prediction_costs_more). Kept
        as 6 separate weighted terms instead.
        """
        if link_stats is None:
            return 1.0

        utilization = float(link_stats.utilization)

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
        instability = self.network_state.get_link_churn_score(link_id, now=now)
        reliability_penalty = 0.0 if link_stats.status == "up" else 1.0
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
        return max(raw_cost, self.MIN_EDGE_COST)

    def _get_link_id(self, u: str, v: str) -> str:
        nodes = sorted([str(u), str(v)])
        return f"{nodes[0]}-{nodes[1]}"

    @staticmethod
    def _canonical_edge(edge: Tuple[str, str]) -> Tuple[str, str]:
        """Sort edges by canonical endpoint order for deterministic traversal."""
        return tuple(sorted(str(node) for node in edge))
