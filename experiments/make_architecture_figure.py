#!/usr/bin/env python3
"""
Make Architecture Figure - renders the system's real module composition
(monitor -> decide -> route -> enforce, plus the three compared algorithms'
scope through that pipeline) as a publication-quality diagram, straight
from the actual import graph in src/ -- not a hand-wavy simplification.

Run as: python3 -m experiments.make_architecture_figure
Writes results/figures/system_architecture.png and .pdf.
"""
from __future__ import annotations

from pathlib import Path

OUTPUT_DIR = Path("results/figures")

# Layer fill colors -- distinct from the COLOR dict in make_figures.py (that one
# colors *algorithms*; this one colors *architectural layers*), kept in the same
# muted, print-friendly palette as the rest of the dissertation figure set.
LAYER_FILL = {
    "data_plane": "#e9ecf1",
    "monitor": "#e8f0fa",
    "state": "#fdf3e3",
    "routing": "#e6f3ee",
    "decision": "#f7ebe3",
    "enforce": "#e9ecf1",
    "config": "#f1f0ea",
}
LAYER_EDGE = {
    "data_plane": "#7c8698",
    "monitor": "#2a78d6",
    "state": "#c98a1f",
    "routing": "#1baf7a",
    "decision": "#b5602f",
    "enforce": "#7c8698",
    "config": "#9b9788",
}
ALGO_COLOR = {"static": "#94a3ab", "dynamic": "#eb6834", "proposed": "#2a78d6"}


def _box(ax, x, y, w, h, label, fill, edge, fontsize=10.5, weight="bold", text_color="#17181a", ha="center"):
    from matplotlib.patches import FancyBboxPatch
    ax.add_patch(FancyBboxPatch(
        (x, y), w, h, boxstyle="round,pad=0.02,rounding_size=0.08",
        linewidth=1.3, edgecolor=edge, facecolor=fill, zorder=2,
    ))
    tx = x + w / 2 if ha == "center" else x + 0.12
    ax.text(tx, y + h / 2, label, ha=ha, va="center", fontsize=fontsize,
             fontweight=weight, color=text_color, zorder=3, linespacing=1.35)


def _chip(ax, x, y, w, h, label, edge, fontsize=8.3):
    from matplotlib.patches import FancyBboxPatch
    ax.add_patch(FancyBboxPatch(
        (x, y), w, h, boxstyle="round,pad=0.015,rounding_size=0.05",
        linewidth=0.9, edgecolor=edge, facecolor="white", zorder=3,
    ))
    ax.text(x + w / 2, y + h / 2, label, ha="center", va="center", fontsize=fontsize,
             color="#3d4a5c", zorder=4)


def _arrow(ax, xy_from, xy_to, color="#3d4a5c", label=None, label_pos=0.5, style="-|>", lw=1.4, connectionstyle="arc3,rad=0.0"):
    from matplotlib.patches import FancyArrowPatch
    ax.add_patch(FancyArrowPatch(
        xy_from, xy_to, arrowstyle=style, mutation_scale=13, linewidth=lw,
        color=color, zorder=2.5, connectionstyle=connectionstyle,
    ))
    if label:
        mx = xy_from[0] + (xy_to[0] - xy_from[0]) * label_pos
        my = xy_from[1] + (xy_to[1] - xy_from[1]) * label_pos
        ax.text(mx + 0.12, my, label, fontsize=7.8, color="#5a6472", style="italic", va="center", zorder=4)


def make_architecture_figure(plt) -> None:
    fig, ax = plt.subplots(figsize=(16.6, 11))
    ax.set_xlim(-0.85, 16)
    ax.set_ylim(0, 11)
    ax.axis("off")

    ax.text(0.5, 10.65, "System Architecture", fontsize=19, fontweight="bold", color="#17181a")
    ax.text(0.5, 10.28, "Monitor → State → Route → Decide → Enforce, driven by the real GEANT topology on Mininet/OVS",
            fontsize=11, color="#5a6472")

    PX, PW = 0.5, 10.3  # main pipeline column x-range

    # --- Data Plane ---
    _box(ax, PX, 9.35, PW, 0.75, "Data Plane\nMininet / OVS switches -- real GEANT topology (40 nodes, 61 edges)",
         LAYER_FILL["data_plane"], LAYER_EDGE["data_plane"], fontsize=10.5)

    # --- Monitoring Layer ---
    _box(ax, PX, 7.75, PW, 1.35, "", LAYER_FILL["monitor"], LAYER_EDGE["monitor"])
    ax.text(PX + 0.15, 8.92, "Monitoring Layer", fontsize=10.5, fontweight="bold", color="#173a5e")
    mon_chips = [
        ("StatisticsCollector\n(OVS port stats)", PX + 0.2),
        ("QdiscLossTracker\n(real tc drops)", PX + 3.65),
        ("DelayProber\n(ping RTT)", PX + 7.1),
    ]
    for label, cx in mon_chips:
        _chip(ax, cx, 7.9, 2.95, 0.85, label, LAYER_EDGE["monitor"])

    # --- NetworkState ---
    _box(ax, PX, 6.35, PW, 1.15, "", LAYER_FILL["state"], LAYER_EDGE["state"])
    ax.text(PX + 0.15, 7.32, "NetworkState  (central link/topology store)", fontsize=10.5, fontweight="bold", color="#7a4d0f")
    state_chips = [
        "LinkMonitor", "TopologyState", "HistoryStore",
        "LinkChurnTracker", "DelayJitterTracker", "LossJitterTracker",
    ]
    chip_w = (PW - 0.4 - 5 * 0.1) / 6
    for i, label in enumerate(state_chips):
        _chip(ax, PX + 0.2 + i * (chip_w + 0.1), 6.45, chip_w, 0.6, label, LAYER_EDGE["state"], fontsize=7.6)

    # --- Routing Layer ---
    _box(ax, PX, 4.85, PW, 1.15, "", LAYER_FILL["routing"], LAYER_EDGE["routing"])
    ax.text(PX + 0.15, 5.82, "Routing Layer", fontsize=10.5, fontweight="bold", color="#175f42")
    _chip(ax, PX + 0.2, 4.95, 5.0, 0.65, "GraphBuilder\n7-weight cost: αu+βΔdelay+γΔloss+δchurn+εreliab.+ζjitD+ηjitL", LAYER_EDGE["routing"], fontsize=7.8)
    _chip(ax, PX + 5.35, 4.95, 2.4, 0.65, "CongestionModel\n(calibrated curves)", LAYER_EDGE["routing"], fontsize=7.8)
    _chip(ax, PX + 7.9, 4.95, 2.1, 0.65, "PathCost\n(compare, min-Δ)", LAYER_EDGE["routing"], fontsize=7.8)

    # --- Decision Layer ---
    _box(ax, PX, 2.55, PW, 2.05, "", LAYER_FILL["decision"], LAYER_EDGE["decision"])
    ax.text(PX + 0.15, 4.38, "Decision Layer  --  DecisionEngine (orchestrator)", fontsize=10.5, fontweight="bold", color="#8a4420")
    dec_chips = [
        "ThresholdDetector", "StabilityManager\n(hysteresis, hold-down)", "PersistenceChecker\n(leaky-bucket)",
        "ChangeBudget", "TrafficPolicy\n(priority classes)", "FailureHandler", "RecoveryManager\n(switch-back window)",
    ]
    cols = 4
    cw = (PW - 0.4 - (cols - 1) * 0.12) / cols
    for i, label in enumerate(dec_chips):
        r, c = divmod(i, cols)
        _chip(ax, PX + 0.2 + c * (cw + 0.12), 2.68 + (1 - r) * 0.72, cw, 0.6, label, LAYER_EDGE["decision"], fontsize=7.6)

    # --- Enforcement ---
    _box(ax, PX, 1.35, PW, 0.75, "FlowInstaller  (build_flow_rules -> ovs-ofctl add-flow)",
         LAYER_FILL["enforce"], LAYER_EDGE["enforce"], fontsize=10.2)

    # --- Config strip ---
    _box(ax, PX, 0.25, PW, 0.7, "", LAYER_FILL["config"], LAYER_EDGE["config"])
    cfg_chips = ["config/decision.yaml\n(thresholds, weights, hysteresis, hold-down, budget)",
                 "config/policies.yaml\n(traffic-class priority)", "config/topology.yaml\n(link capacities)"]
    cw2 = (PW - 0.4 - 2 * 0.1) / 3
    for i, label in enumerate(cfg_chips):
        _chip(ax, PX + 0.2 + i * (cw2 + 0.1), 0.32, cw2, 0.55, label, LAYER_EDGE["config"], fontsize=7.3)

    # --- Pipeline arrows (down the main column) ---
    cx = PX + 1.4
    _arrow(ax, (cx, 9.35), (cx, 9.10), label="port stats / tc qdisc / ping RTT", label_pos=0.5)
    _arrow(ax, (cx, 7.75), (cx, 7.50), label="LinkStatistics samples")
    _arrow(ax, (cx, 6.35), (cx, 6.00), label="read link stats + topology")
    _arrow(ax, (cx, 4.85), (cx, 4.60), label="candidate paths + cost")
    _arrow(ax, (cx, 2.55), (cx, 2.10), label="reroute decision (new_path)")
    # loop back: enforcement -> data plane, bowed out to the left of the main column
    _arrow(ax, (PX - 0.05, 1.72), (PX - 0.05, 9.55), color="#7c8698", lw=1.2, connectionstyle="arc3,rad=-0.55")
    ax.text(PX - 0.62, 5.5, "OpenFlow rules pushed to switches", fontsize=7.8, color="#5a6472",
            style="italic", rotation=90, ha="center", va="center")
    # config -> decision (straight, inside the column) / config -> routing (routed around the
    # outside of the Decision/Enforcement boxes on the right, so it doesn't cross their content)
    _arrow(ax, (PX + 1.3, 0.95), (PX + 1.3, 2.55), color="#9b9788", lw=1.1)
    edge_x = PX + PW + 0.15
    _arrow(ax, (PX + 7.5, 0.6), (edge_x, 0.6), color="#9b9788", lw=1.0, style="-")
    _arrow(ax, (edge_x, 0.6), (edge_x, 5.4), color="#9b9788", lw=1.1, style="-")
    _arrow(ax, (edge_x, 5.4), (PX + PW, 5.4), color="#9b9788", lw=1.1)

    # --- Right column: three compared algorithms ---
    RX, RW = 11.15, 4.35
    ax.text(RX, 10.5, "Three Compared Algorithms", fontsize=13, fontweight="bold", color="#17181a")
    ax.text(RX, 10.18, "same topology, same traffic -- differ only in how much\nof the pipeline above each one actually uses", fontsize=8.8, color="#5a6472")

    algo_specs = [
        ("static", "Static",
         "Shortest path via networkx, computed\n"
         "once at startup. Never re-evaluates --\n"
         "step() always returns reroute=False.",
         "StaticShortestPath only.\nBypasses Monitoring, State, Routing's\ncost formula, and the whole Decision Layer.",
         7.65, 1.7),
        ("dynamic", "Dynamic",
         "Recomputes the cheapest path every sample\n"
         "via the real cost formula, and reroutes the\n"
         "moment utilization crosses the threshold --\n"
         "no hysteresis, no persistence, no cooldown.",
         "GraphBuilder + PathCost (Routing Layer) +\nDynamicBaseline's own raw threshold check.\nBypasses every Decision Layer gate.",
         4.975, 2.3),
        ("proposed", "Proposed",
         "Full stability-aware stack via DecisionEngine,\n"
         "every gate below active on every sample:\n"
         "• Hysteresis -- separate enter/release thresholds\n"
         "• Persistence -- leaky-bucket, 1:1 forgive rate\n"
         "• Priority policy -- VoIP/Video skip persistence\n"
         "• Hold-down -- cooldown after every real reroute\n"
         "• Change budget -- caps rule churn per window\n"
         "• Emergency failure bypass + recovery-window-\n"
         "  protected switch-back\n"
         "• Offered-load self-influence correction on\n"
         "  the switchback candidate's own cost",
         "Monitoring -> NetworkState -> Routing\n-> full DecisionEngine -> Enforcement.\nEvery layer above.",
         0.25, 4.35),
    ]
    for algo, title, desc, scope, y, h in algo_specs:
        color = ALGO_COLOR[algo]
        from matplotlib.patches import FancyBboxPatch
        ax.add_patch(FancyBboxPatch((RX, y), RW, h, boxstyle="round,pad=0.02,rounding_size=0.08",
                                     linewidth=1.6, edgecolor=color, facecolor="white", zorder=2))
        ax.add_patch(FancyBboxPatch((RX, y + h - 0.42), RW, 0.42, boxstyle="round,pad=0.0,rounding_size=0.0",
                                     linewidth=0, facecolor=color, alpha=0.85, zorder=2.2))
        ax.text(RX + 0.15, y + h - 0.21, title, fontsize=11.5, fontweight="bold", color="white", va="center", zorder=3)
        ax.text(RX + 0.15, y + h - 0.68, desc, fontsize=8.4, color="#17181a", va="top", zorder=3, linespacing=1.4)
        ax.text(RX + 0.15, y + 0.28, "Uses:", fontsize=7.8, fontweight="bold", color="#5a6472", va="top", zorder=3)
        ax.text(RX + 0.7, y + 0.28, scope, fontsize=7.8, color="#5a6472", va="top", zorder=3, linespacing=1.4)

    fig.tight_layout()
    for ext in ("png", "pdf"):
        out = OUTPUT_DIR / f"system_architecture.{ext}"
        fig.savefig(out, dpi=170 if ext == "png" else None)
        print("Wrote", out)
    plt.close(fig)


def main() -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams.update({"font.family": "sans-serif"})

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    make_architecture_figure(plt)


if __name__ == "__main__":
    main()
