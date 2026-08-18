#!/usr/bin/env python3
"""
Test Independence Stats

Each test checks the numpy-from-scratch implementations against a case
where the true answer is known analytically, not just "runs without
crashing" -- since there's no scipy/statsmodels reference implementation
available in this environment to compare against directly.
"""
import numpy as np
import pytest

from experiments.independence_stats import (
    distance_correlation,
    permutation_test,
    spearman_rho,
    variance_inflation_factors,
)


def test_spearman_rho_perfect_monotonic_is_one():
    x = np.arange(20, dtype=float)
    y = x**3  # monotonic but nonlinear -- spearman shouldn't care, pearson would
    assert spearman_rho(x, y) == pytest.approx(1.0)


def test_spearman_rho_perfectly_reversed_is_minus_one():
    x = np.arange(20, dtype=float)
    y = -x
    assert spearman_rho(x, y) == pytest.approx(-1.0)


def test_spearman_rho_independent_random_is_near_zero():
    rng = np.random.default_rng(42)
    x = rng.normal(size=500)
    y = rng.normal(size=500)
    assert abs(spearman_rho(x, y)) < 0.1


def test_permutation_test_flags_real_correlation_as_significant():
    x = np.arange(30, dtype=float)
    y = 2.0 * x + 1.0
    observed, p_value = permutation_test(x, y, n_permutations=999, seed=1)
    assert observed == pytest.approx(1.0)
    assert p_value < 0.01


def test_permutation_test_does_not_flag_independent_data_as_significant():
    rng = np.random.default_rng(7)
    x = rng.normal(size=40)
    y = rng.normal(size=40)
    _, p_value = permutation_test(x, y, n_permutations=999, seed=7)
    assert p_value > 0.05


def test_distance_correlation_catches_nonmonotonic_dependence_spearman_misses():
    # y = x^2 around x=0: strongly dependent, but not monotonic -- a real
    # failure mode Spearman is blind to, which is the whole reason dCor
    # is in this toolkit rather than relying on Spearman alone.
    x = np.linspace(-5, 5, 101)
    y = x**2
    rho = spearman_rho(x, y)
    dcor = distance_correlation(x, y)
    assert abs(rho) < 0.05
    assert dcor > 0.4


def test_distance_correlation_is_one_for_a_perfect_linear_relationship():
    x = np.arange(30, dtype=float)
    y = 3.0 * x - 7.0
    assert distance_correlation(x, y) == pytest.approx(1.0, abs=1e-9)


def test_distance_correlation_near_zero_for_independent_random_data():
    rng = np.random.default_rng(3)
    x = rng.normal(size=300)
    y = rng.normal(size=300)
    assert distance_correlation(x, y) < 0.15


def test_vif_is_near_one_for_independent_columns():
    rng = np.random.default_rng(11)
    n = 200
    data = {
        "u": rng.normal(size=n),
        "d": rng.normal(size=n),
        "l": rng.normal(size=n),
    }
    vifs = variance_inflation_factors(data)
    for name, vif in vifs.items():
        assert vif < 1.5, f"{name} VIF={vif} should be near 1 for independent columns"


def test_vif_is_large_when_one_column_is_a_linear_combination_of_others():
    rng = np.random.default_rng(11)
    n = 200
    u = rng.normal(size=n)
    d = rng.normal(size=n)
    # r is (almost exactly) u + d -- should be flagged as highly redundant
    # given the other two, even though pairwise it may not stand out.
    r = u + d + rng.normal(scale=1e-6, size=n)
    vifs = variance_inflation_factors({"u": u, "d": d, "r": r})
    assert vifs["r"] > 100
