#!/usr/bin/env python3
"""
Make Figures - render publication-style, multi-panel matplotlib figures
(box plots, delay-vs-load curves with error bands, violin plots, VIF and
offered-load bar charts) for the generalized scenario results and the
formula-validation statistics, straight from the CSVs already in results/.

Run as: python3 -m experiments.make_figures
Writes PNGs to results/figures/.
"""
from __future__ import annotations

import csv
import json
import random
import statistics as st
from pathlib import Path
from typing import Dict, List

OUTPUT_DIR = Path("results/figures")

COLOR = {"static": "#94a3ab", "dynamic": "#eb6834", "proposed": "#2a78d6"}


def _load_csv(path: Path) -> List[Dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def make_failure_recovery_figure(plt) -> None:
    rows = _load_csv(Path("results/failure_recovery_generalization/summary.csv"))
    fig, axes = plt.subplots(1, 2, figsize=(14, 5.2))
    fig.suptitle(
        "failure_recovery: proposed vs dynamic vs static, 23 real node pairs x 5 seeds\n"
        "stable + unstable (flap-then-settle) restoration, both cases",
        fontsize=13,
    )

    ax = axes[0]
    data, tick_labels, colors = [], [], []
    for case in ("stable", "unstable"):
        for algo in ("static", "dynamic", "proposed"):
            data.append([float(r[f"{case}_{algo}_delay_ms"]) for r in rows])
            tick_labels.append(f"{case}\n{algo}")
            colors.append(COLOR[algo])
    bp = ax.boxplot(data, tick_labels=tick_labels, patch_artist=True, widths=0.6)
    for patch, color in zip(bp["boxes"], colors):
        patch.set_facecolor(color)
        patch.set_alpha(0.65)
    ax.set_ylabel("post-failure delay (ms)")
    ax.set_title("(a) Delay distribution, n=23 pairs/box")
    ax.axvline(3.5, color="#c3c2b7", linestyle="--", linewidth=1)

    ax = axes[1]
    hops = [int(r["hops"]) for r in rows]
    dyn = [float(r["stable_dynamic_delay_ms"]) for r in rows]
    prop = [float(r["stable_proposed_delay_ms"]) for r in rows]
    improvement = [(d - p) / d * 100 for d, p in zip(dyn, prop)]
    rng = random.Random(3)
    jittered_x = [h + rng.uniform(-0.08, 0.08) for h in hops]
    ax.scatter(jittered_x, improvement, color=COLOR["proposed"], alpha=0.75, s=36, zorder=3)
    n = len(hops)
    mean_x, mean_y = sum(hops) / n, sum(improvement) / n
    sxy = sum((x - mean_x) * (y - mean_y) for x, y in zip(hops, improvement))
    sxx = sum((x - mean_x) ** 2 for x in hops)
    slope = sxy / sxx
    intercept = mean_y - slope * mean_x
    r = sxy / ((sxx ** 0.5) * (sum((y - mean_y) ** 2 for y in improvement) ** 0.5))
    x_line = [min(hops), max(hops)]
    ax.plot(x_line, [slope * x + intercept for x in x_line], color="#e34948", linestyle="--",
            label=f"OLS slope={slope:.2f}pp/hop, r={r:.2f}")
    ax.set_xlabel("hop count")
    ax.set_ylabel("proposed's delay improvement over dynamic (%)")
    ax.set_title("(b) Improvement vs hop count (stable case)")
    ax.legend(fontsize=9)

    fig.tight_layout(rect=(0, 0, 1, 0.93))
    out = OUTPUT_DIR / "failure_recovery.png"
    fig.savefig(out, dpi=150)
    plt.close(fig)
    print("Wrote", out)


def make_increasing_load_figure(plt) -> None:
    rows = _load_csv(Path("results/increasing_load_generalization/persample.csv"))
    summary_rows = _load_csv(Path("results/increasing_load_generalization/summary.csv"))
    fig, axes = plt.subplots(1, 2, figsize=(14, 5.2))
    fig.suptitle(
        "increasing_load: monotonic ramp 0.10->0.90, 23 real node pairs x 5 seeds\n"
        "the one scenario where dynamic beats proposed on raw delay",
        fontsize=13,
    )

    ax = axes[0]
    load = [float(r["load_factor"]) for r in rows]
    for algo in ("static", "dynamic", "proposed"):
        mean = [float(r[f"{algo}_mean"]) for r in rows]
        std = [float(r[f"{algo}_std"]) for r in rows]
        ax.plot(load, mean, label=algo, color=COLOR[algo], linewidth=2.2)
        ax.fill_between(load, [m - s for m, s in zip(mean, std)], [m + s for m, s in zip(mean, std)],
                         color=COLOR[algo], alpha=0.15)
    ax.axvline(0.75, color="#c3c2b7", linestyle=":", linewidth=1)
    ax.annotate("dynamic reroutes\n(sample 10)", xy=(0.755, 90), xytext=(0.50, 320),
                fontsize=8.5, color=COLOR["dynamic"],
                arrowprops=dict(arrowstyle="->", color=COLOR["dynamic"], lw=1))
    ax.annotate("proposed reroutes\n2 samples later", xy=(0.83, 42), xytext=(0.60, 130),
                fontsize=8.5, color=COLOR["proposed"],
                arrowprops=dict(arrowstyle="->", color=COLOR["proposed"], lw=1))
    ax.set_xlabel("offered load (fraction of link capacity)")
    ax.set_ylabel("mean delay (ms), +/-1 s.d.")
    ax.set_title("(a) Delay vs offered load")
    ax.legend(fontsize=9)

    ax = axes[1]
    algos = ("static", "dynamic", "proposed")
    means = [st.mean([float(r[f"{a}_reroutes"]) for r in summary_rows]) for a in algos]
    bars = ax.bar(algos, means, color=[COLOR[a] for a in algos], alpha=0.85)
    ax.bar_label(bars, fmt="%.2f")
    ax.set_ylabel("mean reroute count per run")
    ax.set_title("(b) Operational cost (reroute count), mean across 23 pairs")

    fig.tight_layout(rect=(0, 0, 1, 0.93))
    out = OUTPUT_DIR / "increasing_load.png"
    fig.savefig(out, dpi=150)
    plt.close(fig)
    print("Wrote", out)


def make_stale_stats_figure(plt) -> None:
    summary_rows = _load_csv(Path("results/stale_stats_generalization/summary.csv"))
    dd_rows = _load_csv(Path("results/stale_stats_generalization/persample_delayed_detection.csv"))
    fig, axes = plt.subplots(1, 2, figsize=(14, 5.2))
    fig.suptitle(
        "stale_stats: a single observed-only glitch vs. a real sustained event with delayed polls\n"
        "23 real node pairs x 5 seeds per phase",
        fontsize=13,
    )

    ax = axes[0]
    algos = ("static", "dynamic", "proposed")
    fp_rate = [st.mean([float(r[f"{a}_noise_false_reroutes"]) for r in summary_rows]) * 100 for a in algos]
    detect_rate = [st.mean([float(r[f"{a}_detect_rate"]) for r in summary_rows]) * 100 for a in algos]
    x = range(len(algos))
    width = 0.35
    b1 = ax.bar([i - width / 2 for i in x], fp_rate, width, label="false-positive rate (noise phase)", color="#e34948", alpha=0.85)
    b2 = ax.bar([i + width / 2 for i in x], detect_rate, width, label="real-event detection rate", color="#1baf7a", alpha=0.85)
    ax.bar_label(b1, fmt="%.0f%%")
    ax.bar_label(b2, fmt="%.0f%%")
    ax.set_xticks(list(x))
    ax.set_xticklabels(algos)
    ax.set_ylabel("% of 23 pairs x 5 seeds (n=115)")
    ax.set_title("(a) Robustness vs responsiveness")
    ax.legend(fontsize=9)
    ax.set_ylim(0, 115)

    ax = axes[1]
    samples = [int(r["sample"]) for r in dd_rows]
    for algo in algos:
        mean = [float(r[f"{algo}_mean"]) for r in dd_rows]
        std = [float(r[f"{algo}_std"]) for r in dd_rows]
        ax.plot(samples, mean, label=algo, color=COLOR[algo], linewidth=2.2)
        ax.fill_between(samples, [m - s for m, s in zip(mean, std)], [m + s for m, s in zip(mean, std)],
                         color=COLOR[algo], alpha=0.15)
    ax.axvspan(4, 9, color="#e34948", alpha=0.06)
    ax.annotate("real congestion window\n(polls 5 & 7 report stale)", xy=(4.2, 400), fontsize=8.5, color="#b23434")
    ax.set_xlabel("sample")
    ax.set_ylabel("delay (ms)")
    ax.set_title("(b) Delayed-detection phase: delay vs sample")
    ax.legend(fontsize=9)

    fig.tight_layout(rect=(0, 0, 1, 0.93))
    out = OUTPUT_DIR / "stale_stats.png"
    fig.savefig(out, dpi=150)
    plt.close(fig)
    print("Wrote", out)


def make_priority_policy_figure(plt) -> None:
    rows = _load_csv(Path("results/priority_policy_generalization/summary.csv"))
    fig, ax = plt.subplots(figsize=(9, 5.2))
    fig.suptitle(
        "priority_policy: first-reroute sample by traffic class, 23 real node pairs x 5 seeds\n"
        "reroute_immediate ordering (VoIP/Video before Web/File Transfer) holds 23/23",
        fontsize=12.5,
    )
    classes = [
        ("voip_mean_sample", "VoIP", "#2a78d6"),
        ("video_mean_sample", "Video", "#2a78d6"),
        ("web_mean_sample", "Web", "#eda100"),
        ("file_transfer_mean_sample", "File\nTransfer", "#e34948"),
    ]
    data = [[float(r[key]) for r in rows] for key, _, _ in classes]
    parts = ax.violinplot(data, showmedians=True, widths=0.7)
    for pc, (_, _, color) in zip(parts["bodies"], classes):
        pc.set_facecolor(color)
        pc.set_alpha(0.55)
    rng = random.Random(7)
    for i, vals in enumerate(data, start=1):
        xs = [i + rng.uniform(-0.06, 0.06) for _ in vals]
        ax.scatter(xs, vals, color="#17181a", alpha=0.35, s=14, zorder=3)
    ax.set_xticks(range(1, len(classes) + 1))
    ax.set_xticklabels([label for _, label, _ in classes])
    ax.set_ylabel("first-reroute sample")

    fig.tight_layout(rect=(0, 0, 1, 0.90))
    out = OUTPUT_DIR / "priority_policy.png"
    fig.savefig(out, dpi=150)
    plt.close(fig)
    print("Wrote", out)


def make_congestion_figure(plt) -> None:
    """
    3-panel congestion figure: sustained/intermittent delay bars, and a churn-vs-delay
    scatter with every seed's own point plotted individually (not one point per
    algorithm x phase) -- PRIMARY_PAIR only, 5 seeds, not yet generalized to 23 pairs.
    """
    runs = [_load_csv(Path(f"results/pilot/scenario1-2/congestion_run_{i}.csv")) for i in range(1, 6)]

    fig, axes = plt.subplots(1, 3, figsize=(17, 5.2))
    fig.suptitle(
        "congestion: local single-pair spike, 5 seeds -- sustained and intermittent phases\n"
        "PRIMARY_PAIR only (not yet generalized to the 23-pair sample)",
        fontsize=13,
    )

    algos = ("static", "dynamic", "proposed")
    phases = ("sustained", "temporary")
    phase_titles = {"sustained": "(a) Sustained spike", "temporary": "(b) Intermittent spike"}

    # Panels (a)/(b): mean delay per algorithm per phase, with seed-to-seed spread as error bars.
    for ax, phase in zip(axes[:2], phases):
        means, stds = [], []
        for algo in algos:
            per_run_means = [
                st.mean(float(r["delay_ms"]) for r in rows if r["algorithm"] == algo and r["phase"] == phase)
                for rows in runs
            ]
            means.append(st.mean(per_run_means))
            stds.append(st.pstdev(per_run_means))
        bars = ax.bar(algos, means, yerr=stds, capsize=4, color=[COLOR[a] for a in algos], alpha=0.85)
        ax.bar_label(bars, fmt="%.1f", padding=8)
        ax.set_ylabel("mean delay (ms), 5 seeds")
        ax.set_title(phase_titles[phase])

    # Panel (c): reroute count (churn) vs delay -- one point per (algorithm, phase, seed),
    # not aggregated, so the real seed-to-seed spread (e.g. proposed suppressing some
    # intermittent-phase reroutes dynamic still takes) is directly visible.
    ax = axes[2]
    markers = {"sustained": "o", "temporary": "^"}
    for algo in algos:
        for phase in phases:
            reroutes, delays = [], []
            for rows in runs:
                sub = [r for r in rows if r["algorithm"] == algo and r["phase"] == phase]
                if not sub:
                    continue
                reroutes.append(sum(1 for r in sub if r["reroute"] == "True"))
                delays.append(st.mean(float(r["delay_ms"]) for r in sub))
            rng = random.Random(abs(hash((algo, phase))) % (2**31))
            jittered_x = [x + rng.uniform(-0.08, 0.08) for x in reroutes]
            ax.scatter(jittered_x, delays, color=COLOR[algo], marker=markers[phase], alpha=0.75, s=55)

    from matplotlib.lines import Line2D
    color_handles = [Line2D([0], [0], marker="o", color="w", markerfacecolor=COLOR[a], markersize=9, label=a) for a in algos]
    shape_handles = [Line2D([0], [0], marker=markers[p], color="#555555", linestyle="None", markersize=9, label=p) for p in phases]
    ax.legend(handles=color_handles + shape_handles, fontsize=8, loc="best")
    ax.set_xlabel("reroute events in this run/phase (churn)")
    ax.set_ylabel("mean delay (ms)")
    ax.set_title("(c) Churn vs delay, all 5 seeds shown individually")

    fig.tight_layout(rect=(0, 0, 1, 0.90))
    out = OUTPUT_DIR / "congestion.png"
    fig.savefig(out, dpi=150)
    plt.close(fig)
    print("Wrote", out)


def make_weight_search_figure(plt) -> None:
    """
    2-panel weight-search benchmark figure: multi-instance cumulative-regret comparison
    across search methods (50 independent instances/method, matched budget), and
    one-at-a-time weight sensitivity ranked by impact.
    """
    data = json.loads(Path("results/pilot/sensitivity/weight_search_comparison.json").read_text())
    rows = _load_csv(Path("results/pilot/sensitivity/weight_search_multi_instance_comparison.csv"))

    fig, axes = plt.subplots(1, 2, figsize=(14, 5.2))
    fig.suptitle(
        "weight_search: DIRECT vs Bayesian optimization vs simulated annealing vs random search\n"
        "vs DIRECT-then-SA hybrid -- 50 independent instances/method, matched 30-evaluation budget",
        fontsize=13,
    )

    ax = axes[0]
    methods = ["direct", "direct_then_sa_hybrid", "bayesian_optimization", "simulated_annealing", "random_search"]
    method_labels = ["DIRECT", "DIRECT\n+SA hybrid", "Bayesian\nOpt.", "Simulated\nAnnealing", "Random\nSearch"]
    method_colors = ["#2a78d6", "#8858c8", "#1baf7a", "#eda100", "#94a3ab"]
    box_data = [[float(r["cumulative_regret"]) for r in rows if r["method"] == m] for m in methods]
    bp = ax.boxplot(box_data, tick_labels=method_labels, patch_artist=True, widths=0.6)
    for patch, color in zip(bp["boxes"], method_colors):
        patch.set_facecolor(color)
        patch.set_alpha(0.65)
    ax.set_ylabel("cumulative regret over 30 evaluations")
    ax.set_title("(a) Search efficiency, n=50 instances/method")

    ax = axes[1]
    oat = data["oat_sensitivity_ranked_by_impact"]
    labels = [o["variable"] for o in oat]
    ranges = [o["range"] for o in oat]
    bars = ax.barh(labels, ranges, color="#2a78d6", alpha=0.85)
    ax.bar_label(bars, fmt="%.3f", padding=4)
    ax.invert_yaxis()
    ax.set_xlabel("regret range across weight's full [0,1] sweep (OAT)")
    ax.set_title("(b) One-at-a-time sensitivity, ranked by impact")

    fig.tight_layout(rect=(0, 0, 1, 0.90))
    out = OUTPUT_DIR / "weight_search.png"
    fig.savefig(out, dpi=150)
    plt.close(fig)
    print("Wrote", out)


def make_vif_figure(plt) -> None:
    fig, ax = plt.subplots(figsize=(8, 4.2))
    labels = ["utilization", "delay_residual", "loss_residual", "churn_score"]
    values = [2.055, 2.293, 2.836, 1.020]
    colors = ["#2a78d6", "#2a78d6", "#e34948", "#1baf7a"]
    bars = ax.barh(labels, values, color=colors, alpha=0.85)
    ax.bar_label(bars, fmt="%.3f", padding=4)
    ax.axvline(5, color="#b23434", linestyle="--", linewidth=1.2)
    ax.text(5.05, 3.4, "concern threshold (5-10)", color="#b23434", fontsize=9)
    ax.set_xlim(0, 6.5)
    ax.set_xlabel("Variance Inflation Factor")
    ax.set_title(
        "Closing VIF check: the 4 variables with real,\nnon-definitional relationships (n=213, post disjoint-link bugfix)",
        fontsize=11.5,
    )
    fig.tight_layout()
    out = OUTPUT_DIR / "vif.png"
    fig.savefig(out, dpi=150)
    plt.close(fig)
    print("Wrote", out)


def make_offered_load_figure(plt) -> None:
    fig, ax = plt.subplots(figsize=(9, 4.6))
    pairs = ["2->7\n(PRIMARY_PAIR)", "12->25", "0->12", "3->36", "16->23"]
    flips = [4, 0, 0, 5, 0]
    colors = ["#1baf7a" if f > 0 else "#94a3ab" for f in flips]
    bars = ax.bar(pairs, flips, color=colors, alpha=0.85)
    ax.bar_label(bars, labels=[f"{f}/5 rates flip" for f in flips])
    ax.set_ylim(0, 6)
    ax.set_ylabel("rates (of 5 tested) where the\ncorrection changes the decision")
    ax.set_title(
        "Real-hardware offered-load recovery check, 5 node pairs\n"
        "PRIMARY_PAIR's original single-rate check (24 Mbps) had found no boundary at all",
        fontsize=11.5,
    )
    fig.tight_layout(rect=(0.03, 0, 1, 1))
    out = OUTPUT_DIR / "offered_load_recovery.png"
    fig.savefig(out, dpi=150)
    plt.close(fig)
    print("Wrote", out)


def main() -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams.update({
        "font.size": 10.5,
        "axes.edgecolor": "#c3c2b7",
        "axes.grid": True,
        "grid.color": "#e1e0d9",
        "grid.linewidth": 0.7,
        "figure.facecolor": "white",
        "axes.facecolor": "white",
    })

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    make_congestion_figure(plt)
    make_weight_search_figure(plt)
    make_failure_recovery_figure(plt)
    make_increasing_load_figure(plt)
    make_stale_stats_figure(plt)
    make_priority_policy_figure(plt)
    make_vif_figure(plt)
    make_offered_load_figure(plt)


if __name__ == "__main__":
    main()
