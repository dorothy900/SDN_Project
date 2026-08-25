#!/usr/bin/env python3
"""
Make Figures - commits the dissertation-cited figures this session's
scenario design audit found were never actually saved as reproducible code
(finding F1): compliance_check.md's "Publication-style figures added
(2026-08-24)" section describes box plots, a hop-count/improvement scatter
with an OLS trend line, and delay-vs-sample curves as already built -- but
no plotting script or image file for any of them existed anywhere in this
repository or its git history (confirmed: `find` for *.png/*.svg/*.pdf and
`git log --all --diff-filter=A` both came back empty for anything but an
unrelated weight_search_comparison.py chart).

All five figures below were independently reconstructed during that audit
straight from CSVs already sitting in results/ -- nothing here is new data,
only the plotting code that was missing. Run this after the relevant
*_generalization.py / *_persample.py scripts to refresh the PNGs from
current data.

Deliberately does NOT cover congestion.py, which was out of scope for that
audit (excluded by request) but has the identical gap -- its own richer
figure (sustained + chronic-intermittent panels, a churn-vs-delay scatter
across the 4 objectively-selected pairs) has the same "real numbers, no
committed script" problem and needs the same treatment once
congestion.py's own generalization data is available in a comparable
per-pair/per-seed CSV form.

Run as: python3 -m experiments.make_figures
Writes PNGs to results/figures/.
"""
from __future__ import annotations

import csv
import random
import statistics as st
from pathlib import Path
from typing import Dict, List

OUTPUT_DIR = Path("results/figures")
ALGO_COLOR = {"static": "tab:gray", "dynamic": "tab:blue", "proposed": "tab:green"}


def _load_csv(path: Path) -> List[Dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _band(ax, x, mean, std, color, label) -> None:
    ax.plot(x, mean, label=label, color=color, linewidth=2)
    ax.fill_between(
        x, [m - s for m, s in zip(mean, std)], [m + s for m, s in zip(mean, std)],
        color=color, alpha=0.15,
    )


def fig_increasing_load_delay_vs_load(ax) -> None:
    rows = _load_csv(Path("results/increasing_load_generalization/persample.csv"))
    load = [float(r["load_factor"]) for r in rows]
    for algo in ("static", "dynamic", "proposed"):
        mean = [float(r[f"{algo}_mean"]) for r in rows]
        std = [float(r[f"{algo}_std"]) for r in rows]
        _band(ax, load, mean, std, ALGO_COLOR[algo], algo)
    ax.set_xlabel("offered load (fraction of link capacity)")
    ax.set_ylabel("mean delay (ms)")
    ax.set_title("increasing_load: delay vs offered load\n(23 pairs x 5 seeds, mean +/- 1 s.d.)")
    ax.legend()


def fig_failure_recovery_boxplot(ax) -> None:
    rows = _load_csv(Path("results/failure_recovery_generalization/summary.csv"))
    data, tick_labels, colors = [], [], []
    for case in ("stable", "unstable"):
        for algo in ("static", "dynamic", "proposed"):
            data.append([float(r[f"{case}_{algo}_delay_ms"]) for r in rows])
            tick_labels.append(f"{case}\n{algo}")
            colors.append(ALGO_COLOR[algo])
    bp = ax.boxplot(data, tick_labels=tick_labels, patch_artist=True)
    for patch, color in zip(bp["boxes"], colors):
        patch.set_facecolor(color)
        patch.set_alpha(0.5)
    ax.set_ylabel("post-failure delay (ms)")
    ax.set_title("failure_recovery: delay distribution across 23 pairs\n(each box: n=23 pair-means, 5 seeds/pair)")


def fig_hop_vs_improvement(ax) -> None:
    rows = _load_csv(Path("results/failure_recovery_generalization/summary.csv"))
    hops = [int(r["hops"]) for r in rows]
    dyn = [float(r["stable_dynamic_delay_ms"]) for r in rows]
    prop = [float(r["stable_proposed_delay_ms"]) for r in rows]
    improvement = [(d - p) / d * 100 for d, p in zip(dyn, prop)]

    rng = random.Random(3)
    jittered_x = [h + rng.uniform(-0.08, 0.08) for h in hops]
    ax.scatter(jittered_x, improvement, color="tab:blue", alpha=0.7, zorder=3)

    n = len(hops)
    mean_x, mean_y = sum(hops) / n, sum(improvement) / n
    sxy = sum((x - mean_x) * (y - mean_y) for x, y in zip(hops, improvement))
    sxx = sum((x - mean_x) ** 2 for x in hops)
    slope = sxy / sxx
    intercept = mean_y - slope * mean_x
    r = sxy / ((sxx ** 0.5) * (sum((y - mean_y) ** 2 for y in improvement) ** 0.5))
    x_line = [min(hops), max(hops)]
    ax.plot(
        x_line, [slope * x + intercept for x in x_line], color="tab:red", linestyle="--",
        label=f"OLS: slope={slope:.2f}pp/hop, r={r:.2f}",
    )
    ax.set_xlabel("hop count")
    ax.set_ylabel("proposed's delay improvement over dynamic (%)")
    ax.set_title("failure_recovery (stable case): improvement vs hop count\n(23 pairs)")
    ax.legend()


def fig_priority_policy_strip(ax) -> None:
    rows = _load_csv(Path("results/priority_policy_generalization/summary.csv"))
    classes = [
        ("voip_mean_sample", "VoIP", "tab:blue"),
        ("video_mean_sample", "Video", "tab:blue"),
        ("web_mean_sample", "Web", "tab:orange"),
        ("file_transfer_mean_sample", "File Transfer", "tab:red"),
    ]
    rng = random.Random(7)
    for i, (key, _, color) in enumerate(classes):
        vals = [float(r[key]) for r in rows]
        xs = [i + rng.uniform(-0.15, 0.15) for _ in vals]
        ax.scatter(xs, vals, color=color, alpha=0.6, zorder=3)
        ax.hlines(st.median(vals), i - 0.22, i + 0.22, color=color, linewidth=2.5, zorder=4)
    ax.set_xticks(range(len(classes)))
    ax.set_xticklabels([label for _, label, _ in classes])
    ax.set_ylabel("first-reroute sample")
    ax.set_title("priority_policy: first-reroute sample by class\n(23 pairs, dot=pair, bar=median)")


def fig_stale_stats_phase(ax, csv_path: Path, title: str) -> None:
    rows = _load_csv(csv_path)
    samples = [int(r["sample"]) for r in rows]
    for algo in ("static", "dynamic", "proposed"):
        mean = [float(r[f"{algo}_mean"]) for r in rows]
        std = [float(r[f"{algo}_std"]) for r in rows]
        _band(ax, samples, mean, std, ALGO_COLOR[algo], algo)
    ax.set_xlabel("sample")
    ax.set_ylabel("delay (ms)")
    ax.set_title(title)
    ax.legend()


def main() -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    single_axis_specs = [
        ("increasing_load_delay_vs_load.png", fig_increasing_load_delay_vs_load, (8, 5)),
        ("failure_recovery_boxplot.png", fig_failure_recovery_boxplot, (9, 5)),
        ("failure_recovery_hop_vs_improvement.png", fig_hop_vs_improvement, (7, 5)),
        ("priority_policy_first_reroute_strip.png", fig_priority_policy_strip, (7, 5)),
    ]
    for filename, plot_fn, figsize in single_axis_specs:
        fig, ax = plt.subplots(figsize=figsize)
        plot_fn(ax)
        fig.tight_layout()
        out_path = OUTPUT_DIR / filename
        fig.savefig(out_path, dpi=150)
        plt.close(fig)
        print("Wrote", out_path)

    stale_stats_specs = [
        ("stale_stats_noise_phase.png", "persample_noise.csv", "stale_stats: noise phase, delay vs sample"),
        (
            "stale_stats_delayed_detection_phase.png", "persample_delayed_detection.csv",
            "stale_stats: delayed-detection phase, delay vs sample",
        ),
    ]
    for filename, csv_name, title in stale_stats_specs:
        fig, ax = plt.subplots(figsize=(8, 5))
        fig_stale_stats_phase(ax, Path("results/stale_stats_generalization") / csv_name, title)
        fig.tight_layout()
        out_path = OUTPUT_DIR / filename
        fig.savefig(out_path, dpi=150)
        plt.close(fig)
        print("Wrote", out_path)


if __name__ == "__main__":
    main()
