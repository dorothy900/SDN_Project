#!/usr/bin/env python3
"""
Joint Independence Matrix - full correlation matrix + VIF across all 5
cost-formula variables (utilization, delay, loss, delta/churn,
epsilon/reliability) from ONE unified offline experiment, instead of
testing pairs one at a time across separate experiments
(mininet_independence_check.py for u/delay/loss,
decision_churn_independence.py for delta/epsilon).

Honesty note, stated up front: utilization/delay/loss here are generated
together via congestion_model.py's real-data-fitted curves
(set_link_condition's default path), not measured on real Mininet traffic.
This means u-delay and u-loss in this matrix are correlated *by
construction* -- confirming the formula's internal consistency, not new
empirical evidence. Real empirical evidence for those two specific pairs
already exists from real Mininet traffic (results/independence_check/,
results/loss_saturation_check/). What this analysis newly tests are the
pairs involving delta/epsilon, which are NOT mechanically pre-determined by
the generation formula -- churn and reliability come from how the real
DecisionEngine actually reacts to the injected conditions, not from a fixed
function of utilization.

Method: same randomized congestion/failure/recovery/quiet event campaigns as
decision_churn_independence.py (6 real GEANT pairs, real ProposedDriver/
DecisionEngine), but each sample now also records the link's delay_ms/
packet_loss (set alongside utilization by set_link_condition), not just
churn/reliability. One Spearman correlation matrix computed once over all
5x5 pairs, plus VIF computed once per variable (5 values) for joint (not
just pairwise) collinearity.
"""
from __future__ import annotations

import csv
import random
from pathlib import Path
from typing import Dict, List

from .independence_stats import permutation_test, spearman_rho, variance_inflation_factors
from .simulation_common import (
    SAMPLE_INTERVAL_S,
    build_network_state,
    link_id,
    make_drivers,
    set_link_condition,
)
from src.routing.graph_builder import GraphBuilder

RANDOM_SEED = 42
NUM_PAIRS = 6
EVENTS_PER_PAIR = 15
CONGESTED_UTILIZATION = 0.85
BASELINE_UTILIZATION = 0.3
RECOVERY_WINDOW_BUFFER_S = 20.0
EVENT_TYPES = ["congest", "relieve", "fail", "recover", "quiet"]

VARIABLES = ["utilization", "delay_ms", "loss", "churn_score", "reliability_down"]


def run(output_dir: Path = Path("results/joint_independence_matrix")) -> Dict[str, object]:
    output_dir.mkdir(parents=True, exist_ok=True)
    state = build_network_state(output_dir, seed=RANDOM_SEED)
    builder = GraphBuilder(state)
    pairs = builder.select_test_pairs(limit=NUM_PAIRS, min_candidate_paths=2)

    rng = random.Random(RANDOM_SEED)
    campaigns = []
    for src, dst in pairs:
        drivers = make_drivers(state, src, dst, threshold=0.7, persistence_required_samples=1)
        driver = drivers["proposed"]
        if not driver.path or len(driver.path) < 2:
            continue
        first_link = link_id(driver.path[0], driver.path[1])
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

    rows: List[Dict[str, object]] = []
    for c_index, event in plan:
        campaign = campaigns[c_index]
        driver, link = campaign["driver"], campaign["link"]
        campaign["now_s"] += SAMPLE_INTERVAL_S

        if event == "congest" and not campaign["failed"]:
            set_link_condition(state, link, utilization=CONGESTED_UTILIZATION, now=campaign["now_s"])
            driver.step(now_s=campaign["now_s"], hotspot_link=link, hotspot_utilization=CONGESTED_UTILIZATION)
        elif event == "relieve" and not campaign["failed"]:
            set_link_condition(state, link, utilization=BASELINE_UTILIZATION, now=campaign["now_s"])
            driver.step(now_s=campaign["now_s"], hotspot_link=link, hotspot_utilization=BASELINE_UTILIZATION)
        elif event == "fail" and not campaign["failed"]:
            set_link_condition(state, link, status="down", now=campaign["now_s"])
            driver.on_link_failure(link, campaign["now_s"])
            campaign["failed"] = True
        elif event == "recover" and campaign["failed"]:
            set_link_condition(state, link, status="up", utilization=BASELINE_UTILIZATION, now=campaign["now_s"])
            driver.on_link_recovered(link, campaign["now_s"])
            campaign["now_s"] += RECOVERY_WINDOW_BUFFER_S
            driver.step(now_s=campaign["now_s"])
            campaign["failed"] = False
        else:
            driver.step(now_s=campaign["now_s"])

        stats = state.get_link_stats(link)
        rows.append({
            "pair": f"{campaign['src']}->{campaign['dst']}",
            "link": link,
            "event": event,
            "utilization": float(stats.utilization) if stats else None,
            "delay_ms": float(stats.delay_ms) if stats and stats.delay_ms is not None else None,
            "loss": float(stats.packet_loss) if stats and stats.packet_loss is not None else None,
            "churn_score": state.get_link_churn_score(link, now=campaign["now_s"]),
            "reliability_down": 0 if (stats is None or stats.status == "up") else 1,
        })

    clean_rows = [r for r in rows if all(r[v] is not None for v in VARIABLES)]
    data = {v: [float(r[v]) for r in clean_rows] for v in VARIABLES}
    n = len(clean_rows)

    with (output_dir / "joint_samples.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    # --- ONE correlation matrix, all pairs computed together ---
    matrix_lines = ["## Spearman correlation matrix (computed once)", ""]
    header = "| |" + "|".join(VARIABLES) + "|"
    sep = "|---|" + "|".join(["---"] * len(VARIABLES)) + "|"
    matrix_lines += [header, sep]
    rho_matrix: Dict[str, Dict[str, float]] = {}
    p_matrix: Dict[str, Dict[str, float]] = {}
    for vi in VARIABLES:
        row_cells = [vi]
        rho_matrix[vi] = {}
        p_matrix[vi] = {}
        for vj in VARIABLES:
            if vi == vj:
                rho_matrix[vi][vj] = 1.0
                row_cells.append("1.000")
            else:
                rho, p = permutation_test(data[vi], data[vj], statistic_fn=spearman_rho, n_permutations=4999, seed=RANDOM_SEED)
                rho_matrix[vi][vj] = rho
                p_matrix[vi][vj] = p
                marker = "*" if p < 0.05 else ""
                row_cells.append(f"{rho:.3f}{marker}")
        matrix_lines.append("| " + " | ".join(row_cells) + " |")

    # --- VIF, once per variable (5 total) ---
    vifs = variance_inflation_factors(data)

    report_lines = [
        "# Joint Independence Matrix (single unified experiment)",
        "",
        f"Samples: {n} (offline, real ProposedDriver/DecisionEngine, {len(campaigns)} pairs x {EVENTS_PER_PAIR} events)",
        "",
        "**Caveat:** utilization/delay/loss are generated together via congestion_model.py's "
        "fitted curve (not measured on real Mininet traffic here), so utilization-delay and "
        "utilization-loss are correlated *by construction* in this matrix -- real empirical "
        "evidence for those two pairs already exists separately (results/independence_check/, "
        "results/loss_saturation_check/). The new information here is delta/epsilon's "
        "relationships, which are not mechanically fixed by the generation formula.",
        "",
    ] + matrix_lines + [
        "",
        "(* = p<0.05 under a 4999-permutation test; matrix is symmetric, only computed once per pair)",
        "",
        "## VIF (joint collinearity, one value per variable)",
        "",
    ]
    for name, vif in vifs.items():
        report_lines.append(f"- VIF({name}) = {vif:.3f}")

    report_text = "\n".join(report_lines) + "\n"
    (output_dir / "joint_report.md").write_text(report_text, encoding="utf-8")
    print(report_text)
    return {"rho_matrix": rho_matrix, "p_matrix": p_matrix, "vifs": vifs, "n": n}


if __name__ == "__main__":
    run()
