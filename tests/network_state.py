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

import pytest

from src.monitor.network_state import NetworkState
from src.monitor.models import LinkStatistics
from src.routing.congestion_model import predicted_delay_ms, predicted_loss


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


def test_samples_during_the_post_switch_settle_window_are_excluded_from_jitter():
    """
    A switch event itself can cause a real, transient delay blip unrelated
    to steady-state jitter. Samples taken while a link is still within its
    jitter_settle_window_seconds of its last churn should not be recorded,
    so delta (control-plane churn) and zeta (data-plane jitter) don't
    partly double-count the same reroute event.
    """
    state = NetworkState(jitter_settle_window_seconds=10.0)
    u = 0.3
    predicted = predicted_delay_ms(u)
    state.record_link_churn("0-2", timestamp=100.0)

    # samples taken *during* the settle window (still within 10s of the
    # churn event) -- deliberately dispersed, but should be ignored
    for t, bump in [(101.0, -80.0), (105.0, 0.0), (109.0, 80.0)]:
        state.update_link_statistics(
            LinkStatistics(
                timestamp=datetime.fromtimestamp(t), link_id="0-2", utilization=u,
                rx_mbps=10.0, tx_mbps=10.0, delay_ms=predicted + bump,
            ),
            now=t,
        )
    assert state.get_delay_jitter_score("0-2", now=109.0) == 0.0  # nothing recorded yet

    # samples taken *after* the settle window has passed -- should count
    for t, bump in [(115.0, -80.0), (116.0, 0.0), (117.0, 80.0)]:
        state.update_link_statistics(
            LinkStatistics(
                timestamp=datetime.fromtimestamp(t), link_id="0-2", utilization=u,
                rx_mbps=10.0, tx_mbps=10.0, delay_ms=predicted + bump,
            ),
            now=t,
        )
    assert state.get_delay_jitter_score("0-2", now=117.0) > 0.0


def test_loss_jitter_score_visible_under_the_same_synthetic_clock_used_to_record_it():
    state = NetworkState()
    now = 1000.0
    for v in [-0.05, 0.0, 0.05]:
        state.record_loss_residual("0-2", v, now=now)
    assert state.get_loss_jitter_score("0-2", now=now) > 0.0


def test_update_link_statistics_feeds_the_loss_jitter_tracker_automatically():
    """Mirrors test_update_link_statistics_feeds_the_jitter_tracker_automatically
    for loss_residual (packet_loss - predicted_loss(utilization))."""
    state = NetworkState()
    ts = datetime(2026, 8, 19, 12, 0, 0)
    u = 0.75
    predicted = predicted_loss(u)
    for bump in [-0.05, 0.0, 0.05]:
        state.update_link_statistics(
            LinkStatistics(
                timestamp=ts, link_id="0-2", utilization=u,
                rx_mbps=10.0, tx_mbps=10.0, packet_loss=max(0.0, predicted + bump),
            )
        )
    assert state.get_loss_jitter_score("0-2", now=ts.timestamp()) > 0.0


def test_set_link_status_first_report_is_not_a_flap():
    """A link's very first status observation has nothing to flap from."""
    state = NetworkState()
    state.set_link_status("0-2", is_up=False, now=100.0)
    assert state.get_link_flap_score("0-2", now=100.0) == 0.0


def test_set_link_status_real_transitions_are_flaps():
    state = NetworkState()
    state.set_link_status("0-2", is_up=True, now=0.0)   # first report, not a flap
    state.set_link_status("0-2", is_up=False, now=10.0)  # down: 1 flap
    state.set_link_status("0-2", is_up=True, now=20.0)   # back up: 2nd flap
    assert state.get_link_flap_score("0-2", now=20.0) > 0.0


def test_set_link_status_repeating_the_same_status_is_not_a_flap():
    state = NetworkState()
    state.set_link_status("0-2", is_up=False, now=0.0)
    state.set_link_status("0-2", is_up=False, now=10.0)  # already down -- not a real transition
    assert state.get_link_flap_score("0-2", now=10.0) == 0.0


def test_correlated_flap_burst_is_discounted():
    """Many links transitioning together inside one poll is a controller-view
    artefact, not that many real failures -- each transition is recorded at a
    reduced weight, so no single link in the burst gets pushed to a
    resilience-avoidance level on the strength of the burst alone."""
    burst = NetworkState()
    isolated = NetworkState()
    for lid in ("0-1", "0-2", "0-3", "0-4", "0-5"):  # >= CORRELATED_FLAP_MIN_LINKS
        burst.set_link_status(lid, is_up=True, now=0.0)
        burst.set_link_status(lid, is_up=False, now=1.0)   # all flap in the same poll
    isolated.set_link_status("0-1", is_up=True, now=0.0)
    isolated.set_link_status("0-1", is_up=False, now=1.0)  # one link, alone

    assert burst.get_link_flap_score("0-1", now=1.0) < isolated.get_link_flap_score("0-1", now=1.0)


def test_a_single_link_flapping_repeatedly_is_not_discounted():
    """The discount keys off *many links at once*, not one link flapping fast --
    a genuinely unstable single link must still accumulate full penalty."""
    solo = NetworkState()
    solo.set_link_status("0-2", is_up=True, now=0.0)
    for i, up in enumerate([False, True, False, True], start=1):
        solo.set_link_status("0-2", is_up=up, now=float(i))
    ref = NetworkState()
    ref.set_link_status("0-2", is_up=True, now=0.0)
    for i, up in enumerate([False, True, False, True], start=1):
        ref.set_link_status("0-2", is_up=up, now=float(i))
    assert solo.get_link_flap_score("0-2", now=4.0) == ref.get_link_flap_score("0-2", now=4.0)
    assert solo.get_link_flap_score("0-2", now=4.0) > 0.5


def test_abnormal_loss_score_visible_through_network_state_facade():
    state = NetworkState()
    now = 1000.0
    for v in [0.0, 0.0, 0.2, 0.2]:  # flat baseline, then a real upward shift
        state.record_loss_residual("0-2", v, now=now)
    assert state.get_abnormal_loss_score("0-2", now=now) > 0.0


def test_traffic_growth_score_visible_through_network_state_facade():
    state = NetworkState()
    ts = datetime(2026, 8, 19, 12, 0, 0)
    # 10 samples ramping tx_mbps from 5 to 50 -- a real, sustained growth pattern.
    for i in range(10):
        state.update_link_statistics(
            LinkStatistics(
                timestamp=ts, link_id="0-2", utilization=0.3,
                rx_mbps=10.0, tx_mbps=5.0 + i * 5.0,
            )
        )
    assert state.get_traffic_growth_score("0-2") > 0.0


def test_resilience_score_is_zero_when_nothing_is_anomalous():
    state = NetworkState()
    assert state.get_resilience_score("0-2", now=1000.0) == 0.0


def test_honestly_congested_link_is_not_a_resilience_anomaly():
    """abnormal_loss_score keys off a shift in the loss *residual* (actual minus
    utilization-predicted), so a link that is genuinely, heavily congested --
    high loss, but exactly the loss its own utilisation predicts -- must NOT
    read as a resilience anomaly. De-ranking that link is the alpha term's job;
    letting the gate also fire on it would wrongly structurally avoid a link the
    cost formula is already handling correctly (false positive)."""
    from src.routing.congestion_model import predicted_delay_ms, predicted_loss

    state = NetworkState()
    ts = datetime(2026, 9, 1, 12, 0, 0)
    for i in range(10):
        util = 0.30 if i < 5 else 0.95  # a real jump into heavy congestion
        state.update_link_statistics(
            LinkStatistics(
                timestamp=ts, link_id="0-2", utilization=util,
                rx_mbps=10.0, tx_mbps=10.0, status="up",
                delay_ms=predicted_delay_ms(util), packet_loss=predicted_loss(util),
            ),
            now=1000.0 + i,
        )
    assert state.get_abnormal_loss_score("0-2", now=1009.0) == 0.0
    assert state.get_resilience_score("0-2", now=1009.0) == 0.0


def test_resilience_score_is_the_max_of_its_components_not_their_sum():
    """Two simultaneous signals shouldn't stack past what either alone already means."""
    state = NetworkState()
    now = 1000.0
    for v in [0.0, 0.0, 0.2, 0.2]:  # flat baseline, then a real upward shift
        state.record_loss_residual("0-2", v, now=now)
    abnormal_loss_alone = state.get_abnormal_loss_score("0-2", now=now)

    state.set_link_status("0-2", is_up=True, now=now - 20.0)
    state.set_link_status("0-2", is_up=False, now=now - 10.0)
    state.set_link_status("0-2", is_up=True, now=now)

    combined = state.get_resilience_score("0-2", now=now)
    assert combined == pytest.approx(max(abnormal_loss_alone, state.get_link_flap_score("0-2", now=now)))
    assert combined <= 1.0
