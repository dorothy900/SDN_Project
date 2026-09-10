#!/usr/bin/env python3
"""
Decision Churn Independence - tests whether delta (link instability/churn)
and epsilon (reliability) are independent of each other and of utilization,
driving several (src, dst) pairs through randomized congestion/failure/
recovery/quiet events via the real ProposedDriver, offline (not Mininet:
delta/epsilon are properties of the decision system's own bookkeeping, not
physical measurements), then running the same independence toolkit used for
u/delay/loss (experiments/independence_stats.py).
"""
from __future__ import annotations

import csv
import random
from pathlib import Path
from typing import Dict, List

from experiments.common.independence_stats import (
    distance_correlation,
    permutation_test,
    spearman_rho,
    variance_inflation_factors,
)
from experiments.common.simulation_common import (
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
RECOVERY_WINDOW_BUFFER_S = 20.0  # comfortably exceeds RecoveryManager's default 5s window
EVENT_TYPES = ["congest", "relieve", "fail", "recover", "quiet"]


def run(output_dir: Path = Path("results/decision_churn_independence")) -> Dict[str, object]:
    output_dir.mkdir(parents=True, exist_ok=True)
    state = build_network_state(output_dir, seed=RANDOM_SEED)
    builder = GraphBuilder(state)
    # Over-fetch and de-dupe by first link -- see joint_independence_matrix.py's
    # matching comment: campaigns sharing a real link corrupt each other's
    # recorded (utilization, delay) pairing, since they all write to the
    # same NetworkState in one globally-shuffled event order.
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

    # Interleave every campaign's events into one globally randomized order.
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
            "churn_score": state.get_link_churn_score(link, now=campaign["now_s"]),
            "reliability_down": 0 if (stats is None or stats.status == "up") else 1,
        })

    with (output_dir / "decision_churn_samples.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    u_vals = [r["utilization"] for r in rows]
    churn_vals = [r["churn_score"] for r in rows]
    down_vals = [float(r["reliability_down"]) for r in rows]

    report_lines = [
        "# Decision Churn Independence Check (delta vs epsilon vs utilization)",
        "",
        f"Samples: {len(rows)} across {len(campaigns)} pairs x {EVENTS_PER_PAIR} events "
        f"(offline, driven through the real DecisionEngine/ProposedDriver, no Mininet)",
        "",
        "## Pairwise monotonic dependence (Spearman + permutation test)",
        "",
    ]
    pairs_to_test = [
        ("utilization vs churn_score", u_vals, churn_vals),
        ("utilization vs reliability_down", u_vals, down_vals),
        ("churn_score vs reliability_down", churn_vals, down_vals),
    ]
    for name, x, y in pairs_to_test:
        rho, p = permutation_test(x, y, statistic_fn=spearman_rho, n_permutations=9999, seed=RANDOM_SEED)
        report_lines.append(f"- {name}: rho={rho:.4f}, p={p:.4f}")

    report_lines += ["", "## Pairwise dependence beyond monotonic (distance correlation)", ""]
    for name, x, y in pairs_to_test:
        report_lines.append(f"- {name}: dCor={distance_correlation(x, y):.4f}")

    report_lines += ["", "## Joint collinearity (VIF)", ""]
    vifs = variance_inflation_factors({"utilization": u_vals, "churn_score": churn_vals, "reliability_down": down_vals})
    for name, vif in vifs.items():
        report_lines.append(f"- VIF({name}) = {vif:.3f}")

    report_lines += [
        "",
        "## Caveat",
        "",
        "Samples within one pair's campaign track the same link across time (churn is "
        "inherently about a link's own recent history), so they are not fully independent "
        "draws -- the global event order is randomized across all campaigns, but this does "
        "not eliminate within-campaign temporal structure.",
    ]

    report_text = "\n".join(report_lines) + "\n"
    (output_dir / "decision_churn_report.md").write_text(report_text, encoding="utf-8")
    print(report_text)
    return {"rows": rows, "report_path": str(output_dir / "decision_churn_report.md")}


if __name__ == "__main__":
    run()
