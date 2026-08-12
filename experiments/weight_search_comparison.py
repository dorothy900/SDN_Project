#!/usr/bin/env python3
"""
Weight Search Comparison - grid search vs Bayesian optimization for
path_cost_weights (alpha/beta/gamma), evaluated against a ground-truth
regret objective, not the isolated cost/path-selection checks in
sensitivity_analysis.py.

The objective: assume a "ground truth" relative importance of utilization/
delay/loss exists (equal weight, 1/3 each -- an explicit modeling choice,
not derived from anything; a different real deployment could reasonably
pick a different ground truth) that is independent of the (alpha, beta,
gamma) being searched. Those three numbers are the *decision-maker's* belief
about relative importance, which may not match the ground truth. For a
given (alpha, beta, gamma), the decision-maker picks whichever of the three
contrasting COST_PATH_* paths (see sensitivity_analysis.py for their exact
construction) it believes is cheapest; regret is how much worse that choice
is than the actual best path, measured under the ground-truth weights:

    regret(alpha, beta, gamma) = GT(path the decision-maker picked) - min(GT(A), GT(B), GT(C))

This is 0 when the decision-maker's weights happen to agree with the ground
truth about which path is best, and positive otherwise (bounded, piecewise
-constant with real jumps at the same flip boundaries
sensitivity_analysis.py's Sweep C found -- this is the same underlying
decision surface, just scored against an external reference instead of
"which path is cheapest under its own weights").

Two searches are run against the identical objective, same evaluation
budget, so the comparison is fair:
  - Grid search: a fixed, pre-chosen set of points (same style as
    sensitivity_analysis.py).
  - Bayesian optimization: a from-scratch Gaussian Process (RBF kernel) +
    Expected Improvement loop, implemented in plain numpy (no scipy/sklearn
    available in this environment) -- proposes each next point based on
    what all previous evaluations suggest, rather than a fixed pre-chosen
    set.

Run as: python3 -m experiments.weight_search_comparison
"""
from __future__ import annotations

import csv
import json
import math
import random
from pathlib import Path
from typing import Callable, Dict, List, Tuple

import numpy as np

from .sensitivity_analysis import (
    COST_PATH_A,
    COST_PATH_B,
    COST_PATH_C,
    SensitivityAnalysis,
)
from src.routing.graph_builder import GraphBuilder

GROUND_TRUTH_WEIGHTS = {"alpha": 1 / 3, "beta": 1 / 3, "gamma": 1 / 3, "delta": 0.0, "epsilon": 0.0}
BOUNDS = {"alpha": (0.0, 1.0), "beta": (0.0, 1.0), "gamma": (0.0, 1.0)}
PARAM_ORDER = ["alpha", "beta", "gamma"]

GRID_BUDGET_PER_DIM = 6  # 6^3 = 216 evaluations, matched by BO's iteration count below
BO_INIT_POINTS = 8
BO_ITERATIONS = 208  # + BO_INIT_POINTS = 216, same total budget as the grid


def make_objective() -> Tuple[Callable[..., float], Dict[str, float]]:
    """
    Build the regret objective against a fixed contrasting-paths state.

    Returns regret(alpha, beta, gamma, delta=0.05, epsilon=0.05) -- delta/
    epsilon default to the project's current values so existing call sites
    (grid search, BO) that only pass alpha/beta/gamma are unaffected, while
    the OAT sensitivity sweep below can still pass all five explicitly.
    """
    sa = SensitivityAnalysis(output_dir=Path("results/pilot/sensitivity"))
    state = sa._build_contrast_state(seed=0)

    gt_builder = GraphBuilder(state, weights=GROUND_TRUTH_WEIGHTS)
    gt_graph = gt_builder.build_weighted_graph()
    gt_costs = {
        "A": gt_builder.get_path_cost(COST_PATH_A, gt_graph),
        "B": gt_builder.get_path_cost(COST_PATH_B, gt_graph),
        "C": gt_builder.get_path_cost(COST_PATH_C, gt_graph),
    }
    best_gt = min(gt_costs.values())

    def regret(alpha: float, beta: float, gamma: float, delta: float = 0.05, epsilon: float = 0.05) -> float:
        weights = {"alpha": alpha, "beta": beta, "gamma": gamma, "delta": delta, "epsilon": epsilon}
        builder = GraphBuilder(state, weights=weights)
        graph = builder.build_weighted_graph()
        costs = {
            "A": builder.get_path_cost(COST_PATH_A, graph),
            "B": builder.get_path_cost(COST_PATH_B, graph),
            "C": builder.get_path_cost(COST_PATH_C, graph),
        }
        chosen = min(costs, key=costs.get)
        return gt_costs[chosen] - best_gt

    return regret, gt_costs


CURRENT_DEFAULTS = {"alpha": 0.4, "beta": 0.3, "gamma": 0.2, "delta": 0.05, "epsilon": 0.05}
OAT_SENSITIVITY_GRID = {
    "alpha": [0.0, 0.02, 0.05, 0.1, 0.2, 0.4, 0.7, 1.0],
    "beta": [0.0, 0.1, 0.3, 0.6, 1.0, 1.5, 2.0],
    "gamma": [0.0, 0.1, 0.2, 0.3, 0.5, 0.7, 0.9, 1.2],
    "delta": [0.0, 0.2, 0.5, 1.0],
    "epsilon": [0.0, 0.2, 0.5, 1.0],
}


def run_oat_sensitivity(objective) -> Dict[str, List[Dict[str, object]]]:
    """
    Classic one-at-a-time sensitivity analysis: for each of the five weights,
    hold the other four at CURRENT_DEFAULTS and sweep this one across its own
    range, recording the *continuous* regret value at every point (not just
    which path wins, like sensitivity_analysis.py's Sweep C) -- this is what
    directly shows "how much does moving this variable alone move the
    objective."
    """
    results: Dict[str, List[Dict[str, object]]] = {}
    for var, grid in OAT_SENSITIVITY_GRID.items():
        rows: List[Dict[str, object]] = []
        for value in grid:
            kwargs = dict(CURRENT_DEFAULTS)
            kwargs[var] = value
            r = objective(**kwargs)
            rows.append({"variable": var, "value": value, "regret": round(r, 6),
                         "is_current_default": value == CURRENT_DEFAULTS[var]})
        results[var] = rows
    return results


def run_grid_search(objective) -> List[Dict[str, object]]:
    rows: List[Dict[str, object]] = []
    grid = {p: np.linspace(BOUNDS[p][0], BOUNDS[p][1], GRID_BUDGET_PER_DIM) for p in PARAM_ORDER}
    for a in grid["alpha"]:
        for b in grid["beta"]:
            for g in grid["gamma"]:
                r = objective(float(a), float(b), float(g))
                rows.append({"alpha": float(a), "beta": float(b), "gamma": float(g), "regret": r})
    return rows


# ---------------------------------------------------------------------------
# From-scratch Gaussian Process regression + Expected Improvement BO loop.
# No scipy/sklearn available in this environment; everything below is plain
# numpy plus math.erf (standard library) for the normal CDF/PDF that
# Expected Improvement needs.
# ---------------------------------------------------------------------------

def rbf_kernel(X1: np.ndarray, X2: np.ndarray, length_scale: float, signal_var: float) -> np.ndarray:
    sq_dists = np.sum(X1**2, axis=1)[:, None] + np.sum(X2**2, axis=1)[None, :] - 2 * X1 @ X2.T
    return signal_var * np.exp(-0.5 * sq_dists / (length_scale**2))


def gp_posterior(X_train, y_train, X_test, length_scale=0.3, signal_var=1.0, noise=1e-6):
    K = rbf_kernel(X_train, X_train, length_scale, signal_var) + noise * np.eye(len(X_train))
    K_s = rbf_kernel(X_train, X_test, length_scale, signal_var)
    K_ss = rbf_kernel(X_test, X_test, length_scale, signal_var)
    L = np.linalg.cholesky(K)
    alpha_vec = np.linalg.solve(L.T, np.linalg.solve(L, y_train))
    mu = K_s.T @ alpha_vec
    v = np.linalg.solve(L, K_s)
    var = np.diag(K_ss) - np.sum(v**2, axis=0)
    return mu, np.maximum(var, 1e-12)


def normal_cdf(z: np.ndarray) -> np.ndarray:
    return 0.5 * (1.0 + np.vectorize(math.erf)(z / math.sqrt(2)))


def normal_pdf(z: np.ndarray) -> np.ndarray:
    return np.exp(-0.5 * z**2) / math.sqrt(2 * math.pi)


def expected_improvement(mu, var, best_so_far, xi=0.01):
    """EI for MINIMIZATION: improvement = best_so_far - mu (want mu below best)."""
    sigma = np.sqrt(var)
    improvement = best_so_far - mu - xi
    z = np.where(sigma > 0, improvement / sigma, 0.0)
    ei = improvement * normal_cdf(z) + sigma * normal_pdf(z)
    return np.where(sigma > 0, ei, 0.0)


def run_bayesian_optimization(objective, seed: int = 0) -> List[Dict[str, object]]:
    rng = random.Random(seed)
    np_rng = np.random.default_rng(seed)
    rows: List[Dict[str, object]] = []
    X: List[List[float]] = []
    y: List[float] = []

    # Initial random points (BO needs a few observations before the GP means anything).
    for _ in range(BO_INIT_POINTS):
        point = [rng.uniform(*BOUNDS[p]) for p in PARAM_ORDER]
        r = objective(*point)
        X.append(point)
        y.append(r)
        rows.append({"alpha": point[0], "beta": point[1], "gamma": point[2], "regret": r, "phase": "init"})

    for _ in range(BO_ITERATIONS):
        X_arr = np.array(X)
        y_arr = np.array(y)
        best_so_far = float(np.min(y_arr))

        # Optimize the acquisition function by dense random search over the
        # bounded cube (no scipy.optimize available) -- 3000 candidates is
        # cheap and adequate at this dimensionality.
        candidates = np_rng.uniform(
            low=[BOUNDS[p][0] for p in PARAM_ORDER],
            high=[BOUNDS[p][1] for p in PARAM_ORDER],
            size=(3000, 3),
        )
        mu, var = gp_posterior(X_arr, y_arr, candidates)
        ei = expected_improvement(mu, var, best_so_far)
        next_point = candidates[int(np.argmax(ei))]

        r = objective(float(next_point[0]), float(next_point[1]), float(next_point[2]))
        X.append(list(next_point))
        y.append(r)
        rows.append(
            {"alpha": float(next_point[0]), "beta": float(next_point[1]), "gamma": float(next_point[2]),
             "regret": r, "phase": "bo"}
        )

    return rows


SA_ITERATIONS = 216  # same total budget as grid/BO, for a fair comparison
SA_INITIAL_TEMPERATURE = 0.5  # comparable to the largest possible regret (~0.52), so early moves are exploratory
SA_COOLING_RATE = 0.98  # geometric cooling: T_i = T0 * SA_COOLING_RATE^i
SA_PROPOSAL_STD = 0.15  # stddev of the Gaussian step proposed at each iteration, in the same [0,1]-ish units as the weights


def run_simulated_annealing(objective, seed: int = 0) -> List[Dict[str, object]]:
    """
    Classic simulated annealing: propose a small random step from the
    current point; always accept it if it's better (lower regret); if it's
    worse, still accept with probability exp(-(new-current)/T), so the
    search can climb out of flat plateaus/local optima instead of getting
    stuck the moment it lands on one -- particularly relevant here since the
    regret surface is piecewise-constant with large flat regions (confirmed
    in sensitivity_analysis.py's Sweep C/D). T is cooled geometrically each
    iteration, so early on almost any move is accepted (exploration), and by
    the end only improving moves are (exploitation).
    """
    rng = random.Random(seed)
    rows: List[Dict[str, object]] = []

    current = [rng.uniform(*BOUNDS[p]) for p in PARAM_ORDER]
    current_regret = objective(*current)
    best_point, best_regret = list(current), current_regret
    rows.append({"alpha": current[0], "beta": current[1], "gamma": current[2],
                 "regret": current_regret, "temperature": SA_INITIAL_TEMPERATURE, "accepted": True})

    temperature = SA_INITIAL_TEMPERATURE
    for _ in range(SA_ITERATIONS - 1):
        candidate = [
            min(max(current[i] + rng.gauss(0.0, SA_PROPOSAL_STD), BOUNDS[PARAM_ORDER[i]][0]), BOUNDS[PARAM_ORDER[i]][1])
            for i in range(3)
        ]
        candidate_regret = objective(*candidate)

        delta = candidate_regret - current_regret
        if delta <= 0:
            accept = True
        else:
            accept_prob = math.exp(-delta / temperature) if temperature > 1e-12 else 0.0
            accept = rng.random() < accept_prob

        if accept:
            current, current_regret = candidate, candidate_regret
            if current_regret < best_regret:
                best_point, best_regret = list(current), current_regret

        rows.append({"alpha": candidate[0], "beta": candidate[1], "gamma": candidate[2],
                     "regret": candidate_regret, "temperature": round(temperature, 6), "accepted": accept})
        temperature *= SA_COOLING_RATE

    return rows


def main() -> None:
    output_dir = Path("results/pilot/sensitivity")
    output_dir.mkdir(parents=True, exist_ok=True)

    objective, gt_costs = make_objective()

    oat_results = run_oat_sensitivity(objective)
    oat_rows: List[Dict[str, object]] = [row for rows in oat_results.values() for row in rows]
    with (output_dir / "oat_regret_sensitivity.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(oat_rows[0].keys()))
        w.writeheader()
        w.writerows(oat_rows)

    oat_impact = {
        var: {
            "min_regret": min(row["regret"] for row in rows),
            "max_regret": max(row["regret"] for row in rows),
            "range": round(max(row["regret"] for row in rows) - min(row["regret"] for row in rows), 6),
        }
        for var, rows in oat_results.items()
    }
    ranked = sorted(oat_impact.items(), key=lambda kv: -kv[1]["range"])

    grid_rows = run_grid_search(objective)
    bo_rows = run_bayesian_optimization(objective)
    sa_rows = run_simulated_annealing(objective)

    grid_best = min(grid_rows, key=lambda r: r["regret"])
    bo_best = min(bo_rows, key=lambda r: r["regret"])
    sa_best = min(sa_rows, key=lambda r: r["regret"])

    with (output_dir / "weight_search_grid.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(grid_rows[0].keys()))
        w.writeheader()
        w.writerows(grid_rows)

    with (output_dir / "weight_search_bayesian.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(bo_rows[0].keys()))
        w.writeheader()
        w.writerows(bo_rows)

    with (output_dir / "weight_search_simulated_annealing.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(sa_rows[0].keys()))
        w.writeheader()
        w.writerows(sa_rows)

    # Running best-so-far, to show convergence speed, not just the final answer.
    def running_best(rows):
        best = float("inf")
        trace = []
        for row in rows:
            best = min(best, row["regret"])
            trace.append(best)
        return trace

    grid_trace = running_best(grid_rows)
    bo_trace = running_best(bo_rows)
    sa_trace = running_best(sa_rows)

    def evals_to_converge(trace):
        return next(
            (i + 1 for i, v in enumerate(trace) if v <= trace[-1] * 1.01 or (trace[-1] == 0 and v == 0)),
            len(trace),
        )

    report = {
        "ground_truth_path_costs": gt_costs,
        "oat_sensitivity_ranked_by_impact": [{"variable": var, **impact} for var, impact in ranked],
        "evaluation_budget": len(grid_rows),
        "grid_search": {
            "best_regret": grid_best["regret"],
            "best_point": {k: grid_best[k] for k in PARAM_ORDER},
            "evaluations_to_reach_within_1pct_of_final_best": evals_to_converge(grid_trace),
        },
        "bayesian_optimization": {
            "best_regret": bo_best["regret"],
            "best_point": {k: bo_best[k] for k in PARAM_ORDER},
            "evaluations_to_reach_within_1pct_of_final_best": evals_to_converge(bo_trace),
        },
        "simulated_annealing": {
            "best_regret": sa_best["regret"],
            "best_point": {k: sa_best[k] for k in PARAM_ORDER},
            "evaluations_to_reach_within_1pct_of_final_best": evals_to_converge(sa_trace),
            "acceptance_rate": round(sum(1 for r in sa_rows if r["accepted"]) / len(sa_rows), 4),
        },
    }
    (output_dir / "weight_search_comparison.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
