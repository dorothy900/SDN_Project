#!/usr/bin/env python3
"""
Test Network State (churn facade)

Regression tests for a real bug found 2026-08-12: record_link_churn()
accepted an explicit timestamp, but get_link_churn_score() had no `now`
parameter at all and always read back against real wall-clock time. Any
caller recording churn against a synthetic clock (e.g. an offline experiment
using now_s starting from 0) would record correctly but always read back
0.0, since the window-eviction check compared the small synthetic timestamp
against real time and evicted it as falsely stale on every read. Existing
tests/link_churn_tracker.py tests didn't catch this because they exercise
LinkChurnTracker directly, bypassing NetworkState's facade entirely.
"""
from datetime import datetime

from src.monitor.network_state import NetworkState
from src.monitor.models import LinkStatistics
from src.routing.congestion_model import predicted_delay_ms


def test_churn_score_is_visible_under_the_same_synthetic_clock_used_to_record_it():
    state = NetworkState()
    state.record_link_churn("0-2", timestamp=2.0)
    assert state.get_link_churn_score("0-2", now=2.0) == 1 / 5  # 1 change / default saturation_count(5)


def test_churn_score_defaults_to_real_time_consistently_with_record():
    state = NetworkState()
    state.record_link_churn("0-2")  # no timestamp -> real time.time()
    assert state.get_link_churn_score("0-2") > 0.0  # no now= -> also real time.time(); these must agree


def test_synthetic_clock_recorded_churn_reads_as_zero_under_real_time_default():
    """
    Documents the actual failure mode this bug caused, so a future change
    that reintroduces it fails loudly: recording against a small synthetic
    clock, then reading with no `now` (defaulting to real wall-clock time,
    which is enormously larger), must NOT see that event -- it's outside the
    60s window relative to real time. This is expected/correct behavior for
    real deployment (record and read both naturally use real time there);
    the bug was the *absence* of a way to keep both sides on the same
    synthetic clock, not this specific real-vs-synthetic mismatch itself.
    """
    state = NetworkState()
    state.record_link_churn("0-2", timestamp=2.0)
    assert state.get_link_churn_score("0-2") == 0.0


def test_delay_jitter_score_visible_under_the_same_synthetic_clock_used_to_record_it():
    state = NetworkState()
    now = 1000.0
    for v in [-80.0, 0.0, 80.0]:
        state.record_delay_residual("0-2", v, now=now)
    assert state.get_delay_jitter_score("0-2", now=now) > 0.0


def test_update_link_statistics_feeds_the_jitter_tracker_automatically():
    """
    update_link_statistics() should compute this sample's delay residual
    (delay_ms - predicted_delay_ms(utilization)) and record it into the
    jitter tracker itself -- callers shouldn't need to call
    record_delay_residual() by hand on every monitoring update.
    """
    state = NetworkState()
    ts = datetime(2026, 8, 19, 12, 0, 0)
    u = 0.3
    # three samples whose delay is deliberately far from predicted_delay_ms(u)
    # in alternating directions, so the tracker sees real dispersion
    predicted = predicted_delay_ms(u)
    for bump in [-80.0, 0.0, 80.0]:
        state.update_link_statistics(
            LinkStatistics(
                timestamp=ts, link_id="0-2", utilization=u,
                rx_mbps=10.0, tx_mbps=10.0, delay_ms=predicted + bump,
            )
        )
    assert state.get_delay_jitter_score("0-2", now=ts.timestamp()) > 0.0
