#!/usr/bin/env python3
"""
Test Loss Jitter Tracker
"""
import pytest
from src.monitor.loss_jitter_tracker import LossJitterTracker


def test_no_samples_recorded_is_zero_score():
    tracker = LossJitterTracker()
    assert tracker.get_jitter_score("s1-s2") == 0.0


def test_fewer_than_min_samples_is_zero_score():
    tracker = LossJitterTracker(min_samples=3)
    now = 1000.0
    tracker.record_loss_residual("s1-s2", 0.05, now=now)
    tracker.record_loss_residual("s1-s2", -0.05, now=now)
    assert tracker.get_jitter_score("s1-s2", now=now) == 0.0


def test_score_rises_with_dispersion():
    tracker = LossJitterTracker(saturation=0.2, min_samples=3)
    now = 1000.0
    for v in [0.0, 0.0, 0.0]:
        tracker.record_loss_residual("s1-s2", v, now=now)
    stable_score = tracker.get_jitter_score("s1-s2", now=now)
    assert stable_score == pytest.approx(0.0)

    tracker2 = LossJitterTracker(saturation=0.2, min_samples=3)
    for v in [-0.2, 0.0, 0.2]:
        tracker2.record_loss_residual("s1-s2", v, now=now)
    volatile_score = tracker2.get_jitter_score("s1-s2", now=now)
    assert volatile_score > stable_score


def test_score_saturates_at_one():
    tracker = LossJitterTracker(saturation=0.1, min_samples=3)
    now = 1000.0
    for v in [-1.0, 0.0, 1.0, -0.8, 0.9]:
        tracker.record_loss_residual("s1-s2", v, now=now)
    assert tracker.get_jitter_score("s1-s2", now=now) == 1.0


def test_old_samples_fall_out_of_the_window():
    tracker = LossJitterTracker(window_seconds=10.0, saturation=0.2, min_samples=3)
    tracker.record_loss_residual("s1-s2", -0.1, now=0.0)
    tracker.record_loss_residual("s1-s2", 0.0, now=0.0)
    tracker.record_loss_residual("s1-s2", 0.1, now=0.0)
    assert tracker.get_jitter_score("s1-s2", now=5.0) > 0.0
    # past the window, those 3 samples are evicted -> back under min_samples
    assert tracker.get_jitter_score("s1-s2", now=15.0) == 0.0


def test_links_are_tracked_independently():
    tracker = LossJitterTracker(saturation=0.2, min_samples=3)
    now = 1000.0
    for v in [-0.1, 0.0, 0.1]:
        tracker.record_loss_residual("s1-s2", v, now=now)
    assert tracker.get_jitter_score("s1-s2", now=now) > 0.0
    assert tracker.get_jitter_score("s3-s4", now=now) == 0.0


def test_rejects_non_positive_window():
    with pytest.raises(ValueError, match="window_seconds"):
        LossJitterTracker(window_seconds=0.0)


def test_rejects_non_positive_saturation():
    with pytest.raises(ValueError, match="saturation"):
        LossJitterTracker(saturation=0.0)


def test_rejects_min_samples_below_two():
    with pytest.raises(ValueError, match="min_samples"):
        LossJitterTracker(min_samples=1)


def test_abnormal_loss_score_zero_with_fewer_than_two_samples_per_half():
    tracker = LossJitterTracker()
    now = 1000.0
    for v in [0.1, 0.1, 0.1]:  # half=1 -- not enough for a baseline std
        tracker.record_loss_residual("s1-s2", v, now=now)
    assert tracker.get_abnormal_loss_score("s1-s2", now=now) == 0.0


def test_abnormal_loss_score_zero_with_no_shift():
    tracker = LossJitterTracker()
    now = 1000.0
    # Identical pattern repeated in both halves -- baseline and recent means match exactly.
    for v in [0.05, 0.06, 0.05, 0.06, 0.05, 0.06, 0.05, 0.06]:
        tracker.record_loss_residual("s1-s2", v, now=now)
    assert tracker.get_abnormal_loss_score("s1-s2", now=now) == pytest.approx(0.0, abs=1e-9)


def test_abnormal_loss_score_rises_with_a_real_upward_shift():
    tracker = LossJitterTracker()
    now = 1000.0
    for v in [0.01, 0.01, 0.01, 0.30, 0.30, 0.30]:  # stable baseline, then a real jump
        tracker.record_loss_residual("s1-s2", v, now=now)
    assert tracker.get_abnormal_loss_score("s1-s2", now=now) > 0.0


def test_abnormal_loss_score_ignores_downward_shift():
    """A link doing *better* than its own baseline isn't abnormal -- floored at 0."""
    tracker = LossJitterTracker()
    now = 1000.0
    for v in [0.30, 0.30, 0.30, 0.01, 0.01, 0.01]:
        tracker.record_loss_residual("s1-s2", v, now=now)
    assert tracker.get_abnormal_loss_score("s1-s2", now=now) == 0.0


def test_abnormal_loss_score_maxes_out_for_shift_from_a_perfectly_stable_baseline():
    """baseline_std ~= 0 must not divide-blow-up -- any real positive shift reads as maximal."""
    tracker = LossJitterTracker()
    now = 1000.0
    for v in [0.05, 0.05, 0.05, 0.20, 0.20, 0.20]:
        tracker.record_loss_residual("s1-s2", v, now=now)
    assert tracker.get_abnormal_loss_score("s1-s2", now=now) == 1.0


def test_abnormal_loss_score_saturates_at_sigma_cap():
    tracker = LossJitterTracker()
    now = 1000.0
    for v in [0.10, 0.11, 0.09, 5.0, 5.0, 5.0]:  # shift vastly exceeds 3 baseline std devs
        tracker.record_loss_residual("s1-s2", v, now=now)
    assert tracker.get_abnormal_loss_score("s1-s2", now=now) == 1.0


def test_abnormal_loss_score_and_jitter_score_are_independent_signals():
    """Oscillating around a flat mean: real dispersion (jitter fires) but no net shift
    between the window's two halves (abnormal-loss doesn't) -- the two scores measure
    genuinely different things, not the same statistic twice."""
    tracker = LossJitterTracker(saturation=0.05)
    now = 1000.0
    # 4+4 so each half is itself symmetric (equal -0.1/0.1 count) -- a real net shift
    # would show up here as a genuine confound, not a rounding artifact.
    for v in [-0.1, 0.1, -0.1, 0.1, -0.1, 0.1, -0.1, 0.1]:
        tracker.record_loss_residual("s1-s2", v, now=now)
    assert tracker.get_jitter_score("s1-s2", now=now) > 0.0
    assert tracker.get_abnormal_loss_score("s1-s2", now=now) == pytest.approx(0.0, abs=1e-9)
