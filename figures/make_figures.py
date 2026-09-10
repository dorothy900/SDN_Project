#!/usr/bin/env python3
"""
Make Figures - render the multi-panel result figures (box plots, delay-vs-load
curves with error bands, ROC / Youden-J panels, VIF and offered-load bar
charts) straight from the CSVs already in results/.

The figures carry no overall title: in the write-up the descriptive title
(setting, protocol, notation) belongs in the LaTeX \\caption, not baked into
the image.  Panel labels "(a)" / "(b)" stay, since they are referenced from
the caption.  Each figure is written as both .png (preview) and .pdf (vector,
for \\includegraphics).

Run as: python3 -m figures.make_figures
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


def _save(fig, name: str) -> None:
    """Write <name>.png and <name>.pdf into results/figures/."""
    for ext in ("png", "pdf"):
        out = OUTPUT_DIR / f"{name}.{ext}"
        fig.savefig(out, dpi=200, bbox_inches="tight")
        print("Wrote", out)
    import matplotlib.pyplot as _plt
    _plt.close(fig)


def make_failure_recovery_figure(plt) -> None:
    rows = _load_csv(Path("results/failure_recovery_generalization/summary.csv"))
    fig, axes = plt.subplots(1, 2, figsize=(14, 5.2))
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
    ax.legend()

    fig.tight_layout()
    _save(fig, "failure_recovery")


def make_failure_recovery_reroute_count_figure(plt) -> None:
    """
    Reroute count (churn) comparison for failure_recovery, dynamic vs proposed, stable vs
    unstable case -- the direct churn-reduction evidence for this scenario: proposed's
    reroute count barely moves between the stable and the flap-then-settle unstable case,
    while dynamic's roughly doubles, since it has no stability gate against the extra flap.
    """
    rows = _load_csv(Path("results/failure_recovery_generalization/summary.csv"))
    fig, ax = plt.subplots(figsize=(8, 5.2))
    cases = ("stable", "unstable")
    algos = ("dynamic", "proposed")
    x = range(len(cases))
    width = 0.32
    for i, algo in enumerate(algos):
        means = [st.mean([float(r[f"{case}_{algo}_reroutes"]) for r in rows]) for case in cases]
        stds = [st.pstdev([float(r[f"{case}_{algo}_reroutes"]) for r in rows]) for case in cases]
        bars = ax.bar([xi + (i - 0.5) * width for xi in x], means, width, yerr=stds, capsize=4,
                      label=algo, color=COLOR[algo], alpha=0.85)
        ax.bar_label(bars, fmt="%.2f", padding=8)
    ax.set_xticks(list(x))
    ax.set_xticklabels(cases)
    ax.set_ylabel("mean reroute count per run, n=23 pairs")
    ax.legend()

    fig.tight_layout()
    _save(fig, "failure_recovery_reroute_count")


def make_increasing_load_figure(plt) -> None:
    rows = _load_csv(Path("results/increasing_load_generalization/persample.csv"))
    summary_rows = _load_csv(Path("results/increasing_load_generalization/summary.csv"))
    fig, axes = plt.subplots(1, 2, figsize=(14, 5.2))
    ax = axes[0]
    load = [float(r["load_factor"]) for r in rows]
    for algo in ("static", "dynamic", "proposed"):
        mean = [float(r[f"{algo}_mean"]) for r in rows]
        std = [float(r[f"{algo}_std"]) for r in rows]
        ax.plot(load, mean, label=algo, color=COLOR[algo], linewidth=2.2)
        ax.fill_between(load, [m - s for m, s in zip(mean, std)], [m + s for m, s in zip(mean, std)],
                         color=COLOR[algo], alpha=0.15)
    ax.set_xlabel("offered load (fraction of link capacity)")
    ax.set_ylabel("mean delay (ms), ±1 s.d.")
    ax.set_title("(a) Delay vs offered load")
    ax.legend()

    ax = axes[1]
    algos = ("static", "dynamic", "proposed")
    means = [st.mean([float(r[f"{a}_reroutes"]) for r in summary_rows]) for a in algos]
    bars = ax.bar(algos, means, color=[COLOR[a] for a in algos], alpha=0.85)
    ax.bar_label(bars, fmt="%.2f")
    ax.set_ylabel("mean reroute count per run")
    ax.set_title("(b) Operational cost (reroute count), mean across 23 pairs")

    fig.tight_layout()
    _save(fig, "increasing_load")


def make_stale_stats_figure(plt) -> None:
    summary_rows = _load_csv(Path("results/stale_stats_generalization/summary.csv"))
    dd_rows = _load_csv(Path("results/stale_stats_generalization/persample_delayed_detection.csv"))
    fig, axes = plt.subplots(1, 2, figsize=(14, 5.2))
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
    ax.legend()
    ax.set_ylim(0, 115)

    ax = axes[1]
    samples = [int(r["sample"]) for r in dd_rows]
    for algo in algos:
        mean = [float(r[f"{algo}_mean"]) for r in dd_rows]
        std = [float(r[f"{algo}_std"]) for r in dd_rows]
        ax.plot(samples, mean, label=algo, color=COLOR[algo], linewidth=2.2)
        ax.fill_between(samples, [m - s for m, s in zip(mean, std)], [m + s for m, s in zip(mean, std)],
                         color=COLOR[algo], alpha=0.15)
    ax.axvspan(4, 9, color="#e34948", alpha=0.08, label="injected congestion")
    ax.set_xlabel("sample")
    ax.set_ylabel("delay (ms)")
    ax.set_title("(b) Delayed-detection phase: delay vs sample")
    ax.legend()

    fig.tight_layout()
    _save(fig, "stale_stats")


def make_priority_policy_figure(plt) -> None:
    rows = _load_csv(Path("results/priority_policy_generalization/summary.csv"))
    fig, ax = plt.subplots(figsize=(9, 4.3))
    classes = [
        ("voip_mean_sample", "VoIP", "#2a78d6"),
        ("video_mean_sample", "Video", "#2a78d6"),
        ("web_mean_sample", "Web", "#eda100"),
        ("file_transfer_mean_sample", "File\nTransfer", "#e34948"),
    ]
    # Strip plot, not a violin: the per-pair values are means of small integers
    # and heavily tied (File Transfer is a single value across all 23 pairs), so
    # a KDE would invent a continuous spread that is not in the data.
    data = [[float(r[key]) for r in rows] for key, _, _ in classes]
    rng = random.Random(7)
    for i, (vals, (_, _, color)) in enumerate(zip(data, classes), start=1):
        xs = [i + rng.uniform(-0.11, 0.11) for _ in vals]
        ax.scatter(xs, vals, color=color, alpha=0.55, s=26, zorder=3, edgecolors="none")
        med = st.median(vals)
        ax.plot([i - 0.24, i + 0.24], [med, med], color=color, lw=2.4, zorder=4)
        ax.plot([i - 0.16, i + 0.16], [min(vals), min(vals)], color=color, lw=1.0, alpha=0.6)
        ax.plot([i - 0.16, i + 0.16], [max(vals), max(vals)], color=color, lw=1.0, alpha=0.6)
    ax.set_xticks(range(1, len(classes) + 1))
    ax.set_xticklabels([label for _, label, _ in classes])
    ax.set_xlim(0.5, len(classes) + 0.5)
    ax.set_ylabel("mean first-reroute sample  (per node pair, n = 23)")
    from matplotlib.lines import Line2D
    ax.legend(handles=[Line2D([0], [0], color="#555", lw=2.4, label="median"),
                       Line2D([0], [0], color="#555", lw=1.0, alpha=0.6, label="min / max")],
              loc="center right", framealpha=0.9)

    fig.tight_layout()
    _save(fig, "priority_policy")


def make_congestion_figure(plt) -> None:
    """
    2-panel congestion figure from the real 23-pair generalization: delay distribution
    across pairs, and the reroute-agreement finding (dynamic and proposed reroute
    identically here because the monitored flow is a reroute_immediate priority class,
    not because persistence is doing anything -- see congestion.py's module docstring).
    """
    rows = _load_csv(Path("results/congestion_generalization/summary.csv"))

    fig, axes = plt.subplots(1, 2, figsize=(14, 5.2))
    algos = ("static", "dynamic", "proposed")
    phases = ("temporary", "sustained")
    # Sample counts (transient: 8 samples / 2 over threshold; sustained: 12 / 6)
    # are stated in the caption, not on the axis -- see congestion_generalization.py.
    phase_labels = {"temporary": "transient", "sustained": "sustained"}

    ax = axes[0]
    data, tick_labels, colors = [], [], []
    for phase in phases:
        for algo in algos:
            data.append([float(r[f"{phase}_{algo}_delay_ms"]) for r in rows])
            tick_labels.append(f"{phase_labels[phase]}\n{algo}")
            colors.append(COLOR[algo])
    bp = ax.boxplot(data, tick_labels=tick_labels, patch_artist=True, widths=0.6)
    for patch, color in zip(bp["boxes"], colors):
        patch.set_facecolor(color)
        patch.set_alpha(0.65)
    ax.set_ylabel("mean delay (ms), n=23 pairs/box")
    ax.set_title("(a) Delay distribution")
    ax.axvline(3.5, color="#c3c2b7", linestyle="--", linewidth=1)

    # Panel (b): reroute rate per phase/algorithm -- dynamic and proposed match exactly
    # (both 1.0/1.0 across all 23 pairs, both phases) because flow-video-1's service_type
    # ("Video") is config/policies.yaml's reroute_immediate class, so proposed's
    # persistence gate never actually engages for it here.
    ax = axes[1]
    x = range(len(phases))
    width = 0.25
    for i, algo in enumerate(algos):
        vals = [st.mean([float(r[f"{phase}_{algo}_reroutes"]) for r in rows]) for phase in phases]
        bars = ax.bar([xi + (i - 1) * width for xi in x], vals, width, label=algo, color=COLOR[algo], alpha=0.85)
        ax.bar_label(bars, fmt="%.2f")
    ax.set_xticks(list(x))
    ax.set_xticklabels([phase_labels[p] for p in phases])
    ax.set_ylabel("mean reroute count, n=23 pairs")
    ax.set_ylim(0, 1.3)
    ax.set_title("(b) Reroute count per phase")
    ax.legend()

    fig.tight_layout()
    _save(fig, "congestion")


def make_weight_search_figure(plt) -> None:
    """
    2-panel weight-search benchmark figure: multi-instance cumulative-regret comparison
    across search methods (50 independent instances/method, matched budget), and
    one-at-a-time weight sensitivity ranked by impact.
    """
    data = json.loads(Path("results/pilot/sensitivity/weight_search_comparison.json").read_text())
    rows = _load_csv(Path("results/pilot/sensitivity/weight_search_multi_instance_comparison.csv"))

    fig, axes = plt.subplots(1, 2, figsize=(14, 5.2))
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

    fig.tight_layout()
    _save(fig, "weight_search")


def make_resilience_avoidance_figure(plt) -> None:
    """
    The real-scenario evidence for the resilience-avoidance layer (every other
    resilience artefact is synthetic ground truth only). A link on the flow's
    cost-optimal path loses packets far above its utilisation-predicted rate
    while staying up and uncongested. static / dynamic / proposed_noresil all
    ride it the whole episode -- an ordinary threshold reroute never sees a
    lossy-but-uncongested link, and a reactive baseline can't either. proposed
    (resilience ON, config default) detects it via LossJitterTracker and moves
    the flow off with DecisionEngine.evaluate_resilience_avoidance. Two rows,
    one per loss-signal path, from experiments/resilience/resilience_avoidance.py:
      abnormal_loss - clean baseline then a sustained loss shift (3-sigma term).
      chronic_loss  - the same loss from sample 1, no shift (absolute-level term).
    """
    all_rows = [r for r in _load_csv(Path("results/resilience_avoidance/summary.csv"))
                if int(r["scored_seeds"]) > 0]
    # Episode length: static / dynamic ride the anomaly link every sample, so the
    # largest on-anomaly-link count in the data is the per-episode sample count.
    episode_samples = int(round(max(float(r["static_on_anomaly_link_samples"]) for r in all_rows)))
    labels = {"proposed_noresil": "static / dynamic /\nproposed (resil. OFF)",
              "proposed": "proposed\n(resil. ON)"}
    colors = {"proposed_noresil": "#c98a2b", "proposed": COLOR["proposed"]}
    algos = ("proposed_noresil", "proposed")

    scen_label = {"abnormal_loss": "Abnormal loss (sudden shift)",
                  "chronic_loss": "Chronic loss (bad from the start)"}
    panel = iter("abcd")
    fig, axes = plt.subplots(2, 2, figsize=(12.5, 10.5))
    for row, scenario in enumerate(("abnormal_loss", "chronic_loss")):
        rows = [r for r in all_rows if r["scenario"] == scenario]
        _mean = lambda col: st.mean([float(r[col]) for r in rows])
        _pstd = lambda col: st.pstdev([float(r[col]) for r in rows])

        ax = axes[row][0]
        means = [100 * _mean(f"{a}_loss_rate") for a in algos]
        stds = [100 * _pstd(f"{a}_loss_rate") for a in algos]
        bars = ax.bar(range(len(algos)), means, 0.55, yerr=stds, capsize=4,
                      color=[colors[a] for a in algos], alpha=0.9)
        ax.bar_label(bars, fmt="%.2f", padding=6)
        ax.set_xticks(range(len(algos)))
        ax.set_xticklabels([labels[a] for a in algos])
        ax.set_ylabel("mean flow packet loss\nover the episode (%)")
        ax.set_title("(%s) %s — flow packet loss" % (next(panel), scen_label[scenario]))

        ax = axes[row][1]
        means = [_mean(f"{a}_delay_ms") for a in algos]
        stds = [_pstd(f"{a}_delay_ms") for a in algos]
        exposure = [_mean(f"{a}_on_anomaly_link_samples") for a in algos]
        bars = ax.bar(range(len(algos)), means, 0.55, yerr=stds, capsize=4,
                      color=[colors[a] for a in algos], alpha=0.9)
        ax.bar_label(bars, labels=["%.0f ms\n(%.0f/%d on bad link)" % (m, e, episode_samples)
                                   for m, e in zip(means, exposure)], padding=6)
        ax.set_xticks(range(len(algos)))
        ax.set_xticklabels([labels[a] for a in algos])
        ax.set_ylabel("mean flow delay\nover the episode (ms)")
        ax.set_ylim(0, max(means) * 1.45)
        ax.set_title("(%s) %s — flow delay" % (next(panel), scen_label[scenario]))

    fig.tight_layout()
    _save(fig, "resilience_avoidance")


def make_resilience_figure(plt) -> None:
    """
    Threshold-selection figure for BOTH resilience signals, from
    experiments/resilience/resilience_sensitivity.py's ROC / Youden's-J
    searches (300 randomized instances/class):
      top row    -- flap signal (LinkFlapTracker.get_flap_score); positive =
                    "genuinely flapping", negative = "one legitimate transition".
      bottom row -- loss signal (LossJitterTracker.get_abnormal_loss_score);
                    positive = "sustained excess loss", negative = 50% honestly
                    priced / 50% one-or-two isolated bad polls, swept over
                    LOSS_LEVEL_CAP.

    The class instances are synthetic ground truth (there is no labelled
    real-traffic flap/loss dataset), but the *scored functions* are the real
    production code, and the operating point this figure picks (avoid_threshold
    = 0.57) was then confirmed to fire correctly on real OVS + iperf:
    scripts/mininet/link_flap_check.py (4/4 PASS) and abnormal_loss_check.py
    (10/10 PASS) -- annotated on panels (b) and (d).
    """
    roc_rows = _load_csv(Path("results/resilience_sensitivity/roc.csv"))
    loss_roc = _load_csv(Path("results/resilience_sensitivity/roc_loss.csv"))
    loss_caps_csv = _load_csv(Path("results/resilience_sensitivity/loss_cap_sensitivity.csv"))
    half_lives = sorted({float(r["half_life_seconds"]) for r in roc_rows})
    hl_colors = {5.0: "#94a3ab", 10.0: "#eda100", 20.0: "#2a78d6", 40.0: "#1baf7a", 60.0: "#8858c8"}
    CONFIG_CAP = 0.05

    fig, axes = plt.subplots(2, 2, figsize=(13, 10.4))
    ax = axes[0][0]
    ax.plot([0, 1], [0, 1], linestyle="--", color="#b0b6bf", linewidth=1, label="chance")
    for hl in half_lives:
        hl_rows = sorted((r for r in roc_rows if float(r["half_life_seconds"]) == hl),
                          key=lambda r: float(r["threshold"]))
        fpr = [float(r["fpr"]) for r in hl_rows]
        tpr = [float(r["tpr"]) for r in hl_rows]
        label = f"half-life = {hl:.0f} s" + ("  (operating point)" if hl == 20.0 else "")
        ax.plot(fpr, tpr, color=hl_colors.get(hl, "#333333"), linewidth=1.8, label=label)
    ax.set_xlabel("FPR  (false positives on 'one legitimate transition')")
    ax.set_ylabel("TPR  (true positives on 'genuinely flapping')")
    ax.set_title("(a) Flap signal — ROC by half-life")
    ax.legend(loc="lower right")
    ax.set_xlim(-0.02, 1.02)
    ax.set_ylim(-0.02, 1.02)

    ax = axes[0][1]
    hl20 = sorted((r for r in roc_rows if float(r["half_life_seconds"]) == 20.0),
                  key=lambda r: float(r["threshold"]))
    thresholds = [float(r["threshold"]) for r in hl20]
    j_values = [float(r["youden_j"]) for r in hl20]
    ax.plot(thresholds, j_values, color="#2a78d6", linewidth=1.8)
    plateau = [t for t, j in zip(thresholds, j_values) if j >= 0.999]
    if plateau:
        ax.axvspan(plateau[0], plateau[-1], color="#2a78d6", alpha=0.12,
                   label=f"J=1.0 plateau [{plateau[0]:.2f}, {plateau[-1]:.2f}]")
    ax.axvline(0.57, color="#e34948", linestyle="--", linewidth=1.4, label="operating point = 0.57")
    ax.axvline(0.70, color="#94a3ab", linestyle=":", linewidth=1.2, label="earlier hand-picked value = 0.70")
    ax.set_xlabel("avoidance threshold")
    ax.set_ylabel("Youden's J  =  TPR − FPR")
    ax.set_title("(b) Flap signal (half-life 20 s) — Youden's J vs threshold")
    ax.legend(loc="lower left")
    ax.set_xlim(-0.02, 1.02)
    ax.set_ylim(-0.05, 1.08)

    ax = axes[1][0]
    ax.plot([0, 1], [0, 1], linestyle="--", color="#b0b6bf", linewidth=1, label="chance")
    caps = sorted({float(r["loss_level_cap"]) for r in loss_roc})
    cap_colors = {0.02: "#94a3ab", 0.03: "#eda100", 0.04: "#1baf7a", 0.05: "#2a78d6",
                  0.07: "#d1604d", 0.10: "#8858c8"}
    for cap in caps:
        cap_rows = sorted((r for r in loss_roc if abs(float(r["loss_level_cap"]) - cap) < 1e-9),
                          key=lambda r: float(r["threshold"]))
        fpr = [float(r["fpr"]) for r in cap_rows]
        tpr = [float(r["tpr"]) for r in cap_rows]
        label = f"loss-level cap = {cap:.2f}" + ("  (operating point)" if abs(cap - CONFIG_CAP) < 1e-9 else "")
        lw = 2.4 if abs(cap - CONFIG_CAP) < 1e-9 else 1.5
        ax.plot(fpr, tpr, color=cap_colors.get(cap, "#333333"), linewidth=lw, label=label)
    ax.set_xlabel("FPR  (honest pricing / isolated bad polls)")
    ax.set_ylabel("TPR  (sustained excess loss)")
    ax.set_title("(c) Loss signal — ROC by loss-level cap")
    ax.legend(loc="lower right")
    ax.set_xlim(-0.02, 1.02)
    ax.set_ylim(-0.02, 1.02)

    ax = axes[1][1]
    cfg_rows = sorted((r for r in loss_roc if abs(float(r["loss_level_cap"]) - CONFIG_CAP) < 1e-9),
                      key=lambda r: float(r["threshold"]))
    t = [float(r["threshold"]) for r in cfg_rows]
    j = [float(r["youden_j"]) for r in cfg_rows]
    ax.plot(t, j, color="#2a78d6", linewidth=1.8)
    j_best = max(range(len(j)), key=lambda i: j[i])
    ax.axvline(t[j_best], color="#1baf7a", linestyle=":", linewidth=1.3,
               label=f"Youden-optimal = {t[j_best]:.2f} (J={j[j_best]:.2f})")
    cfg_cap_row = next((r for r in loss_caps_csv if abs(float(r["loss_level_cap"]) - CONFIG_CAP) < 1e-9), None)
    if cfg_cap_row is not None:
        op_label = "operating point = 0.57  (TPR %.2f, FPR %.2f)" % (
            float(cfg_cap_row["tpr_at_config_0.57"]), float(cfg_cap_row["fpr_at_config_0.57"]))
    else:
        op_label = "operating point = 0.57"
    ax.axvline(0.57, color="#e34948", linestyle="--", linewidth=1.4, label=op_label)
    ax.set_title("(d) Loss signal (loss-level cap 0.05) — Youden's J vs threshold")
    ax.set_xlabel("avoidance threshold")
    ax.set_ylabel("Youden's J  =  TPR − FPR")
    ax.legend(loc="lower left")
    ax.set_xlim(-0.02, 1.02)
    ax.set_ylim(-0.05, 1.08)

    fig.tight_layout()
    _save(fig, "resilience_sensitivity")


def make_vif_figure(plt) -> None:
    """
    Scoped VIF over the 4 variables the 7-weight formula prices from a
    residual/independent angle (delay_residual / loss_residual, not raw
    delay_ms / loss -- those are collinear with utilisation by construction,
    VIF > 200, see results/joint_independence_matrix/joint_report.md).

    Sourced entirely from results/hybrid_congestion_churn_matrix/scoped_vif.csv,
    written by experiments/cost_formula/hybrid_congestion_churn_matrix.py. If
    that file is missing the figure is skipped (no transcribed / hand-entered
    numbers are ever drawn) -- run that experiment first.
    """
    keys = ["utilization", "delay_residual", "loss_residual", "churn_score"]
    labels = ["utilisation", "delay residual", "loss residual", "churn score"]
    scoped = Path("results/hybrid_congestion_churn_matrix/scoped_vif.csv")
    if not scoped.exists():
        print("Skipping vif -- run experiments/cost_formula/hybrid_congestion_churn_matrix.py first")
        return
    scoped_rows = _load_csv(scoped)
    by_var = {r["variable"]: float(r["vif"]) for r in scoped_rows}
    values = [by_var[k] for k in keys]
    n = scoped_rows[0]["n"]

    fig, ax = plt.subplots(figsize=(8, 4.6))
    colors = ["#2a78d6", "#2a78d6", "#e34948", "#1baf7a"]
    bars = ax.barh(labels, values, color=colors, alpha=0.85)
    ax.bar_label(bars, fmt="%.3f", padding=4)
    ax.axvline(5, color="#b23434", linestyle="--", linewidth=1.2,
               label="conventional concern threshold (VIF 5–10)")
    ax.legend(loc="lower right")
    ax.set_xlim(0, 6.5)
    ax.set_xlabel("variance inflation factor   (n = %s)" % n)
    fig.tight_layout()
    _save(fig, "vif")


def _offered_load_flip_counts() -> List[tuple]:
    """
    (pair_label, flip_count, rates_swept) from the real per-pair reports the
    mininet offered-load check wrote. A 'flip' is a background rate where the
    offered-load correction changed the accept/reject decision
    (without_accepted != with_accepted in that pair's markdown table);
    rates_swept is that pair's total number of rate rows.
    """
    base = Path("results/mininet_offered_load_recovery_check")
    out = []
    for d in sorted(base.glob("*_*")):
        if not d.is_dir():
            continue
        src, dst = d.name.split("_", 1)
        text = (d / "report.md").read_text() if (d / "report.md").exists() else ""
        flips = rates = 0
        for line in text.splitlines():
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if len(cells) >= 4 and cells[2] in ("True", "False") and cells[3] in ("True", "False"):
                rates += 1
                flips += cells[2] != cells[3]
        out.append(("%s->%s" % (src, dst), flips, rates))
    return out


def make_offered_load_figure(plt) -> None:
    counts = _offered_load_flip_counts()
    if not counts:
        return
    pairs = [p.replace("->", "→") for p, _, _ in counts]
    flips = [f for _, f, _ in counts]
    rate_counts = {r for _, _, r in counts}
    n_rates = max(rate_counts)
    fig, ax = plt.subplots(figsize=(9, 4.6))
    colors = ["#1baf7a" if f > 0 else "#94a3ab" for f in flips]
    bars = ax.bar(pairs, flips, color=colors, alpha=0.85)
    ax.bar_label(bars, labels=[f"{f} / {r}" for _, f, r in counts])
    ax.set_ylim(0, n_rates + 1)
    ax.set_xlabel("monitored node pair")
    ax.set_ylabel("decision flips across the rate sweep")
    fig.tight_layout()
    _save(fig, "offered_load_recovery")


def main() -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams.update({
        "font.family": "DejaVu Sans",
        "font.size": 12.5,
        "axes.titlesize": 14,
        "axes.labelsize": 13,
        "legend.fontsize": 11,
        "xtick.labelsize": 11.5,
        "ytick.labelsize": 11.5,
        "axes.edgecolor": "#c3c2b7",
        "axes.grid": True,
        "grid.color": "#e6e5df",
        "grid.linewidth": 0.6,
        "figure.facecolor": "white",
        "axes.facecolor": "white",
        "savefig.bbox": "tight",
        "pdf.fonttype": 42,   # embed TrueType, not Type-3, for camera-ready PDFs
    })

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    make_congestion_figure(plt)
    make_weight_search_figure(plt)
    make_failure_recovery_figure(plt)
    make_failure_recovery_reroute_count_figure(plt)
    make_increasing_load_figure(plt)
    make_stale_stats_figure(plt)
    make_priority_policy_figure(plt)
    make_resilience_figure(plt)
    make_resilience_avoidance_figure(plt)
    make_vif_figure(plt)
    make_offered_load_figure(plt)


if __name__ == "__main__":
    main()
