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
from src.monitor.network_state import NetworkState


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
