#!/usr/bin/env python3
"""
Test History Store
"""
from datetime import datetime

import pytest

from src.monitor.history_store import LinkHistory


def _add_samples(history: LinkHistory, tx_values):
    ts = datetime(2026, 8, 19, 12, 0, 0)
    for tx in tx_values:
        history.add_sample(timestamp=ts, utilization=0.3, rx_mbps=10.0, tx_mbps=tx)


def test_traffic_growth_score_zero_with_fewer_than_two_samples_per_half():
    history = LinkHistory()
    _add_samples(history, [10.0, 20.0, 30.0])  # half=1
    assert history.traffic_growth_score == 0.0


def test_traffic_growth_score_zero_for_flat_traffic():
    history = LinkHistory()
    _add_samples(history, [10.0] * 10)
    assert history.traffic_growth_score == pytest.approx(0.0, abs=1e-9)


def test_traffic_growth_score_zero_with_no_net_shift_between_halves():
    history = LinkHistory()
    # Identical pattern repeated in both halves -- baseline and recent means match exactly.
    _add_samples(history, [10.0, 12.0, 10.0, 12.0, 10.0, 12.0, 10.0, 12.0])
    assert history.traffic_growth_score == pytest.approx(0.0, abs=1e-9)


def test_traffic_growth_score_rises_with_a_real_upward_shift():
    history = LinkHistory()
    _add_samples(history, [10.0, 10.0, 10.0, 50.0, 50.0, 50.0])
    assert history.traffic_growth_score > 0.0


def test_traffic_growth_score_ignores_downward_shift():
    """Traffic dropping off isn't 'abnormal growth' -- floored at 0, not negative."""
    history = LinkHistory()
    _add_samples(history, [50.0, 50.0, 50.0, 10.0, 10.0, 10.0])
    assert history.traffic_growth_score == 0.0


def test_traffic_growth_score_maxes_out_for_shift_from_a_flat_baseline():
    """baseline_std ~= 0 must not divide-blow-up -- any real positive shift reads as maximal."""
    history = LinkHistory()
    _add_samples(history, [10.0, 10.0, 10.0, 50.0, 50.0, 50.0])
    assert history.traffic_growth_score == 1.0


def test_traffic_growth_score_saturates_at_sigma_cap():
    history = LinkHistory()
    _add_samples(history, [10.0, 11.0, 9.0, 500.0, 500.0, 500.0])  # shift vastly exceeds 3 baseline std devs
    assert history.traffic_growth_score == 1.0
