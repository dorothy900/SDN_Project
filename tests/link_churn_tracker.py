#!/usr/bin/env python3
"""
Test Link Churn Tracker
"""
import pytest
from src.monitor.link_churn_tracker import LinkChurnTracker


def test_no_changes_recorded_is_zero_score():
    tracker = LinkChurnTracker()
    assert tracker.get_churn_score("s1-s2") == 0.0


def test_score_rises_with_recorded_changes():
    tracker = LinkChurnTracker(window_seconds=60.0, saturation_count=5)
    now = 1000.0
    tracker.record_change("s1-s2", now=now)
    assert tracker.get_churn_score("s1-s2", now=now) == pytest.approx(1 / 5)
    tracker.record_change("s1-s2", now=now)
    assert tracker.get_churn_score("s1-s2", now=now) == pytest.approx(2 / 5)


def test_score_saturates_at_one():
    tracker = LinkChurnTracker(window_seconds=60.0, saturation_count=5)
    now = 1000.0
    for _ in range(20):
        tracker.record_change("s1-s2", now=now)
    assert tracker.get_churn_score("s1-s2", now=now) == 1.0


def test_old_changes_fall_out_of_the_window():
    tracker = LinkChurnTracker(window_seconds=10.0, saturation_count=5)
    tracker.record_change("s1-s2", now=0.0)
    assert tracker.get_churn_score("s1-s2", now=5.0) == pytest.approx(1 / 5)
    assert tracker.get_churn_score("s1-s2", now=15.0) == 0.0


def test_links_are_tracked_independently():
    tracker = LinkChurnTracker(window_seconds=60.0, saturation_count=5)
    now = 1000.0
    tracker.record_change("s1-s2", now=now)
    assert tracker.get_churn_score("s1-s2", now=now) > 0.0
    assert tracker.get_churn_score("s3-s4", now=now) == 0.0


def test_rejects_non_positive_window():
    with pytest.raises(ValueError, match="window_seconds"):
        LinkChurnTracker(window_seconds=0.0)


def test_rejects_non_positive_saturation_count():
    with pytest.raises(ValueError, match="saturation_count"):
        LinkChurnTracker(saturation_count=0)
