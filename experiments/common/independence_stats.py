#!/usr/bin/env python3
"""
Independence Stats - pairwise and joint dependence tests for the cost
formula's five variables (utilization, delay, loss, instability, reliability),
implemented from scratch in numpy (no scipy/sklearn/statsmodels available in
this environment).

Three complementary tools, each answering a different question:
  - spearman_rho() + permutation_test(): pairwise *monotonic* dependence,
    with a permutation-based p-value (no asymptotic-normal assumption, which
    matters given the small sample sizes these experiments produce).
  - distance_correlation(): pairwise dependence of *any* form, not just
    monotonic -- dCor(X,Y) == 0 iff X and Y are statistically independent,
    a property Spearman/Pearson don't have (they can read 0 on a real but
    non-monotonic relationship, e.g. Y = X^2 around X=0).
  - variance_inflation_factors(): joint (not pairwise) linear collinearity --
    whether one variable is redundant given a *linear combination* of the
    others, which pairwise tests alone can miss.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np


def _rankdata(values: Sequence[float]) -> np.ndarray:
    """Rank values 1..n, averaging ranks across tied groups."""
    arr = np.asarray(values, dtype=float)
    order = np.argsort(arr, kind="mergesort")
    ranks = np.empty(len(arr), dtype=float)
    sorted_vals = arr[order]

    i = 0
    n = len(arr)
    while i < n:
        j = i
        while j + 1 < n and sorted_vals[j + 1] == sorted_vals[i]:
            j += 1
        avg_rank = (i + j) / 2.0 + 1.0
        ranks[order[i : j + 1]] = avg_rank
        i = j + 1
    return ranks


def pearson_r(x: Sequence[float], y: Sequence[float]) -> float:
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    xm = x - x.mean()
    ym = y - y.mean()
    denom = np.sqrt(np.sum(xm**2) * np.sum(ym**2))
    if denom == 0.0:
        return 0.0
    return float(np.sum(xm * ym) / denom)


def spearman_rho(x: Sequence[float], y: Sequence[float]) -> float:
    """Spearman rank correlation: Pearson correlation computed on ranks."""
    if len(x) != len(y):
        raise ValueError("x and y must be the same length")
    return pearson_r(_rankdata(x), _rankdata(y))


def distance_correlation(x: Sequence[float], y: Sequence[float]) -> float:
    """
    Distance correlation (Szekely, Rizzo, Bakirov 2007). dCor(X,Y) == 0 iff
    X and Y are statistically independent -- catches non-monotonic
    dependence that spearman_rho() is blind to.
    """
    x = np.asarray(x, dtype=float).reshape(-1, 1)
    y = np.asarray(y, dtype=float).reshape(-1, 1)
    if x.shape[0] != y.shape[0]:
        raise ValueError("x and y must be the same length")

    a = np.abs(x - x.T)
    b = np.abs(y - y.T)
    A = a - a.mean(axis=0, keepdims=True) - a.mean(axis=1, keepdims=True) + a.mean()
    B = b - b.mean(axis=0, keepdims=True) - b.mean(axis=1, keepdims=True) + b.mean()

    dcov2 = float((A * B).mean())
    dvar2_x = float((A * A).mean())
    dvar2_y = float((B * B).mean())
    if dvar2_x <= 0.0 or dvar2_y <= 0.0:
        return 0.0
    dcor2 = dcov2 / np.sqrt(dvar2_x * dvar2_y)
    return float(np.sqrt(max(dcor2, 0.0)))


def permutation_test(
    x: Sequence[float],
    y: Sequence[float],
    statistic_fn=spearman_rho,
    n_permutations: int = 10000,
    seed: int = 0,
) -> Tuple[float, float]:
    """
    Two-sided permutation test for a pairwise dependence statistic.

    Shuffles y relative to x n_permutations times, recomputes the statistic
    under each shuffle to build a null distribution (the distribution the
    statistic would take if x and y were independent), and reports what
    fraction of that null is at least as extreme as the real observed value.
    Doesn't rely on asymptotic-normal p-value tables, which is the point --
    those assume sample sizes larger than this project's real experiments
    can produce.
    """
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    observed = statistic_fn(x, y)

    rng = np.random.default_rng(seed)
    y_shuffled = y.copy()
    at_least_as_extreme = 0
    for _ in range(n_permutations):
        rng.shuffle(y_shuffled)
        stat = statistic_fn(x, y_shuffled)
        if abs(stat) >= abs(observed):
            at_least_as_extreme += 1

    # +1/+1 correction: the observed arrangement is itself one valid
    # permutation, so p can never be reported as exactly 0.
    p_value = (at_least_as_extreme + 1) / (n_permutations + 1)
    return float(observed), float(p_value)


def variance_inflation_factors(
    data: Dict[str, Sequence[float]],
) -> Dict[str, float]:
    """
    VIF for each named column against a linear combination of all the
    others: VIF_j = 1 / (1 - R^2_j), where R^2_j comes from regressing
    column j on every other column (plus an intercept) via ordinary least
    squares. Large VIF (conventionally > 5, sometimes > 10) means column j
    is close to redundant given the rest -- a *joint* form of collinearity
    plain pairwise correlation can miss.
    """
    names: List[str] = list(data.keys())
    X = np.column_stack([np.asarray(data[name], dtype=float) for name in names])
    n = X.shape[0]

    vifs: Dict[str, float] = {}
    for j, name in enumerate(names):
        target = X[:, j]
        predictors = np.delete(X, j, axis=1)
        design = np.column_stack([np.ones(n), predictors])
        beta, _, _, _ = np.linalg.lstsq(design, target, rcond=None)
        predicted = design @ beta
        ss_res = float(np.sum((target - predicted) ** 2))
        ss_tot = float(np.sum((target - target.mean()) ** 2))
        r_squared = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0
        vifs[name] = float("inf") if r_squared >= 1.0 else 1.0 / (1.0 - r_squared)
    return vifs
