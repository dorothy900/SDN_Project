#!/usr/bin/env python3
"""
Topology - Geant2012 Mininet Topology Definition
"""

import sys
from pathlib import Path
from typing import Dict

import networkx as nx
from mininet.topo import Topo

PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.monitor.link_capacity import build_node_mapping, resolve_link_bw_mbps


class GeantTopology(Topo):
    """Builds Mininet topology from Geant2012 GraphML file."""

    def __init__(self, graphml_path: str = "data/Geant2012.graphml",
                 link_bw_mbps: int = 100, link_delay: str = "10ms"):
        self.graphml_path = Path(graphml_path)
        self.link_bw_mbps = link_bw_mbps
        self.link_delay = link_delay
        self.node_mapping: Dict[str, tuple] = {}  # graph_id -> (switch, host)
        self.link_bandwidths: Dict[tuple, int] = {}  # frozenset({switch_a, switch_b}) -> real configured Mbit
        super().__init__()

    def build(self):
        """Construct topology."""
        if not self.graphml_path.exists():
            raise FileNotFoundError(f"Topology file not found: {self.graphml_path.resolve()}")

        original_graph = nx.read_graphml(self.graphml_path)
        graph = nx.Graph(original_graph)
        graph.remove_edges_from(nx.selfloop_edges(graph))

        # link_capacity.build_node_mapping() is the single source of truth for this
        # exact sorted-node-order / "s{i}"/"h{i}" naming -- a RYU app translating a
        # live datapath.id back to a GEANT node id must compute the identical mapping
        # without importing mininet at all, so both share this one function instead of
        # separately re-deriving it (see build_node_mapping's own docstring for why
        # that drifting apart is a real, previously-hit bug, not a hypothetical one).
        # Only valid for the default graphml_path -- build_node_mapping() always reads
        # data/Geant2012.graphml internally, so a caller overriding graphml_path here
        # falls back to deriving the mapping locally instead.
        if str(self.graphml_path) == "data/Geant2012.graphml":
            self.node_mapping = dict(build_node_mapping())
        else:
            self.node_mapping = {
                str(node): (f"s{i}", f"h{i}")
                for i, node in enumerate(sorted(graph.nodes(), key=str), start=1)
            }

        for node, (switch_name, host_name) in self.node_mapping.items():
            i = switch_name[1:]
            self.addSwitch(switch_name, protocols="OpenFlow13")
            self.addHost(host_name, ip=f"10.0.0.{i}/24")
            self.addLink(host_name, switch_name, bw=self.link_bw_mbps, delay="1ms")

        for u, v in graph.edges():
            su, _ = self.node_mapping[str(u)]
            sv, _ = self.node_mapping[str(v)]
            edge_data = graph.get_edge_data(u, v) or {}
            link_bw = resolve_link_bw_mbps(edge_data.get("LinkLabel"), self.link_bw_mbps)
            self.addLink(su, sv, bw=link_bw, delay=self.link_delay)
            self.link_bandwidths[frozenset((su, sv))] = link_bw

    def get_switch_names(self):
        return [s for s, _ in self.node_mapping.values()]

    def get_host_names(self):
        return [h for _, h in self.node_mapping.values()]

    def get_link_bw_mbps(self, switch_a: str, switch_b: str) -> int:
        """
        Real configured Mbit/s for a switch-switch link, as actually applied
        by build() (see src/monitor/link_capacity.py) -- callers measuring
        utilization against this link must divide by this, not assume a flat
        constant, since real per-link bandwidth now varies (2026-08-12).
        """
        return self.link_bandwidths[frozenset((switch_a, switch_b))]


# Registers this topology with Mininet's --custom loader, e.g.:
#   sudo mn --custom topology.py --topo geant --controller none --test pingall
topos = {"geant": GeantTopology}
