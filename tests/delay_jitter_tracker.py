#!/usr/bin/env python3
"""
Test Delay Jitter Tracker
"""
import pytest
from src.monitor.delay_jitter_tracker import DelayJitterTracker


def test_no_samples_recorded_is_zero_score():
    tracker = DelayJitterTracker()
    assert tracker.get_jitter_score("s1-s2") == 0.0


def test_fewer_than_min_samples_is_zero_score():
    tracker = DelayJitterTracker(min_samples=3)
    now = 1000.0
    tracker.record_delay_residual("s1-s2", 10.0, now=now)
    tracker.record_delay_residual("s1-s2", -10.0, now=now)
    assert tracker.get_jitter_score("s1-s2", now=now) == 0.0


def test_score_rises_with_dispersion():
    tracker = DelayJitterTracker(saturation_ms=100.0, min_samples=3)
    now = 1000.0
    for v in [0.0, 0.0, 0.0]:
        tracker.record_delay_residual("s1-s2", v, now=now)
    stable_score = tracker.get_jitter_score("s1-s2", now=now)
    assert stable_score == pytest.approx(0.0)

    tracker2 = DelayJitterTracker(saturation_ms=100.0, min_samples=3)
    for v in [-100.0, 0.0, 100.0]:
        tracker2.record_delay_residual("s1-s2", v, now=now)
    volatile_score = tracker2.get_jitter_score("s1-s2", now=now)
    assert volatile_score > stable_score


def test_score_saturates_at_one():
    tracker = DelayJitterTracker(saturation_ms=50.0, min_samples=3)
    now = 1000.0
    for v in [-1000.0, 0.0, 1000.0, -800.0, 900.0]:
        tracker.record_delay_residual("s1-s2", v, now=now)
    assert tracker.get_jitter_score("s1-s2", now=now) == 1.0


def test_old_samples_fall_out_of_the_window():
    tracker = DelayJitterTracker(window_seconds=10.0, saturation_ms=100.0, min_samples=3)
    tracker.record_delay_residual("s1-s2", -50.0, now=0.0)
    tracker.record_delay_residual("s1-s2", 0.0, now=0.0)
    tracker.record_delay_residual("s1-s2", 50.0, now=0.0)
    assert tracker.get_jitter_score("s1-s2", now=5.0) > 0.0
    # past the window, those 3 samples are evicted -> back under min_samples
    assert tracker.get_jitter_score("s1-s2", now=15.0) == 0.0


def test_links_are_tracked_independently():
    tracker = DelayJitterTracker(saturation_ms=100.0, min_samples=3)
    now = 1000.0
    for v in [-50.0, 0.0, 50.0]:
        tracker.record_delay_residual("s1-s2", v, now=now)
    assert tracker.get_jitter_score("s1-s2", now=now) > 0.0
    assert tracker.get_jitter_score("s3-s4", now=now) == 0.0


def test_rejects_non_positive_window():
    with pytest.raises(ValueError, match="window_seconds"):
        DelayJitterTracker(window_seconds=0.0)


def test_rejects_non_positive_saturation():
    with pytest.raises(ValueError, match="saturation_ms"):
        DelayJitterTracker(saturation_ms=0.0)


def test_rejects_min_samples_below_two():
    with pytest.raises(ValueError, match="min_samples"):
        DelayJitterTracker(min_samples=1)
