#!/usr/bin/env python3
"""
Topology State - Maintain topology information
"""
from pathlib import Path
import networkx as nx


class TopologyState:
    """Maintain current network topology."""

    def __init__(self, graphml_path: str = "data/Geant2012.graphml"):
        self.graphml_path = Path(graphml_path)
        self.graph = self._load_topology()

    def _load_topology(self) -> nx.Graph:
        if not self.graphml_path.exists():
            return nx.Graph()
        graph = nx.read_graphml(self.graphml_path)
        return nx.Graph(graph)

    def get_nodes(self):
        return list(self.graph.nodes())

    def get_edges(self):
        return list(self.graph.edges())
