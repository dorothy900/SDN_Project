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
