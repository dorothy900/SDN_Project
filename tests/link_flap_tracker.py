#!/usr/bin/env python3
"""
Test Link Flap Tracker
"""
import pytest
from src.monitor.link_flap_tracker import LinkFlapTracker


def test_no_transitions_recorded_is_zero_score():
    tracker = LinkFlapTracker()
    assert tracker.get_flap_score("s1-s2") == 0.0


def test_score_rises_with_recorded_transitions():
    tracker = LinkFlapTracker(half_life_seconds=20.0, penalty_per_flap=1000.0, suppress_threshold=2000.0)
    now = 1000.0
    tracker.record_transition("s1-s2", now=now)
    assert tracker.get_flap_score("s1-s2", now=now) == pytest.approx(0.5)


def test_two_flaps_in_quick_succession_reach_full_suppression():
    tracker = LinkFlapTracker(half_life_seconds=20.0, penalty_per_flap=1000.0, suppress_threshold=2000.0)
    now = 1000.0
    tracker.record_transition("s1-s2", now=now)
    tracker.record_transition("s1-s2", now=now)  # zero elapsed time -- no decay between them
    assert tracker.get_flap_score("s1-s2", now=now) == 1.0


def test_suppression_has_hysteresis_not_just_a_step_function():
    """Once suppressed, the score must stay at 1.0 until penalty decays under
    reuse_threshold specifically -- not just back under suppress_threshold."""
    tracker = LinkFlapTracker(
        half_life_seconds=20.0, penalty_per_flap=1000.0, suppress_threshold=2000.0, reuse_threshold=750.0,
    )
    now = 1000.0
    tracker.record_transition("s1-s2", now=now)
    tracker.record_transition("s1-s2", now=now)  # penalty=2000, exactly at suppress_threshold
    assert tracker.get_flap_score("s1-s2", now=now) == 1.0

    # One half-life later, penalty has decayed to 1000 -- back under suppress_threshold,
    # but still above reuse_threshold=750, so it must still read as fully suppressed.
    assert tracker.get_flap_score("s1-s2", now=now + 20.0) == 1.0

    # Enough further decay to fall under reuse_threshold -- now genuinely reusable.
    assert tracker.get_flap_score("s1-s2", now=now + 60.0) < 1.0


def test_penalty_decays_toward_zero_with_no_further_flaps():
    tracker = LinkFlapTracker(half_life_seconds=20.0, penalty_per_flap=1000.0, suppress_threshold=2000.0)
    now = 1000.0
    tracker.record_transition("s1-s2", now=now)
    score_immediate = tracker.get_flap_score("s1-s2", now=now)
    score_after_one_half_life = tracker.get_flap_score("s1-s2", now=now + 20.0)
    assert score_after_one_half_life == pytest.approx(score_immediate / 2, rel=1e-6)


def test_links_are_tracked_independently():
    tracker = LinkFlapTracker()
    now = 1000.0
    tracker.record_transition("s1-s2", now=now)
    assert tracker.get_flap_score("s1-s2", now=now) > 0.0
    assert tracker.get_flap_score("s3-s4", now=now) == 0.0


def test_rejects_non_positive_half_life():
    with pytest.raises(ValueError, match="half_life_seconds"):
        LinkFlapTracker(half_life_seconds=0.0)


def test_rejects_non_positive_penalty_per_flap():
    with pytest.raises(ValueError, match="penalty_per_flap"):
        LinkFlapTracker(penalty_per_flap=0.0)


def test_rejects_non_positive_suppress_threshold():
    with pytest.raises(ValueError, match="suppress_threshold"):
        LinkFlapTracker(suppress_threshold=0.0)


def test_rejects_reuse_threshold_not_below_suppress_threshold():
    with pytest.raises(ValueError, match="reuse_threshold"):
        LinkFlapTracker(suppress_threshold=2000.0, reuse_threshold=2000.0)


def test_reset_clears_all_links():
    tracker = LinkFlapTracker()
    tracker.record_transition("s1-s2", now=0.0)
    tracker.reset()
    assert tracker.get_flap_score("s1-s2", now=0.0) == 0.0
