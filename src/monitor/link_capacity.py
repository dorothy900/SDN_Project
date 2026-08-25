#!/usr/bin/env python3
"""
Link Capacity - resolve data/Geant2012.graphml's real per-link bandwidth
labels into Mininet-usable Mbit/s values.

39 of the 61 real GEANT edges carry a real `LinkLabel` attribute (e.g.
"10 Gbps"), sourced from GEANT's own published network map (see
compliance_check.md for the GraphML provenance metadata: DateObtained,
Source=geant.net, Provenance=Primary) -- previously ignored entirely, with
topology.py applying one flat bw= to every link regardless of this real,
per-link variation.

Real values (155Mbps to 10Gbps) are not directly usable in Mininet on this
test VM: a single iperf UDP stream already tops out around ~50-55Mbit/s here
(results/independence_check/, results/correlation_check/), so a link
literally configured at 10Gbps would never be reachable by any traffic this
environment can actually generate. REAL_LINK_LABEL_TO_MBPS instead maps each
real tier to a *scaled-down* Mininet value, preserving the real relative
ordering (10Gbps links get more simulated bandwidth than 155Mbps links) while
keeping every value within a range real generated traffic can actually
saturate for future experiments.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

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
    edges) once per process. Moved here from experiments/simulation_common.py
    2026-08-20 so production code (PathCost's per-edge offered-load
    correction) can resolve real per-link capacity too, not just offline
    experiments -- previously only wired up on the experiments side."""
    global _geant_graph_cache
    if _geant_graph_cache is None:
        import networkx as nx

        graph = nx.Graph(nx.read_graphml(_GEANT_GRAPHML_PATH))
        graph.remove_edges_from(nx.selfloop_edges(graph))
        _geant_graph_cache = graph
    return _geant_graph_cache


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
