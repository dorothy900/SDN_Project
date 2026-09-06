#!/usr/bin/env python3
"""
Weight Search Comparison - grid search vs Bayesian optimization vs
simulated annealing vs DIRECT for path_cost_weights, evaluated against a
shared ground-truth regret objective under a matched evaluation budget
(as opposed to the isolated cost/path-selection checks in
sensitivity_analysis.py).

Run as: python3 -m experiments.cost_formula.weight_search_comparison
"""
from __future__ import annotations

import csv
import json
import math
import random
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Sequence, Tuple

import numpy as np
from scipy.optimize import direct as scipy_direct

from experiments.sensitivity_analysis import (
    COST_PATH_A,
    COST_PATH_A_UNIQUE_EDGES,
    COST_PATH_B,
    COST_PATH_B_UNIQUE_EDGES,
    COST_PATH_C,
    COST_PATH_C_EDGES,
    COST_SHARED_EDGE,
)
from experiments.simulation_common import build_network_state, link_id
from src.monitor.models import LinkStatistics
from src.routing.graph_builder import GraphBuilder

# Searches 6 of the formula's 7 weights; epsilon is excluded and pinned at 0 -- a "down" link
# is removed from NetworkState's active_graph entirely, so epsilon can never apply to any of
# this comparison's 3 candidate paths.
PARAM_ORDER = ["alpha", "beta", "gamma", "delta", "zeta", "eta"]
GROUND_TRUTH_WEIGHTS = {p: 1.0 / len(PARAM_ORDER) for p in PARAM_ORDER}
GROUND_TRUTH_WEIGHTS["epsilon"] = 0.0
BOUNDS = {p: (0.0, 1.0) for p in PARAM_ORDER}

# zeta has by far the smallest OAT regret range of the 6 searched weights (0.041, vs 0.121
# for the next-lowest, beta) -- see oat_sensitivity_ranked_by_impact in the report this
# module writes. run_direct_then_sa() fixes it at its production default to search 5 dims
# instead of 6; delta/eta are NOT fixed here despite superficially looking like secondary
# weights, since their own OAT ranges (0.301, 0.256) are actually higher than beta's.
HYBRID_FREE_PARAMS = ["alpha", "beta", "gamma", "delta", "eta"]
HYBRID_ZETA_FIXED = 0.05

# 6 dims: matched grid/BO/SA budgets at 3 points/dim (3^6=729) -- large
# enough to be a meaningful comparison, small enough that BO's O(budget^3)
# per-iteration GP refit (Cholesky over all points seen so far) stays fast.
GRID_BUDGET_PER_DIM = 3
TOTAL_BUDGET = GRID_BUDGET_PER_DIM ** len(PARAM_ORDER)  # 729
BO_INIT_POINTS = 20
BO_ITERATIONS = TOTAL_BUDGET - BO_INIT_POINTS
SA_ITERATIONS = TOTAL_BUDGET


NUM_TRIALS = 250  # averaging regret over this many independently-randomized
                  # trials, not one fixed scenario -- see make_objective()'s
                  # docstring for why a single trial was rejected as a
                  # benchmark (73% of the 6-D space tied at regret=0).
                  #
                  # Sized via a Law & Kelton pilot-variance sample-size
                  # check: a 15-trial pilot at a production-like weight
                  # point showed regret is zero-inflated there (most trials
                  # land exactly at 0, a minority carry the whole nonzero
                  # signal), which makes a *relative*-precision target
                  # infeasible -- it would demand N in the thousands purely
                  # because the mean itself is tiny, not because the
                  # estimate is actually unstable. Targeted *absolute*
                  # precision instead (95% CI half-width <= 0.005, small
                  # relative to this project's documented nonzero OAT
                  # sensitivities of 0.033-0.167): implied per-trial std
                  # ~0.0392 -> required N ~236, rounded up to 250 for pilot-
                  # estimate safety margin. Costs ~0.30-0.37s/objective-call,
                  # still affordable for the 729-point grid/DIRECT/BO/SA
                  # comparisons this module runs.


def _build_extended_contrast_state(seed: int = 0):
    """
    sensitivity_analysis.py's 3-way utilization/delay/loss contrast, plus
    real churn/delay-jitter/loss-jitter concentrated on Path C (see module
    docstring's "current design" section for why C specifically) -- but
    with every path's severity now *randomized per trial* (seeded off
    `seed`) instead of one fixed extreme value each. A single fixed-severity
    trial made Path A so catastrophically bad on utilization (and Path C's
    loss/churn/jitter so uniformly bad) that the decision was nearly
    invariant to the actual weight vector across most of the search space
    -- graded severities mean *how much* a term is weighted actually
    matters, not just whether it's above some trivial threshold.
    """
    rng = random.Random(seed)
    state = build_network_state(Path("results/pilot/sensitivity"), seed=seed)

    # build_network_state() already stamped every edge once (including
    # Path A/B/C's and the shared edge's) to seed the background topology --
    # that stamp also recorded a delay/loss-residual sample into the jitter
    # trackers, same as any other update_link_statistics() call. Path C
    # deliberately restamps its own edges 3 more times below to build a
    # *controlled* jitter signal (bad_loss + swing) -- but without this
    # reset, that background sample would silently count as a 4th,
    # unintended sample in the same rolling window, letting whatever the
    # background baseline happens to be (see sndlib_demand.py) leak into
    # what's supposed to be a fully scripted, graded-severity signal.
    # Reset wipes it for every link_id, which is safe here: nothing has
    # read jitter yet, and only Path A/B/C/shared edges are ever consulted
    # via get_path_cost().
    state.delay_jitter.reset()
    state.loss_jitter.reset()

    ts = datetime(2026, 8, 24, 12, 0, 0).timestamp()

    def stamp(u: str, v: str, utilization: float, delay_ms: float, loss: float, now: float) -> None:
        lid = link_id(u, v)
        state.update_link_statistics(
            LinkStatistics(
                timestamp=datetime.fromtimestamp(now), link_id=lid, utilization=utilization,
                rx_mbps=round(20.0 + utilization * 80.0, 4),
                tx_mbps=round(18.0 + utilization * 75.0, 4),
                status="up", delay_ms=delay_ms, packet_loss=loss,
            ),
            now=now,
        )

    # Path A: bad utilization only, severity graded across trials -- still
    # deliberately left clean on delta/zeta/eta (putting a term on a path
    # that's hopeless on alpha alone can never produce regret signal).
    #
    # Range grounded in real Mininet-measured congestion data
    # (results/independence_check/independence_samples.csv, 196 real
    # samples): real achieved_utilization's p75-max is (0.445, 0.782).
    bad_u = rng.uniform(0.445, 0.782)
    for u, v in COST_PATH_A_UNIQUE_EDGES:
        stamp(u, v, utilization=bad_u, delay_ms=2.0, loss=0.0001, now=ts)

    # Path B: bad delay only, severity graded -- the "correct" ground-truth
    # answer more often than not, left clean on delta/zeta/eta.
    #
    # Range grounded in the same real dataset's delay_ms column: p75-p99 is
    # (177.8, 697.1) ms, capped at p99 rather than the real max (1368.1ms)
    # to avoid one outlier sample stretching the whole range.
    bad_delay = rng.uniform(177.8, 697.1)
    for u, v in COST_PATH_B_UNIQUE_EDGES:
        stamp(u, v, utilization=0.10, delay_ms=bad_delay, loss=0.0001, now=ts)

    # Path C: the "trap" -- modest-to-severe loss (gamma) plus real churn
    # history and 3 stats samples (so delay/loss jitter have genuine
    # dispersion to measure -- single-sample jitter is always 0.0, both
    # trackers need >=3 samples), all graded per trial.
    #
    # base_loss grounded in results/loss_saturation_check/loss_samples.csv's
    # nonzero real measured loss (20/30 real samples): p25-p75 is (0.182,
    # 0.520). loss_swing is a moderate fraction of that range (a judgment
    # call -- no matching real per-trial-swing statistic exists to ground
    # it against). churn_events capped at 4 (not the theoretical max of 6):
    # real churn_score in results/churn_jitter_check/churn_jitter_samples.csv
    # never exceeded 0.8 (saturation_count=5, so 0.8 = 4 real events
    # observed, never the full 5/1.0).
    base_loss = rng.uniform(0.182, 0.520)
    loss_swing = rng.uniform(0.02, 0.15)
    churn_events = rng.randint(1, 4)
    for u, v in COST_PATH_C_EDGES:
        loss_samples = [
            max(0.0001, base_loss - loss_swing),
            base_loss + loss_swing,
            base_loss,
        ]
        for i, l in enumerate(loss_samples):
            d = rng.uniform(2.0, 8.0)
            stamp(u, v, utilization=0.10, delay_ms=d, loss=l, now=ts + i * 2.0)
        lid = link_id(u, v)
        for i in range(churn_events):
            state.record_link_churn(lid, timestamp=ts + i)

    # Shared edge: single neutral stamp, no contrast on any dimension.
    stamp(*COST_SHARED_EDGE, utilization=0.30, delay_ms=6.0, loss=0.001, now=ts)

    return state


def make_objective(
    num_trials: int = NUM_TRIALS, seed_offset: int = 0
) -> Tuple[Callable[..., float], List[Dict[str, float]]]:
    """
    Build the regret objective, averaged across `num_trials` independently randomized
    contrast scenarios rather than one fixed severity -- a single fixed trial left 73% of a
    3000-point random sample of the 6-D weight space tied at regret=0 (alpha/gamma alone
    already settled the decision most of the time), so averaging over graded severities is
    what makes delta/zeta/eta's weight actually matter to the outcome.

    seed_offset lets run_multi_instance_comparison() give each instance its own
    non-overlapping trial-seed range instead of rebuilding the same trials every time.
    """
    now = datetime(2026, 8, 24, 12, 0, 0).timestamp()
    trials: List[Tuple[object, Dict[str, float], float]] = []
    for trial_seed in range(seed_offset, seed_offset + num_trials):
        state = _build_extended_contrast_state(seed=trial_seed)
        gt_builder = GraphBuilder(state, weights=GROUND_TRUTH_WEIGHTS)
        gt_graph = gt_builder.build_weighted_graph(now=now)
        gt_costs = {
            "A": gt_builder.get_path_cost(COST_PATH_A, gt_graph, now=now),
            "B": gt_builder.get_path_cost(COST_PATH_B, gt_graph, now=now),
            "C": gt_builder.get_path_cost(COST_PATH_C, gt_graph, now=now),
        }
        trials.append((state, gt_costs, min(gt_costs.values())))

    def regret(alpha: float, beta: float, gamma: float, delta: float, zeta: float, eta: float,
               epsilon: float = 0.05) -> float:
        weights = {
            "alpha": alpha, "beta": beta, "gamma": gamma, "delta": delta,
            "epsilon": epsilon, "zeta": zeta, "eta": eta,
        }
        total = 0.0
        for state, gt_costs, best_gt in trials:
            builder = GraphBuilder(state, weights=weights)
            graph = builder.build_weighted_graph(now=now)
            costs = {
                "A": builder.get_path_cost(COST_PATH_A, graph, now=now),
                "B": builder.get_path_cost(COST_PATH_B, graph, now=now),
                "C": builder.get_path_cost(COST_PATH_C, graph, now=now),
            }
            chosen = min(costs, key=costs.get)
            total += gt_costs[chosen] - best_gt
        return total / len(trials)

    return regret, [gt_costs for _, gt_costs, _ in trials]


def normalize_weights(raw: Sequence[float], epsilon: float = 0.05) -> Dict[str, float]:
    """
    Rescale the 6 searched weights to sum to (1 - epsilon), keeping epsilon fixed, so the 7
    production weights always sum to ~1. Without this a search can recommend delta/zeta/eta
    -- meant as small, secondary tie-breakers -- at a magnitude that competes head-to-head
    with alpha, letting mild instability outweigh heavy real congestion.
    """
    total = sum(raw)
    if total <= 0:
        # All-zero raw vector: fall back to an equal split instead of dividing by zero.
        share = (1.0 - epsilon) / len(raw)
        scaled = [share] * len(raw)
    else:
        scale = (1.0 - epsilon) / total
        scaled = [v * scale for v in raw]
    weights = dict(zip(PARAM_ORDER, scaled))
    weights["epsilon"] = epsilon
    return weights


def make_normalized_objective(**kwargs) -> Tuple[Callable[..., float], List[Dict[str, float]]]:
    """
    Same benchmark as make_objective(), but the returned regret() first
    normalizes its 6 raw weight arguments to a fixed-sum simplex (see
    normalize_weights()) before pricing any path -- so a search over this
    objective explores *relative importance allocations*, not 6
    independently-maxable dials.
    """
    raw_regret, gt_costs_per_trial = make_objective(**kwargs)

    def normalized_regret(alpha: float, beta: float, gamma: float, delta: float, zeta: float, eta: float) -> float:
        weights = normalize_weights([alpha, beta, gamma, delta, zeta, eta])
        return raw_regret(**weights)

    return normalized_regret, gt_costs_per_trial


CURRENT_DEFAULTS = {"alpha": 0.4, "beta": 0.3, "gamma": 0.2, "delta": 0.05, "zeta": 0.05, "eta": 0.05}
OAT_SENSITIVITY_GRID = {
    "alpha": [0.0, 0.02, 0.05, 0.1, 0.2, 0.4, 0.7, 1.0],
    "beta": [0.0, 0.1, 0.3, 0.6, 1.0, 1.5, 2.0],
    "gamma": [0.0, 0.1, 0.2, 0.3, 0.5, 0.7, 0.9, 1.2],
    "delta": [0.0, 0.2, 0.5, 1.0],
    "zeta": [0.0, 0.2, 0.5, 1.0],
    "eta": [0.0, 0.2, 0.5, 1.0],
}


def run_oat_sensitivity(objective) -> Dict[str, List[Dict[str, object]]]:
    """One-at-a-time: for each weight, hold the other five at
    CURRENT_DEFAULTS and sweep this one across its own range."""
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
                for d in grid["delta"]:
                    for z in grid["zeta"]:
                        for e in grid["eta"]:
                            r = objective(float(a), float(b), float(g), float(d), float(z), float(e))
                            rows.append({"alpha": float(a), "beta": float(b), "gamma": float(g),
                                         "delta": float(d), "zeta": float(z), "eta": float(e), "regret": r})
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


def gp_posterior(X_train, y_train, X_test, length_scale=0.5, signal_var=1.0, noise=1e-6):
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


def run_bayesian_optimization(
    objective, seed: int = 0, init_points: int = BO_INIT_POINTS, iterations: int = BO_ITERATIONS
) -> List[Dict[str, object]]:
    """
    seed must come from an independent stream, not shared with
    run_simulated_annealing's seed -- see spawn_independent_seeds() and the
    "seed-sharing bug" note in the module docstring for why this matters:
    passing the same literal seed value to both this function and
    run_simulated_annealing makes their *first* random draw identical
    (both do `[rng.uniform(*BOUNDS[p]) for p in PARAM_ORDER]` as literally
    their first action after seeding `random.Random(seed)`), which silently
    invalidates any "which one converges faster" comparison between them.
    """
    rng = random.Random(seed)
    np_rng = np.random.default_rng(seed)
    rows: List[Dict[str, object]] = []
    X: List[List[float]] = []
    y: List[float] = []
    ndim = len(PARAM_ORDER)

    for _ in range(init_points):
        point = [rng.uniform(*BOUNDS[p]) for p in PARAM_ORDER]
        r = objective(*point)
        X.append(point)
        y.append(r)
        rows.append({**dict(zip(PARAM_ORDER, point)), "regret": r, "phase": "init"})

    for _ in range(iterations):
        X_arr = np.array(X)
        y_arr = np.array(y)
        best_so_far = float(np.min(y_arr))

        # Dense random search over the bounded cube for the acquisition
        # function's argmax (no scipy.optimize available).
        candidates = np_rng.uniform(
            low=[BOUNDS[p][0] for p in PARAM_ORDER],
            high=[BOUNDS[p][1] for p in PARAM_ORDER],
            size=(2000, ndim),
        )
        mu, var = gp_posterior(X_arr, y_arr, candidates)
        ei = expected_improvement(mu, var, best_so_far)
        next_point = candidates[int(np.argmax(ei))]

        r = objective(*[float(v) for v in next_point])
        X.append(list(next_point))
        y.append(r)
        rows.append({**dict(zip(PARAM_ORDER, [float(v) for v in next_point])), "regret": r, "phase": "bo"})

    return rows


SA_INITIAL_TEMPERATURE = 0.5
SA_COOLING_RATE = 0.995  # slower cooling than the 3-dim version -- more
                          # iterations are available (729 vs 216) and 6
                          # dimensions need more time to explore
SA_PROPOSAL_STD = 0.15


def run_simulated_annealing(
    objective,
    seed: int = 0,
    iterations: int = SA_ITERATIONS,
    initial_temperature: float = SA_INITIAL_TEMPERATURE,
    cooling_rate: float = SA_COOLING_RATE,
) -> List[Dict[str, object]]:
    """Same accept-always-if-better / accept-worse-with-probability
    exp(-delta/T) scheme as before, now over 6 dimensions.

    seed must come from a stream independent of run_bayesian_optimization's
    -- see that function's docstring.
    """
    rng = random.Random(seed)
    rows: List[Dict[str, object]] = []
    ndim = len(PARAM_ORDER)

    current = [rng.uniform(*BOUNDS[p]) for p in PARAM_ORDER]
    current_regret = objective(*current)
    rows.append({**dict(zip(PARAM_ORDER, current)), "regret": current_regret,
                 "temperature": initial_temperature, "accepted": True})

    temperature = initial_temperature
    for _ in range(iterations - 1):
        candidate = [
            min(max(current[i] + rng.gauss(0.0, SA_PROPOSAL_STD), BOUNDS[PARAM_ORDER[i]][0]), BOUNDS[PARAM_ORDER[i]][1])
            for i in range(ndim)
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

        rows.append({**dict(zip(PARAM_ORDER, candidate)), "regret": candidate_regret,
                     "temperature": round(temperature, 6), "accepted": accept})
        temperature *= cooling_rate

    return rows


COMPARISON_NUM_SEEDS = 50  # statistical power for the Mann-Whitney/Cliff's
                            # delta comparisons; benchmarked (~7.5s/instance
                            # for the 4-method multi-instance comparison,
                            # ~6 minutes total) to confirm this stays
                            # practical rather than picking it blind.
COMPARISON_INIT_POINTS = 8
COMPARISON_ITERATIONS = 22  # 30 evaluations/run total -- a realistic
                             # "practical retuning budget" (nobody re-tunes
                             # 6 weights with 729 evaluations in practice),
                             # and small enough that not every run trivially
                             # reaches the known global optimum (small
                             # budgets keep real variance in the outcome to
                             # compare, unlike the 729-budget headline run
                             # where 68% of even a single random draw
                             # already succeeds).


# ---------------------------------------------------------------------------
# DIRECT (DIviding RECTangles) -- a good fit for this objective because the
# regret objective's real structure is piecewise-constant (each path's cost
# is linear in the weights, so "which path wins" only changes at sharp
# decision boundaries between trials), which breaks GP-based BO's core
# smoothness assumption. DIRECT doesn't build a smooth global surrogate the
# way GP does -- it recursively subdivides the search space into
# hyperrectangles and uses each rectangle's *size* plus its center's
# function value (not an assumed-smooth interpolation between distant
# points) to decide what to subdivide next, which is a much better
# theoretical fit for a landscape made of flat plateaus and sharp jumps.
# Uses scipy's implementation (the project's only scipy dependency -- see
# requirements.txt) rather than a from-scratch one: unlike GP/BO and SA
# (comparatively simple to hand-roll correctly), DIRECT's potentially-
# optimal-rectangle selection (a lower-convex-hull computation over
# rectangle size vs. function value) is easy to get subtly wrong, and the
# user's explicit call was to use the battle-tested implementation for this
# one method rather than risk a self-implemented bug undermining the very
# comparison this is meant to produce.
# ---------------------------------------------------------------------------

def run_direct(objective, budget: int = COMPARISON_INIT_POINTS + COMPARISON_ITERATIONS) -> List[Dict[str, object]]:
    """
    Deterministic -- no seed. Every evaluated point (not just the final
    best) is logged via the wrapped objective, since scipy's OptimizeResult
    only returns the final best x/fun/nfev, and cumulative regret (see
    run_multiseed_comparison's docstring for why that's the metric that
    actually differentiates methods here) needs the full trajectory.
    """
    rows: List[Dict[str, object]] = []

    def wrapped(x: np.ndarray) -> float:
        point = [float(v) for v in x]
        r = objective(*point)
        rows.append({**dict(zip(PARAM_ORDER, point)), "regret": r})
        return r

    bounds = [BOUNDS[p] for p in PARAM_ORDER]
    scipy_direct(wrapped, bounds=bounds, maxfun=budget)
    return rows


def run_direct_then_sa(
    objective,
    phase1_budget: int = COMPARISON_INIT_POINTS,
    phase2_iterations: int = COMPARISON_ITERATIONS,
    sa_seed: int = 0,
    zeta_fixed: float = HYBRID_ZETA_FIXED,
    neighborhood_pad: float = 0.15,
) -> List[Dict[str, object]]:
    """
    Two-stage hybrid over HYBRID_FREE_PARAMS (zeta fixed): DIRECT scans the space broadly
    first (phase1_budget evals), then SA refines within a bounding box around DIRECT's
    non-zero-regret points (the flat zero-regret majority of this benchmark is already
    "solved" and uninformative -- the box targets the actual decision boundary) for the
    remaining budget. Falls back to the full space if DIRECT's scan never left the
    zero-regret plateau.

    Total budget matches the other methods' (phase1_budget + phase2_iterations), but scipy's
    `maxfun` is an approximate cap, not a hard one -- DIRECT can't stop mid-iteration, so at
    small budgets in 5 dimensions it can overshoot by 2x or more (confirmed directly:
    phase1_budget=8 produced 19 real evaluations). phase2's iteration count is corrected
    for that overshoot below so the *total* evaluation count stays budget-matched, even
    though phase1 alone may have used more of it than requested.
    """
    def fixed_objective(alpha: float, beta: float, gamma: float, delta: float, eta: float) -> float:
        return objective(alpha=alpha, beta=beta, gamma=gamma, delta=delta, zeta=zeta_fixed, eta=eta)

    free_bounds = {p: BOUNDS[p] for p in HYBRID_FREE_PARAMS}

    phase1_rows: List[Dict[str, object]] = []

    def wrapped(x: np.ndarray) -> float:
        point = [float(v) for v in x]
        r = fixed_objective(*point)
        phase1_rows.append({**dict(zip(HYBRID_FREE_PARAMS, point)), "regret": r, "phase": "direct_scan"})
        return r

    scipy_direct(wrapped, bounds=[free_bounds[p] for p in HYBRID_FREE_PARAMS], maxfun=phase1_budget)

    # Correct phase 2's budget for phase 1's real (possibly larger) evaluation count, so the
    # combined total stays matched to the other methods' budget instead of silently growing.
    total_budget = phase1_budget + phase2_iterations
    phase2_iterations = max(1, total_budget - len(phase1_rows))

    nontrivial = [r for r in phase1_rows if r["regret"] > 0]
    if nontrivial:
        box = {
            p: (
                max(free_bounds[p][0], min(r[p] for r in nontrivial) - neighborhood_pad),
                min(free_bounds[p][1], max(r[p] for r in nontrivial) + neighborhood_pad),
            )
            for p in HYBRID_FREE_PARAMS
        }
    else:
        box = free_bounds

    # Phase 2: same accept-always-if-better / accept-worse-with-probability exp(-delta/T)
    # scheme as run_simulated_annealing, but proposals are clipped to `box`, not [0,1].
    rng = random.Random(sa_seed)
    current = [rng.uniform(*box[p]) for p in HYBRID_FREE_PARAMS]
    current_regret = fixed_objective(*current)
    phase2_rows: List[Dict[str, object]] = [{
        **dict(zip(HYBRID_FREE_PARAMS, current)), "regret": current_regret,
        "temperature": SA_INITIAL_TEMPERATURE, "accepted": True, "phase": "sa_refine",
    }]
    temperature = SA_INITIAL_TEMPERATURE
    for _ in range(phase2_iterations - 1):
        candidate = [
            min(max(current[i] + rng.gauss(0.0, SA_PROPOSAL_STD), box[HYBRID_FREE_PARAMS[i]][0]),
                box[HYBRID_FREE_PARAMS[i]][1])
            for i in range(len(HYBRID_FREE_PARAMS))
        ]
        candidate_regret = fixed_objective(*candidate)
        delta = candidate_regret - current_regret
        accept_prob = math.exp(-delta / temperature) if temperature > 1e-12 else 0.0
        accept = delta <= 0 or rng.random() < accept_prob
        if accept:
            current, current_regret = candidate, candidate_regret
        phase2_rows.append({
            **dict(zip(HYBRID_FREE_PARAMS, candidate)), "regret": candidate_regret,
            "temperature": round(temperature, 6), "accepted": accept, "phase": "sa_refine",
        })
        temperature *= SA_COOLING_RATE

    return phase1_rows + phase2_rows


def run_random_search(
    objective, seed: int = 0, budget: int = COMPARISON_INIT_POINTS + COMPARISON_ITERATIONS
) -> List[Dict[str, object]]:
    """
    Uniform random sampling -- the null-model baseline every optimizer
    comparison needs and this one was missing: DIRECT's advantage could in
    principle just be "this benchmark is easy, anything works," not
    DIRECT's partitioning being genuinely smarter. Comparing DIRECT against
    plain random search (not just against BO/SA) is what actually tests
    that distinction.
    """
    rng = random.Random(seed)
    rows: List[Dict[str, object]] = []
    for _ in range(budget):
        point = [rng.uniform(*BOUNDS[p]) for p in PARAM_ORDER]
        r = objective(*point)
        rows.append({**dict(zip(PARAM_ORDER, point)), "regret": r})
    return rows


# ---------------------------------------------------------------------------
# Mann-Whitney U test (independent two-sample rank test, pure numpy,
# permutation-based p-value -- same style as experiments/independence_stats.py's
# permutation_test(); kept hand-rolled even after adding scipy for DIRECT,
# since scipy.stats.mannwhitneyu would work too but every other stats
# function in this project already uses this project's own permutation-test
# convention and there's no reason to mix styles for just this one test).
# Used to compare BO vs SA (and now DIRECT) across many independent problem
# instances/seeds. Mann-Whitney (unpaired) rather than Wilcoxon signed-rank
# (paired) is the correct choice here: each method's i-th run shares no real
# correspondence with another method's i-th run beyond an arbitrary shared
# loop index -- their random search paths (or, for DIRECT, the varying
# problem instance) are independent, so treating "run i" as a matched pair
# would assume a dependency that doesn't exist. Two independent samples is
# exactly what Mann-Whitney is for.
# ---------------------------------------------------------------------------

def mann_whitney_u(x: Sequence[float], y: Sequence[float], num_permutations: int = 9999, seed: int = 0) -> Dict[str, float]:
    """U statistic for x vs y, plus a permutation-test p-value (two-sided)."""
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    combined = np.concatenate([x, y])
    ranks = _rankdata(combined)
    rank_sum_x = ranks[: len(x)].sum()
    u_x = rank_sum_x - len(x) * (len(x) + 1) / 2.0
    u_y = len(x) * len(y) - u_x
    observed_u = min(u_x, u_y)

    rng = np.random.default_rng(seed)
    perm_us = np.empty(num_permutations)
    n_x = len(x)
    for i in range(num_permutations):
        perm = rng.permutation(combined)
        perm_ranks = _rankdata(perm)
        perm_rank_sum_x = perm_ranks[:n_x].sum()
        perm_u_x = perm_rank_sum_x - n_x * (n_x + 1) / 2.0
        perm_u_y = n_x * len(y) - perm_u_x
        perm_us[i] = min(perm_u_x, perm_u_y)

    p_value = (np.sum(perm_us <= observed_u) + 1) / (num_permutations + 1)
    return {"U": float(observed_u), "p_value": float(p_value), "n_x": len(x), "n_y": len(y)}


def cliffs_delta(x: Sequence[float], y: Sequence[float]) -> float:
    """
    Cliff's delta effect size for x vs y: (#(x_i > y_j) - #(x_i < y_j)) /
    (n_x * n_y), in [-1, 1]. Complementary to Mann-Whitney's p-value -- p
    says whether a difference is likely real, delta says how big it is.
    Computed directly via pairwise dominance counting (not derived from the
    U statistic above, which discards direction by taking min(u_x, u_y));
    conventional magnitude bands (Romano et al.): negligible <0.147, small
    <0.33, medium <0.474, large >=0.474. Positive means x tends to exceed y.
    """
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    diffs = x[:, None] - y[None, :]
    more = np.sum(diffs > 0)
    less = np.sum(diffs < 0)
    return float((more - less) / (len(x) * len(y)))


def _rankdata(values: np.ndarray) -> np.ndarray:
    """Average ranks, ties split evenly (standard mid-rank convention)."""
    order = np.argsort(values, kind="mergesort")
    ranks = np.empty(len(values), dtype=float)
    sorted_vals = values[order]
    i = 0
    while i < len(values):
        j = i
        while j < len(values) and sorted_vals[j] == sorted_vals[i]:
            j += 1
        avg_rank = (i + j + 1) / 2.0  # 1-indexed rank average over the tie block
        ranks[order[i:j]] = avg_rank
        i = j
    return ranks


def spawn_independent_seeds(master_seed: int, num_streams: int, num_per_stream: int) -> List[List[int]]:
    """
    num_streams independent, uncorrelated streams of num_per_stream int
    seeds each, via numpy's SeedSequence spawning (recommended way to get
    statistically independent RNG streams -- unlike manually offsetting a
    single int seed, spawning guarantees no hidden correlation between
    streams). Used to give run_bayesian_optimization and
    run_simulated_annealing their own seed streams for the multi-seed
    comparison, instead of both defaulting to the same literal value.
    """
    master = np.random.SeedSequence(master_seed)
    children = master.spawn(num_streams)
    return [
        [int(grandchild.generate_state(1)[0]) for grandchild in child.spawn(num_per_stream)]
        for child in children
    ]


def run_multiseed_comparison(objective, num_seeds: int = COMPARISON_NUM_SEEDS) -> Dict[str, object]:
    """
    Run BO and SA num_seeds times each, independently seeded, at a matched budget
    (COMPARISON_INIT_POINTS + COMPARISON_ITERATIONS evaluations/run).

    Primary metric is *cumulative* regret, not final/best regret: at this benchmark's 68%
    zero-regret base rate, final regret is degenerate (both methods hit exactly 0.0 in
    essentially every run), while cumulative regret still captures how much a method
    "wastes" evaluating clearly-worse points along the way. Final regret is still
    recorded as a sanity check, not the headline number.
    """
    seed_streams = spawn_independent_seeds(master_seed=20260819, num_streams=2, num_per_stream=num_seeds)
    bo_seeds, sa_seeds = seed_streams

    bo_final_regrets: List[float] = []
    sa_final_regrets: List[float] = []
    bo_cumulative_regrets: List[float] = []
    sa_cumulative_regrets: List[float] = []
    bo_traces: List[List[float]] = []
    sa_traces: List[List[float]] = []

    for bo_seed in bo_seeds:
        rows = run_bayesian_optimization(
            objective, seed=bo_seed, init_points=COMPARISON_INIT_POINTS, iterations=COMPARISON_ITERATIONS
        )
        regrets = [row["regret"] for row in rows]
        trace = []
        best = float("inf")
        for r in regrets:
            best = min(best, r)
            trace.append(best)
        bo_traces.append(trace)
        bo_final_regrets.append(trace[-1])
        bo_cumulative_regrets.append(sum(regrets))

    for sa_seed in sa_seeds:
        rows = run_simulated_annealing(
            objective, seed=sa_seed, iterations=COMPARISON_INIT_POINTS + COMPARISON_ITERATIONS
        )
        regrets = [row["regret"] for row in rows]
        trace = []
        best = float("inf")
        for r in regrets:
            best = min(best, r)
            trace.append(best)
        sa_traces.append(trace)
        sa_final_regrets.append(trace[-1])
        sa_cumulative_regrets.append(sum(regrets))

    mw_final = mann_whitney_u(bo_final_regrets, sa_final_regrets)
    mw_cumulative = mann_whitney_u(bo_cumulative_regrets, sa_cumulative_regrets)

    # Mean convergence curve (best-so-far averaged across seeds, per
    # evaluation index) -- the standard way to visualize/compare optimizer
    # speed across many runs.
    bo_mean_curve = np.mean(np.array(bo_traces), axis=0).tolist()
    sa_mean_curve = np.mean(np.array(sa_traces), axis=0).tolist()

    return {
        "num_seeds": num_seeds,
        "budget_per_run": COMPARISON_INIT_POINTS + COMPARISON_ITERATIONS,
        "bo_final_regrets": bo_final_regrets,
        "sa_final_regrets": sa_final_regrets,
        "bo_cumulative_regrets": bo_cumulative_regrets,
        "sa_cumulative_regrets": sa_cumulative_regrets,
        "bo_mean_final_regret": round(float(np.mean(bo_final_regrets)), 6),
        "sa_mean_final_regret": round(float(np.mean(sa_final_regrets)), 6),
        "bo_mean_cumulative_regret": round(float(np.mean(bo_cumulative_regrets)), 6),
        "sa_mean_cumulative_regret": round(float(np.mean(sa_cumulative_regrets)), 6),
        "bo_median_cumulative_regret": round(float(np.median(bo_cumulative_regrets)), 6),
        "sa_median_cumulative_regret": round(float(np.median(sa_cumulative_regrets)), 6),
        "mann_whitney_u_final_regret": mw_final,
        "mann_whitney_u_cumulative_regret": mw_cumulative,
        "bo_mean_convergence_curve": [round(v, 6) for v in bo_mean_curve],
        "sa_mean_convergence_curve": [round(v, 6) for v in sa_mean_curve],
    }


MULTI_INSTANCE_METHODS = ["direct", "bayesian_optimization", "simulated_annealing", "random_search", "direct_then_sa_hybrid"]


def run_multi_instance_comparison(num_instances: int = COMPARISON_NUM_SEEDS) -> Dict[str, object]:
    """
    A fairer 3-way (now 5-way) comparison than run_multiseed_comparison():
    that function ran BO/SA many times against one *fixed* objective, so
    its "many samples" only captured each optimizer's own internal
    randomness, not variation in the underlying problem -- fine for a
    BO-vs-SA question, but DIRECT is deterministic (no seed of its own), so
    it can't be given "many seeds" on a fixed objective at all; it would
    just return the same trajectory every time. Instead this builds
    num_instances *independent* problem instances (via make_objective's
    seed_offset -- see its docstring) and runs DIRECT (deterministic, so
    its own variation comes entirely from the instance varying), BO, SA,
    plain random search, and the DIRECT-then-SA hybrid once each per
    instance, all at the same budget. Random search is included as the
    null-model baseline: without it, DIRECT's advantage over BO/SA can't be
    distinguished from "this benchmark is just easy, anything works" --
    comparing against uniform random sampling is what actually tests
    whether DIRECT's partitioning is doing something smarter than chance.
    """
    seed_streams = spawn_independent_seeds(master_seed=20260819, num_streams=4, num_per_stream=num_instances)
    bo_seeds, sa_seeds, random_seeds, hybrid_seeds = seed_streams
    budget = COMPARISON_INIT_POINTS + COMPARISON_ITERATIONS

    cumulative: Dict[str, List[float]] = {m: [] for m in MULTI_INSTANCE_METHODS}
    final: Dict[str, List[float]] = {m: [] for m in MULTI_INSTANCE_METHODS}
    traces: Dict[str, List[List[float]]] = {m: [] for m in MULTI_INSTANCE_METHODS}

    def record(method: str, rows: List[Dict[str, object]]) -> None:
        regrets = [row["regret"] for row in rows]
        cumulative[method].append(sum(regrets))
        best = float("inf")
        trace = []
        for r in regrets:
            best = min(best, r)
            trace.append(best)
        final[method].append(trace[-1])
        traces[method].append(trace)

    for i in range(num_instances):
        instance_objective, _ = make_objective(num_trials=NUM_TRIALS, seed_offset=i * NUM_TRIALS)

        record("direct", run_direct(instance_objective, budget=budget))
        record("bayesian_optimization", run_bayesian_optimization(
            instance_objective, seed=bo_seeds[i], init_points=COMPARISON_INIT_POINTS, iterations=COMPARISON_ITERATIONS
        ))
        record("simulated_annealing", run_simulated_annealing(instance_objective, seed=sa_seeds[i], iterations=budget))
        record("random_search", run_random_search(instance_objective, seed=random_seeds[i], budget=budget))
        record("direct_then_sa_hybrid", run_direct_then_sa(
            instance_objective, phase1_budget=COMPARISON_INIT_POINTS, phase2_iterations=COMPARISON_ITERATIONS,
            sa_seed=hybrid_seeds[i],
        ))

    # Mean convergence curve per method -- traces can be shorter than
    # `budget` for DIRECT (scipy's maxfun is an approximate cap, actual
    # nfev can undershoot), so trim every trace to the shortest length
    # before averaging rather than assuming a uniform length.
    min_len = min(len(t) for m in MULTI_INSTANCE_METHODS for t in traces[m])
    mean_curves = {
        m: np.mean(np.array([t[:min_len] for t in traces[m]]), axis=0).tolist()
        for m in MULTI_INSTANCE_METHODS
    }

    pairs = [("direct", "bayesian_optimization"), ("direct", "simulated_annealing"),
             ("direct", "random_search"), ("bayesian_optimization", "simulated_annealing"),
             ("direct_then_sa_hybrid", "direct"), ("direct_then_sa_hybrid", "simulated_annealing")]
    comparisons = {
        f"{a}_vs_{b}_cumulative": {
            **mann_whitney_u(cumulative[a], cumulative[b]),
            "cliffs_delta": round(cliffs_delta(cumulative[a], cumulative[b]), 4),
        }
        for a, b in pairs
    }

    result: Dict[str, object] = {
        "num_instances": num_instances,
        "budget_per_run": budget,
        "comparisons": comparisons,
        "mean_convergence_curves": {m: [round(v, 6) for v in mean_curves[m]] for m in MULTI_INSTANCE_METHODS},
    }
    for m in MULTI_INSTANCE_METHODS:
        result[f"{m}_cumulative_regrets"] = cumulative[m]
        result[f"{m}_mean_cumulative_regret"] = round(float(np.mean(cumulative[m])), 6)
        result[f"{m}_median_cumulative_regret"] = round(float(np.median(cumulative[m])), 6)
        result[f"{m}_mean_final_regret"] = round(float(np.mean(final[m])), 6)
    return result


def plot_convergence_curves(multi_instance: Dict[str, object], output_path: Path) -> None:
    """
    Mean best-so-far cumulative regret vs. evaluation count, one line per
    method, averaged across all instances in run_multi_instance_comparison()
    -- a table of final numbers hides *how* each method gets there (e.g.
    whether DIRECT pulls ahead immediately or only after its rectangle
    subdivisions kick in past the initial pass); the curve shows that
    directly. matplotlib import is local to this function since it's only
    needed for this one plot, not the rest of the module.
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    curves = multi_instance["mean_convergence_curves"]
    fig, ax = plt.subplots(figsize=(8, 5))
    labels = {"direct": "DIRECT", "bayesian_optimization": "Bayesian Optimization",
              "simulated_annealing": "Simulated Annealing", "random_search": "Random Search",
              "direct_then_sa_hybrid": "DIRECT-then-SA Hybrid"}
    for method in MULTI_INSTANCE_METHODS:
        curve = curves[method]
        ax.plot(range(1, len(curve) + 1), curve, label=labels.get(method, method), linewidth=2)
    ax.set_xlabel("Evaluation count")
    ax.set_ylabel("Mean best-so-far regret (averaged across instances)")
    ax.set_title("Convergence: DIRECT vs BO vs SA vs Random Search vs Hybrid")
    ax.legend()
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(output_path, dpi=150)
    plt.close(fig)


def run_out_of_sample_validation(
    candidates: Dict[str, Dict[str, float]],
    num_holdout: int = 20,
    holdout_seed_offset_start: int = 1_000_000,
) -> Dict[str, object]:
    """
    Every regret number so far measures a candidate weight vector against
    the SAME benchmark instances it was found/searched against -- a real
    overfitting risk (DIRECT's rectangle subdivisions could in principle be
    exploiting quirks of these specific randomly-generated trials rather
    than finding weights that genuinely generalize). This builds
    num_holdout entirely new problem instances via seed_offset starting at
    holdout_seed_offset_start -- far past any offset used during training
    (training used seed_offset in [0, COMPARISON_NUM_SEEDS*NUM_TRIALS), so
    no overlap is possible) -- and evaluates each candidate's regret on
    them with no further tuning of any kind.
    """
    holdout_regrets: Dict[str, List[float]] = {name: [] for name in candidates}
    for i in range(num_holdout):
        instance_objective, _ = make_objective(
            num_trials=NUM_TRIALS, seed_offset=holdout_seed_offset_start + i * NUM_TRIALS
        )
        for name, weights in candidates.items():
            holdout_regrets[name].append(instance_objective(**weights))

    return {
        "num_holdout_instances": num_holdout,
        "holdout_seed_offset_start": holdout_seed_offset_start,
        "mean_regret": {name: round(float(np.mean(rs)), 6) for name, rs in holdout_regrets.items()},
        "median_regret": {name: round(float(np.median(rs)), 6) for name, rs in holdout_regrets.items()},
        "raw_regrets": holdout_regrets,
    }


def main() -> None:
    output_dir = Path("results/pilot/sensitivity")
    output_dir.mkdir(parents=True, exist_ok=True)

    objective, gt_costs_per_trial = make_objective()

    gt_cost_summary = {
        path: {
            "mean": round(float(np.mean([t[path] for t in gt_costs_per_trial])), 6),
            "min": round(float(np.min([t[path] for t in gt_costs_per_trial])), 6),
            "max": round(float(np.max([t[path] for t in gt_costs_per_trial])), 6),
        }
        for path in ("A", "B", "C")
    }
    gt_winner_counts = {"A": 0, "B": 0, "C": 0}
    for t in gt_costs_per_trial:
        gt_winner_counts[min(t, key=t.get)] += 1

    current_defaults_regret = objective(**CURRENT_DEFAULTS)
    rng = np.random.default_rng(0)
    random_regrets = np.array([
        objective(*[float(v) for v in rng.uniform(0.0, 1.0, size=len(PARAM_ORDER))])
        for _ in range(500)
    ])
    fraction_zero_regret = round(float(np.mean(random_regrets == 0.0)), 4)

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

    # Independent seeds even for this single "headline" run -- both used to
    # silently default to seed=0, which (see run_bayesian_optimization's
    # docstring) made their first random draw identical and was part of
    # what made the original single-run comparison misleading.
    [(headline_bo_seed,), (headline_sa_seed,), (headline_hybrid_seed,)] = spawn_independent_seeds(
        master_seed=20260819, num_streams=3, num_per_stream=1
    )

    grid_rows = run_grid_search(objective)
    bo_rows = run_bayesian_optimization(objective, seed=headline_bo_seed)
    sa_rows = run_simulated_annealing(objective, seed=headline_sa_seed)
    direct_rows = run_direct(objective, budget=len(grid_rows))  # same budget as grid, for apples-to-apples
    # Same total budget as grid/direct's headline run, split 50 DIRECT-scan / rest SA-refine.
    hybrid_rows = run_direct_then_sa(
        objective, phase1_budget=50, phase2_iterations=len(grid_rows) - 50, sa_seed=headline_hybrid_seed,
    )

    grid_best = min(grid_rows, key=lambda r: r["regret"])
    bo_best = min(bo_rows, key=lambda r: r["regret"])
    sa_best = min(sa_rows, key=lambda r: r["regret"])
    direct_best = min(direct_rows, key=lambda r: r["regret"])
    hybrid_best = min(hybrid_rows, key=lambda r: r["regret"])

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

    with (output_dir / "weight_search_direct_then_sa_hybrid.csv").open("w", newline="", encoding="utf-8") as f:
        # phase1 (direct_scan) rows lack the temperature/accepted keys phase2 (sa_refine)
        # rows carry -- union the fieldnames across all rows rather than assuming the first
        # row's keys cover every row.
        fieldnames = list(dict.fromkeys(k for row in hybrid_rows for k in row.keys()))
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(hybrid_rows)

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

    multiseed = run_multiseed_comparison(objective)
    with (output_dir / "weight_search_multiseed_comparison.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["method", "seed_index", "final_regret", "cumulative_regret"])
        w.writeheader()
        for i, (fr, cr) in enumerate(zip(multiseed["bo_final_regrets"], multiseed["bo_cumulative_regrets"])):
            w.writerow({"method": "bayesian_optimization", "seed_index": i, "final_regret": fr, "cumulative_regret": cr})
        for i, (fr, cr) in enumerate(zip(multiseed["sa_final_regrets"], multiseed["sa_cumulative_regrets"])):
            w.writerow({"method": "simulated_annealing", "seed_index": i, "final_regret": fr, "cumulative_regret": cr})

    multi_instance = run_multi_instance_comparison(num_instances=COMPARISON_NUM_SEEDS)
    with (output_dir / "weight_search_multi_instance_comparison.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["method", "instance_index", "cumulative_regret"])
        w.writeheader()
        for method in MULTI_INSTANCE_METHODS:
            for i, cr in enumerate(multi_instance[f"{method}_cumulative_regrets"]):
                w.writerow({"method": method, "instance_index": i, "cumulative_regret": cr})

    plot_convergence_curves(multi_instance, output_dir / "weight_search_convergence_curves.png")

    out_of_sample_candidates = {
        "direct": {k: direct_best[k] for k in PARAM_ORDER},
        "bayesian_optimization": {k: bo_best[k] for k in PARAM_ORDER},
        "simulated_annealing": {k: sa_best[k] for k in PARAM_ORDER},
        "grid_search": {k: grid_best[k] for k in PARAM_ORDER},
        "direct_then_sa_hybrid": {**{k: hybrid_best[k] for k in HYBRID_FREE_PARAMS}, "zeta": HYBRID_ZETA_FIXED},
        "current_production_defaults": CURRENT_DEFAULTS,
    }
    out_of_sample = run_out_of_sample_validation(out_of_sample_candidates)
    with (output_dir / "weight_search_out_of_sample_validation.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["candidate", "holdout_index", "regret"])
        w.writeheader()
        for name, regrets in out_of_sample["raw_regrets"].items():
            for i, r in enumerate(regrets):
                w.writerow({"candidate": name, "holdout_index": i, "regret": r})

    report = {
        "num_trials": len(gt_costs_per_trial),
        "ground_truth_path_cost_summary": gt_cost_summary,
        "ground_truth_winner_counts": gt_winner_counts,
        "current_production_defaults": CURRENT_DEFAULTS,
        "current_production_defaults_regret": round(current_defaults_regret, 6),
        "fraction_zero_regret_in_500_random_weight_vectors": fraction_zero_regret,
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
        "direct": {
            "best_regret": direct_best["regret"],
            "best_point": {k: direct_best[k] for k in PARAM_ORDER},
            "num_evals": len(direct_rows),
        },
        "direct_then_sa_hybrid": {
            "best_regret": hybrid_best["regret"],
            "best_point": {**{k: hybrid_best[k] for k in HYBRID_FREE_PARAMS}, "zeta": HYBRID_ZETA_FIXED},
            "num_evals": len(hybrid_rows),
        },
        "multiseed_comparison": {
            "num_seeds": multiseed["num_seeds"],
            "budget_per_run": multiseed["budget_per_run"],
            "bo_mean_final_regret": multiseed["bo_mean_final_regret"],
            "sa_mean_final_regret": multiseed["sa_mean_final_regret"],
            "mann_whitney_u_final_regret": multiseed["mann_whitney_u_final_regret"],
            "bo_mean_cumulative_regret": multiseed["bo_mean_cumulative_regret"],
            "sa_mean_cumulative_regret": multiseed["sa_mean_cumulative_regret"],
            "bo_median_cumulative_regret": multiseed["bo_median_cumulative_regret"],
            "sa_median_cumulative_regret": multiseed["sa_median_cumulative_regret"],
            "mann_whitney_u_cumulative_regret": multiseed["mann_whitney_u_cumulative_regret"],
        },
        "multi_instance_comparison_direct_vs_bo_vs_sa_vs_random_vs_hybrid": {
            "num_instances": multi_instance["num_instances"],
            "budget_per_run": multi_instance["budget_per_run"],
            "mean_cumulative_regret": {m: multi_instance[f"{m}_mean_cumulative_regret"] for m in MULTI_INSTANCE_METHODS},
            "median_cumulative_regret": {m: multi_instance[f"{m}_median_cumulative_regret"] for m in MULTI_INSTANCE_METHODS},
            "mean_final_regret": {m: multi_instance[f"{m}_mean_final_regret"] for m in MULTI_INSTANCE_METHODS},
            "pairwise_comparisons": multi_instance["comparisons"],
        },
        "out_of_sample_validation": {
            "num_holdout_instances": out_of_sample["num_holdout_instances"],
            "mean_regret": out_of_sample["mean_regret"],
            "median_regret": out_of_sample["median_regret"],
        },
    }
    (output_dir / "weight_search_comparison.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
