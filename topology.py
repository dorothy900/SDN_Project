#!/usr/bin/env python3
"""
Topology - Geant2012 Mininet Topology Definition
"""

from pathlib import Path
from typing import Dict

import networkx as nx
from mininet.topo import Topo


class GeantTopology(Topo):
    """Builds Mininet topology from Geant2012 GraphML file."""

    def __init__(self, graphml_path: str = "data/Geant2012.graphml",
                 link_bw_mbps: int = 100, link_delay: str = "10ms"):
        self.graphml_path = Path(graphml_path)
        self.link_bw_mbps = link_bw_mbps
        self.link_delay = link_delay
        self.node_mapping: Dict[str, tuple] = {}  # graph_id -> (switch, host)
        super().__init__()

    def build(self):
        """Construct topology."""
        if not self.graphml_path.exists():
            raise FileNotFoundError(f"Topology file not found: {self.graphml_path.resolve()}")

        original_graph = nx.read_graphml(self.graphml_path)
        graph = nx.Graph(original_graph)
        graph.remove_edges_from(nx.selfloop_edges(graph))

        sorted_nodes = sorted(graph.nodes(), key=str)

        for i, node in enumerate(sorted_nodes, start=1):
            switch_name = f"s{i}"
            host_name = f"h{i}"
            self.addSwitch(switch_name, protocols="OpenFlow13")
            self.addHost(host_name, ip=f"10.0.0.{i}/24")
            self.addLink(host_name, switch_name, bw=self.link_bw_mbps, delay="1ms")
            self.node_mapping[str(node)] = (switch_name, host_name)

        for u, v in graph.edges():
            su, _ = self.node_mapping[str(u)]
            sv, _ = self.node_mapping[str(v)]
            self.addLink(su, sv, bw=self.link_bw_mbps, delay=self.link_delay)

    def get_switch_names(self):
        return [s for s, _ in self.node_mapping.values()]

    def get_host_names(self):
        return [h for _, h in self.node_mapping.values()]
