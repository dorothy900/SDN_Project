#!/usr/bin/env python3
"""
Hybrid Congestion-Churn Matrix - real DecisionEngine-driven churn combined
with real Mininet-measured delay/loss, instead of congestion_model.py's
formula, so a joint (u, delay_residual, loss_residual, churn) dataset has
genuine, non-degenerate variance in *every* column.

Why this exists: neither of this project's two prior joint-data sources is
enough on its own to build a composite congestion indicator across all 4
variables (see compliance_check.md's PCA section, 2026-08-19):
  - Real Mininet traffic (results/independence_check/,
    results/loss_saturation_check/) has real u/delay/loss with genuine
    residual structure, but no DecisionEngine running -- no churn.
  - decision_churn_independence.py / joint_independence_matrix.py have real
    churn (a real DecisionEngine reacting to injected conditions), but
    their delay/loss come from set_link_condition's DEFAULT path, which
    computes delay/loss *exactly* from congestion_model.py's curve --
    delay_residual/loss_residual are therefore ~0 by construction there,
    carrying essentially no real variance for a PCA to work with (confirmed
    live: loss_residual's std was 0.00006 in that data, and a PCA fit on it
    produced a numerically meaningless loading).

Fix: same campaign/event structure as joint_independence_matrix.py
(including its 2026-08-19 disjoint-first-link fix -- see that module for
why sharing a link across campaigns corrupts the recorded data), but
delay/loss are injected via set_link_condition's delay_ms_override/
loss_override using the *real* Mininet sample whose achieved_utilization is
closest to the target -- not the formula. delay_residual/loss_residual
computed downstream against congestion_model's curve therefore reflect
genuine measurement variance, the same kind characterized all session via
LOESS/Breusch-Pagan on the real Mininet data itself.
"""
from __future__ import annotations

import csv
import random
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np

from .independence_stats import permutation_test, spearman_rho, variance_inflation_factors
from .simulation_common import (
    SAMPLE_INTERVAL_S,
    build_network_state,
    link_id,
    make_drivers,
    set_link_condition,
)
from src.routing.congestion_model import predicted_delay_ms, predicted_loss
from src.routing.graph_builder import GraphBuilder

RANDOM_SEED = 42
NUM_PAIRS = 6
EVENTS_PER_PAIR = 15
# Randomized ranges, not fixed constants (2026-08-19): a fixed target
# utilization makes nearest_real_sample() return the exact same real
# (delay, loss) pair every time that event type fires, collapsing
# delay_residual/loss_residual to ~2 distinct points across the whole
# dataset -- any correlation between them is then a trivial "line through
# 2 points" artifact (found live: -1.000 Spearman), not a real relationship.
CONGESTED_UTILIZATION_RANGE = (0.55, 0.85)
BASELINE_UTILIZATION_RANGE = (0.10, 0.40)
RECOVERY_WINDOW_BUFFER_S = 20.0
EVENT_TYPES = ["congest", "relieve", "fail", "recover", "quiet"]

VARIABLES = ["utilization", "delay_residual", "loss_residual", "churn_score"]

REAL_SAMPLE_SOURCES = [
    Path("results/independence_check/independence_samples.csv"),
    Path("results/loss_saturation_check/loss_samples.csv"),
]


def load_real_samples() -> List[Tuple[float, float, float]]:
    """(achieved_utilization, delay_ms, loss) triples pooled from every real
    Mininet run collected this session -- used as a nearest-neighbor lookup
    table below, not fit to any curve."""
    samples: List[Tuple[float, float, float]] = []
    for path in REAL_SAMPLE_SOURCES:
        if not path.exists():
            continue
        for row in csv.DictReader(path.open()):
            u = float(row["achieved_utilization"])
            delay = row.get("delay_ms")
            loss = row.get("loss")
            if delay in (None, "", "None") or loss in (None, "", "None"):
                continue
            samples.append((u, float(delay), float(loss)))
    if not samples:
        raise RuntimeError(
            "No real samples found -- run scripts/mininet_independence_check.py "
            "and scripts/mininet_loss_saturation_check.py first."
        )
    return samples


def nearest_real_sample(target_u: float, samples: List[Tuple[float, float, float]]) -> Tuple[float, float]:
    """(delay_ms, loss) from whichever real sample's achieved_utilization is
    closest to target_u -- nearest-neighbor lookup, not interpolation."""
    _, delay_ms, loss = min(samples, key=lambda s: abs(s[0] - target_u))
    return delay_ms, loss


def run(output_dir: Path = Path("results/hybrid_congestion_churn_matrix")) -> Dict[str, object]:
    output_dir.mkdir(parents=True, exist_ok=True)
    real_samples = load_real_samples()
    print(f"*** Loaded {len(real_samples)} real (u, delay, loss) samples for lookup")

    state = build_network_state(output_dir, seed=RANDOM_SEED)
    builder = GraphBuilder(state)
    # Over-fetch + de-dupe by first link -- same fix as joint_independence_matrix.py
    # (2026-08-19): campaigns sharing a real link corrupt each other's
    # recorded state since they all write to one NetworkState in one
    # globally-shuffled event order.
    candidate_pairs = builder.select_test_pairs(limit=NUM_PAIRS * 8, min_candidate_paths=2)

    rng = random.Random(RANDOM_SEED)
    campaigns = []
    used_links: set = set()
    for src, dst in candidate_pairs:
        if len(campaigns) >= NUM_PAIRS:
            break
        drivers = make_drivers(state, src, dst, threshold=0.7, persistence_required_samples=1)
        driver = drivers["proposed"]
        if not driver.path or len(driver.path) < 2:
            continue
        first_link = link_id(driver.path[0], driver.path[1])
        if first_link in used_links:
            continue
        used_links.add(first_link)
        schedule = [rng.choice(EVENT_TYPES) for _ in range(EVENTS_PER_PAIR)]
        campaigns.append({
            "src": src, "dst": dst, "driver": driver, "link": first_link,
            "schedule": schedule, "now_s": 0.0, "failed": False,
        })

    plan = [
        (c_index, event)
        for c_index, campaign in enumerate(campaigns)
        for event in campaign["schedule"]
    ]
    rng.shuffle(plan)

    def inject(link: str, target_u: float, now_s: float) -> None:
        delay_ms, loss = nearest_real_sample(target_u, real_samples)
        set_link_condition(
            state, link, utilization=target_u,
            delay_ms_override=delay_ms, loss_override=loss, now=now_s,
        )

    rows: List[Dict[str, object]] = []
    for c_index, event in plan:
        campaign = campaigns[c_index]
        driver, link = campaign["driver"], campaign["link"]
        campaign["now_s"] += SAMPLE_INTERVAL_S

        if event == "congest" and not campaign["failed"]:
            target_u = rng.uniform(*CONGESTED_UTILIZATION_RANGE)
            inject(link, target_u, campaign["now_s"])
            driver.step(now_s=campaign["now_s"], hotspot_link=link, hotspot_utilization=target_u)
        elif event == "relieve" and not campaign["failed"]:
            target_u = rng.uniform(*BASELINE_UTILIZATION_RANGE)
            inject(link, target_u, campaign["now_s"])
            driver.step(now_s=campaign["now_s"], hotspot_link=link, hotspot_utilization=target_u)
        elif event == "fail" and not campaign["failed"]:
            set_link_condition(state, link, status="down", now=campaign["now_s"])
            driver.on_link_failure(link, campaign["now_s"])
            campaign["failed"] = True
        elif event == "recover" and campaign["failed"]:
            target_u = rng.uniform(*BASELINE_UTILIZATION_RANGE)
            set_link_condition(state, link, status="up", now=campaign["now_s"])
            inject(link, target_u, campaign["now_s"])
            driver.on_link_recovered(link, campaign["now_s"])
            campaign["now_s"] += RECOVERY_WINDOW_BUFFER_S
            driver.step(now_s=campaign["now_s"])
            campaign["failed"] = False
        else:
            driver.step(now_s=campaign["now_s"])

        stats = state.get_link_stats(link)
        u = float(stats.utilization) if stats else None
        delay = float(stats.delay_ms) if stats and stats.delay_ms is not None else None
        loss = float(stats.packet_loss) if stats and stats.packet_loss is not None else None
        rows.append({
            "pair": f"{campaign['src']}->{campaign['dst']}",
            "link": link,
            "event": event,
            "utilization": u,
            "delay_ms": delay,
            "loss": loss,
            "delay_residual": (delay - predicted_delay_ms(u)) if (u is not None and delay is not None) else None,
            "loss_residual": (loss - predicted_loss(u)) if (u is not None and loss is not None) else None,
            "churn_score": state.get_link_churn_score(link, now=campaign["now_s"]),
            "reliability_down": 0 if (stats is None or stats.status == "up") else 1,
        })

    # Exclude down-link samples: delay/loss aren't meaningful while a link
    # carries no traffic, so delay_residual/loss_residual during a "fail"
    # event are stale leftovers from before the failure, not real
    # measurements. Found live: including them made delay_residual vs
    # reliability_down spuriously hit rho=0.872 (VIF>3000) -- an artifact of
    # frozen values coinciding with status="down", not a real relationship.
    clean_rows = [
        r for r in rows
        if all(r[v] is not None for v in VARIABLES) and r["reliability_down"] == 0
    ]
    data = {v: [float(r[v]) for r in clean_rows] for v in VARIABLES}
    n = len(clean_rows)
    print(f"*** {n} usable samples across {len(campaigns)} campaigns (down-link samples excluded)")

    with (output_dir / "hybrid_samples.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    # --- correlation matrix + VIF, same pattern as joint_independence_matrix.py ---
    matrix_lines = ["## Spearman correlation matrix", ""]
    header = "| |" + "|".join(VARIABLES) + "|"
    sep = "|---|" + "|".join(["---"] * len(VARIABLES)) + "|"
    matrix_lines += [header, sep]
    rho_matrix: Dict[str, Dict[str, float]] = {}
    for vi in VARIABLES:
        row_cells = [vi]
        rho_matrix[vi] = {}
        for vj in VARIABLES:
            if vi == vj:
                rho_matrix[vi][vj] = 1.0
                row_cells.append("1.000")
            else:
                rho, p = permutation_test(data[vi], data[vj], statistic_fn=spearman_rho, n_permutations=4999, seed=RANDOM_SEED)
                rho_matrix[vi][vj] = rho
                row_cells.append(f"{rho:.3f}{'*' if p < 0.05 else ''}")
        matrix_lines.append("| " + " | ".join(row_cells) + " |")

    vifs = variance_inflation_factors(data)

    # --- PCA on the 4 congestion-chain variables (u, delay_residual, loss_residual, churn) ---
    pca_vars = ["utilization", "delay_residual", "loss_residual", "churn_score"]
    X = np.column_stack([data[v] for v in pca_vars])
    mean = X.mean(axis=0)
    std = X.std(axis=0, ddof=1)
    Xz = (X - mean) / std
    cov = np.cov(Xz, rowvar=False)
    eigvals, eigvecs = np.linalg.eigh(cov)
    order = np.argsort(eigvals)[::-1]
    eigvals, eigvecs = eigvals[order], eigvecs[:, order]
    pc1 = eigvecs[:, 0]
    if pc1[pca_vars.index("utilization")] < 0:
        pc1 = -pc1
    variance_explained = eigvals[0] / eigvals.sum()

    report_lines = [
        "# Hybrid Congestion-Churn Matrix (real delay/loss injection + real DecisionEngine churn)",
        "",
        f"Samples: {n} ({len(campaigns)} campaigns x {EVENTS_PER_PAIR} events, disjoint first links)",
        "",
        "delay_ms/loss injected via nearest-neighbor lookup into real Mininet samples "
        "(results/independence_check/, results/loss_saturation_check/) instead of "
        "congestion_model.py's formula -- delay_residual/loss_residual below carry genuine "
        "measurement variance, not near-zero by-construction values.",
        "",
    ] + matrix_lines + [
        "",
        "(* = p<0.05 under a 4999-permutation test)",
        "",
        "## VIF (joint collinearity)",
        "",
    ]
    for name, vif in vifs.items():
        report_lines.append(f"- VIF({name}) = {vif:.3f}")

    report_lines += [
        "",
        "## PCA on (utilization, delay_residual, loss_residual, churn_score)",
        "",
        f"PC1 explains {100*variance_explained:.1f}% of variance",
        "",
        "PC1 loadings (oriented so higher utilization -> higher score):",
        "",
    ]
    for var, loading in zip(pca_vars, pc1):
        report_lines.append(f"- {var}: {loading:+.4f}")

    report_text = "\n".join(report_lines) + "\n"
    (output_dir / "hybrid_report.md").write_text(report_text, encoding="utf-8")
    print(report_text)
    return {
        "n": n, "rho_matrix": rho_matrix, "vifs": vifs,
        "pca_vars": pca_vars, "pc1": pc1.tolist(),
        "pca_mean": mean.tolist(), "pca_std": std.tolist(),
        "variance_explained": float(variance_explained),
    }


if __name__ == "__main__":
    run()
