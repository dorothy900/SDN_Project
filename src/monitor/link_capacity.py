#!/usr/bin/env python3
"""
Link Capacity - resolve data/Geant2012.graphml's real per-link bandwidth
labels into Mininet-usable Mbit/s values.

39 of the 61 real GEANT edges carry a real `LinkLabel` attribute (e.g.
"10 Gbps"), sourced from GEANT's own published network map.

Real values (155Mbps to 10Gbps) aren't directly usable in Mininet on a
typical test VM: a single iperf UDP stream tops out well under 1Gbit/s
here, so a link configured at its literal 10Gbps would never be saturable
by any traffic this environment can generate. REAL_LINK_LABEL_TO_MBPS maps
each real tier to a scaled-down Mininet value instead, preserving the real
relative ordering (10Gbps links get more simulated bandwidth than 155Mbps
links) while keeping every value within a range real generated traffic can
actually saturate.
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, Optional, Tuple

REAL_LINK_LABEL_TO_MBPS = {
    "155 Mbps": 20,
    "1 Gbps": 50,
    "2.5 Gbps": 80,
    "10 Gbps": 150,
    "Lit Fibre": 150,  # dark fibre; source data has no explicit capacity, treated as the top tier
}


def resolve_link_bw_mbps(link_label: Optional[str], default_mbps: int) -> int:
    """
    Map a real GEANT LinkLabel to a Mininet-usable bandwidth in Mbit/s.

    Returns default_mbps unchanged for links with no real label -- 22 of the
    61 real GEANT edges have no LinkLabel in the source GraphML (missing from
    GEANT's own published map, not something this project removed).
    Deliberately does not guess a value for those rather than fabricate data
    where none exists; an unrecognized label also falls back to default_mbps.
    """
    if not link_label:
        return default_mbps
    return REAL_LINK_LABEL_TO_MBPS.get(link_label, default_mbps)


_GEANT_GRAPHML_PATH = Path(__file__).resolve().parents[2] / "data" / "Geant2012.graphml"
_geant_graph_cache: Optional["object"] = None


def _load_geant_graph():
    """Lazily load and cache GEANT's real topology graph (LinkLabel-annotated
    edges) once per process, shared by both production code (PathCost's
    per-edge offered-load correction) and offline experiments."""
    global _geant_graph_cache
    if _geant_graph_cache is None:
        import networkx as nx

        graph = nx.Graph(nx.read_graphml(_GEANT_GRAPHML_PATH))
        graph.remove_edges_from(nx.selfloop_edges(graph))
        _geant_graph_cache = graph
    return _geant_graph_cache


def build_node_mapping() -> Dict[str, Tuple[str, str]]:
    """
    graph_node_id -> (switch_name, host_name), matching Mininet's default dpid
    assignment (dpid == the switch name's own numeric suffix, e.g. "s13" -> dpid
    13) exactly. The single source of truth topology.py's real build() and any
    RYU app translating a live datapath.id back to a GEANT node id must both
    use -- so they can never independently drift apart, which is exactly how
    the "38/40 nodes mismatch" FlowInstaller's docstring warns about happened
    in the first place (two separate re-derivations of this same mapping).
    """
    graph = _load_geant_graph()
    sorted_nodes = sorted(graph.nodes(), key=str)
    return {str(node): (f"s{i}", f"h{i}") for i, node in enumerate(sorted_nodes, start=1)}


def resolve_link_capacity_mbps(link_id_str: str, default_mbps: float = 100.0) -> float:
    """
    Real per-link capacity (Mbps) for one GEANT edge, keyed by a "u-v"
    link id (sorted node pair, matching NetworkState's own convention).
    Falls back to default_mbps for the ~1/3 of edges with no real
    published LinkLabel (deliberately not guessed -- see
    resolve_link_bw_mbps's docstring).
    """
    graph = _load_geant_graph()
    u, v = link_id_str.split("-", 1)
    edge_data = graph.get_edge_data(u, v) or {}
    return resolve_link_bw_mbps(edge_data.get("LinkLabel"), default_mbps=default_mbps)
