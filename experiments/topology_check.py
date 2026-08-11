#!/usr/bin/env python3
"""
Run Topology Validation - Week 1 Day 1-2 automation.

Loads the real GEANT topology from GraphML and computes real structural
metrics (node/link counts, connectivity, diameter, average degree). Nothing
here is a fixed string -- every number in the output file is derived from the
actual graph file on disk.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

import networkx as nx

from src.monitor.network_state import NetworkState
from src.routing.graph_builder import GraphBuilder


class TopologyValidation:
    """Run Week 1 Day 1-2 checks and persist Stage 1 deliverables."""

    def __init__(self, output_dir: Optional[Path] = None, graphml_path: str = "data/Geant2012.graphml"):
        self.output_dir = output_dir or Path("results/topology")
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.graphml_path = Path(graphml_path)

    def run(self) -> Dict[str, object]:
        if not self.graphml_path.exists():
            raise FileNotFoundError("Topology file not found: %s" % self.graphml_path.resolve())

        raw_graph = nx.read_graphml(self.graphml_path)
        graph = nx.Graph(raw_graph)
        graph.remove_edges_from(nx.selfloop_edges(graph))

        node_count = graph.number_of_nodes()
        edge_count = graph.number_of_edges()
        connected = nx.is_connected(graph)
        diameter = nx.diameter(graph) if connected else None
        avg_degree = sum(dict(graph.degree()).values()) / node_count if node_count else 0.0

        candidate_path_summary = self._check_candidate_paths()

        summary = {
            "node_count": node_count,
            "edge_count": edge_count,
            "connected": connected,
            "diameter": diameter,
            "avg_degree": round(avg_degree, 4),
            "candidate_path_summary": candidate_path_summary,
        }
        self._write_report(summary, graph)
        return summary

    def _check_candidate_paths(self, sample_pairs: int = 3) -> Dict[str, object]:
        """Spot-check that representative pairs have at least two candidate paths."""
        state = NetworkState(output_dir=self.output_dir)
        builder = GraphBuilder(state)
        pairs = builder.select_test_pairs(limit=sample_pairs, min_candidate_paths=2)
        results = []
        for src, dst in pairs:
            paths = builder.get_candidate_paths(src, dst, max_paths=3)
            results.append({"pair": "%s->%s" % (src, dst), "candidate_path_count": len(paths)})
        return {"pairs_checked": len(pairs), "results": results}

    def _write_report(self, summary: Dict[str, object], graph: nx.Graph) -> None:
        output_file = self.output_dir / "topology_validation.txt"
        lines = [
            "Topology Verification - %s" % datetime.now().isoformat(),
            "=" * 60,
            "Source: %s" % self.graphml_path,
            "Nodes: %d" % summary["node_count"],
            "Links: %d" % summary["edge_count"],
            "Connected: %s" % summary["connected"],
            "Diameter: %s" % summary["diameter"],
            "Average degree: %.2f" % summary["avg_degree"],
            "",
            "Candidate-path spot check (>=2 required):",
        ]
        for row in summary["candidate_path_summary"]["results"]:
            lines.append("  %s -> %d candidate paths" % (row["pair"], row["candidate_path_count"]))
        lines.append("")
        lines.append("Nodes (sorted):")
        for node in sorted(graph.nodes(), key=str):
            lines.append("  %s" % node)
        output_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

        (self.output_dir / "topology_summary.json").write_text(
            json.dumps(summary, indent=2, default=str), encoding="utf-8"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Week 1 topology validation runner")
    parser.add_argument("--output-dir", type=str, default="results/topology")
    parser.add_argument("--graphml-path", type=str, default="data/Geant2012.graphml")
    args = parser.parse_args()

    runner = TopologyValidation(output_dir=Path(args.output_dir), graphml_path=args.graphml_path)
    summary = runner.run()
    print(json.dumps(summary, indent=2, default=str))


if __name__ == "__main__":
    main()
