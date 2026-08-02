#!/usr/bin/env python3
"""
Dynamic Baseline - Baseline Algorithm 2
Reacts immediately to any changes, no stability mechanisms
"""
from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import networkx as nx


class DynamicBaseline:
    """Dynamic routing that reroutes immediately once a path is overloaded."""

    def __init__(self, graph_builder, threshold: float = 0.7):
        self.graph_builder = graph_builder
        self.threshold = threshold
        self.events: List[Dict[str, object]] = []

    def compute_path(self, src, dst):
        graph = self.graph_builder.build_weighted_graph()
        try:
            return nx.shortest_path(graph, src, dst, weight="weight")
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            return None

    def should_reroute(self, old_path, new_path) -> bool:
        # Dynamic baseline reroutes whenever path changes.
        return old_path != new_path

    def get_path_utilization(self, path: List[str]) -> float:
        """Return the highest utilization currently observed on a path."""
        if not path or len(path) < 2:
            return 0.0

        max_utilization = 0.0
        for u, v in zip(path, path[1:]):
            link_id = self.graph_builder._get_link_id(u, v)
            link_stats = self.graph_builder.network_state.get_link_stats(link_id)
            if link_stats is not None:
                max_utilization = max(max_utilization, float(link_stats.utilization))
        return max_utilization

    def path_is_viable(self, path: List[str]) -> bool:
        """Return False if any link on the path is currently marked down."""
        if not path or len(path) < 2:
            return True
        for u, v in zip(path, path[1:]):
            link_id = self.graph_builder._get_link_id(u, v)
            link_stats = self.graph_builder.network_state.get_link_stats(link_id)
            if link_stats is not None and link_stats.status != "up":
                return False
        return True

    def evaluate_reroute(
        self,
        src: str,
        dst: str,
        current_path: Optional[List[str]],
        timestamp: Optional[datetime] = None,
        topology_changed: bool = False,
    ) -> Dict[str, object]:
        """
        Evaluate whether the baseline should reroute immediately.

        The dynamic baseline has no persistence or hold-down logic. It
        reconsiders its path whenever the current path is above the configured
        threshold, no longer viable (a link on it failed), or the controller
        just observed a link up/down (port-status) event on the topology,
        which a reactive controller would notice immediately without waiting
        on the next utilization poll.
        """
        ts = timestamp or datetime.now()
        proposed_path = self.compute_path(src, dst)
        current_utilization = self.get_path_utilization(current_path or [])
        current_viable = self.path_is_viable(current_path or [])
        should_consider_reroute = (
            current_path is None
            or current_utilization > self.threshold
            or not current_viable
            or topology_changed
        )
        did_reroute = should_consider_reroute and self.should_reroute(current_path, proposed_path)

        if not current_viable:
            reason = "link_failure"
        elif current_utilization > self.threshold:
            reason = "threshold_exceeded"
        else:
            reason = "below_threshold"

        event = {
            "timestamp": ts.isoformat(),
            "source": src,
            "destination": dst,
            "threshold": self.threshold,
            "current_path": current_path or [],
            "proposed_path": proposed_path or [],
            "current_path_utilization": round(current_utilization, 6),
            "decision": "reroute" if did_reroute else "keep",
            "reason": reason,
        }
        self.events.append(event)
        return event

    def save_events(self, output_path: Path) -> None:
        """Persist reroute decisions for the Week 3 Day 4 deliverable."""
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=[
                    "timestamp",
                    "source",
                    "destination",
                    "threshold",
                    "current_path",
                    "proposed_path",
                    "current_path_utilization",
                    "decision",
                    "reason",
                ],
            )
            writer.writeheader()
            writer.writerows(self.events)
