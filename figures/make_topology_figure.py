#!/usr/bin/env python3
"""
Make Topology Figure - renders the real GEANT2012 topology (40 nodes, 61
edges) this entire project runs on, geographically laid out from the
GraphML's own real Latitude/Longitude per node (an Internet Topology Zoo
export), edge width/color by the real published capacity tier
(src/monitor/link_capacity.py's REAL_LINK_LABEL_TO_MBPS scale) rather than
an arbitrary spring layout -- so the figure is the actual network, not a
generic graph drawing.

Also marks two things this project's own experiments found on this real
topology, not just decoration: the two structural-bridge edges (12-20,
21-27) that have no genuine alternative route anywhere in the graph for
their own endpoints (why the real-hardware congestion demo could not use
either of them -- see scripts/ryu/ryu_real_congestion_probe.py's docstring),
and the monitored pair (node 0 <-> node 12) together with the hotspot
link (0-4) that demo actually used instead.

Run as: python3 -m figures.make_topology_figure
Writes results/figures/topology.png and .pdf.
"""
from __future__ import annotations

from pathlib import Path

from src.monitor.link_capacity import REAL_LINK_LABEL_TO_MBPS, _load_geant_graph

OUTPUT_DIR = Path("results/figures")

# Real published capacity tier -> (edge width, color) -- widest/darkest for the
# highest tier, matching REAL_LINK_LABEL_TO_MBPS's own real relative ordering.
TIER_STYLE = {
    "10 Gbps": (2.6, "#173a5e"),
    "Lit Fibre": (2.6, "#173a5e"),
    "2.5 Gbps": (2.0, "#2a78d6"),
    "1 Gbps": (1.4, "#6fa8e8"),
    "155 Mbps": (1.0, "#b7cdea"),
    None: (0.9, "#c3c2b7"),  # no real published label (22 of 61 edges)
}

BRIDGE_EDGES = {frozenset(("12", "20")), frozenset(("21", "27"))}
HOTSPOT_EDGE = frozenset(("0", "4"))
MONITORED_PAIR = {"0", "12"}

# 3 of the 40 real GEANT nodes (Ukraine, Moldova, Belarus) carry no
# Latitude/Longitude in the source GraphML at all -- not something this
# project removed. Real capital-city coordinates (public knowledge, not
# derived from or claiming anything about the network itself) fill in a
# placement for these 3 only, purely so the figure can render; every other
# node's position below is the source data's own real value, unmodified.
MISSING_COORDS_FALLBACK = {
    "10": (30.5234, 50.4501),  # UA -- Kyiv
    "11": (28.8638, 47.0105),  # MD -- Chisinau
    "19": (27.5615, 53.9006),  # BY -- Minsk
}


def make_topology_figure(plt) -> None:
    graph = _load_geant_graph()
    pos = {}
    for n, d in graph.nodes(data=True):
        if "Longitude" in d and "Latitude" in d:
            pos[n] = (float(d["Longitude"]), float(d["Latitude"]))
        else:
            pos[n] = MISSING_COORDS_FALLBACK[n]

    fig, ax = plt.subplots(figsize=(11, 10.5))

    # --- Edges, real capacity tier by width/color; the two structural bridges
    # and the real congestion-demo hotspot link drawn last, on top, highlighted. ---
    for u, v, data in graph.edges(data=True):
        key = frozenset((u, v))
        if key in BRIDGE_EDGES or key == HOTSPOT_EDGE:
            continue
        width, color = TIER_STYLE.get(data.get("LinkLabel"), TIER_STYLE[None])
        x0, y0 = pos[u]
        x1, y1 = pos[v]
        ax.plot([x0, x1], [y0, y1], color=color, linewidth=width, zorder=1, solid_capstyle="round")

    for u, v in (tuple(e) for e in BRIDGE_EDGES):
        x0, y0 = pos[u]
        x1, y1 = pos[v]
        ax.plot([x0, x1], [y0, y1], color="#e34948", linewidth=2.2, linestyle=(0, (4, 2)), zorder=2)

    x0, y0 = pos["0"]
    x1, y1 = pos["4"]
    ax.plot([x0, x1], [y0, y1], color="#1baf7a", linewidth=3.0, zorder=2)

    # --- Nodes ---
    for n, (x, y) in pos.items():
        if n in MONITORED_PAIR:
            ax.scatter([x], [y], s=110, color="#1baf7a", edgecolor="#0d5c3b", linewidth=1.3, zorder=4)
        else:
            ax.scatter([x], [y], s=42, color="white", edgecolor="#5a6472", linewidth=1.0, zorder=3)
        label = graph.nodes[n].get("label", n)
        ax.annotate(label, (x, y), textcoords="offset points", xytext=(4, 3), fontsize=6.6, color="#3d4a5c", zorder=5)

    # --- Legend (manual proxy artists -- this is a geographic line/scatter
    # plot, not something matplotlib's own legend can infer tier styling from) ---
    from matplotlib.lines import Line2D
    legend_elems = [
        Line2D([0], [0], color=TIER_STYLE["10 Gbps"][1], linewidth=TIER_STYLE["10 Gbps"][0], label="10 Gbps / Lit Fibre (real label)"),
        Line2D([0], [0], color=TIER_STYLE["2.5 Gbps"][1], linewidth=TIER_STYLE["2.5 Gbps"][0], label="2.5 Gbps (real label)"),
        Line2D([0], [0], color=TIER_STYLE["1 Gbps"][1], linewidth=TIER_STYLE["1 Gbps"][0], label="1 Gbps (real label)"),
        Line2D([0], [0], color=TIER_STYLE["155 Mbps"][1], linewidth=TIER_STYLE["155 Mbps"][0], label="155 Mbps (real label)"),
        Line2D([0], [0], color=TIER_STYLE[None][1], linewidth=TIER_STYLE[None][0], label="no published label (falls back to default)"),
        Line2D([0], [0], color="#e34948", linewidth=2.2, linestyle=(0, (4, 2)),
               label="structural bridge (12-20, 21-27) -- no alternative\nroute exists anywhere in the graph for either endpoint"),
        Line2D([0], [0], color="#1baf7a", linewidth=3.0, label="hotspot link (0-4) -- real induced-congestion demo"),
        Line2D([0], [0], marker="o", color="none", markerfacecolor="#1baf7a", markeredgecolor="#0d5c3b",
               markersize=9, label="monitored pair (node 0 <-> node 12)"),
    ]
    ax.legend(handles=legend_elems, loc="lower left", fontsize=7.6, framealpha=0.92)

    ax.set_title(
        "Real GEANT2012 topology (40 nodes, 61 edges) -- geographic layout from the GraphML's\n"
        "own Latitude/Longitude; edge capacity tiers as scaled by src/monitor/link_capacity.py",
        fontsize=12,
    )
    ax.set_xlabel("Longitude")
    ax.set_ylabel("Latitude")
    ax.set_aspect(1.55)  # crude equirectangular correction for this latitude band, not a true projection
    fig.tight_layout()
    for ext in ("png", "pdf"):
        out = OUTPUT_DIR / f"topology.{ext}"
        fig.savefig(out, dpi=170 if ext == "png" else None)
        print("Wrote", out)
    plt.close(fig)


def main() -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams.update({"font.family": "sans-serif"})

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    make_topology_figure(plt)


if __name__ == "__main__":
    main()
