#!/usr/bin/env python3
"""
Test Persistence Checker
"""
import pytest
import time
from src.decision.persistence_checker import PersistenceChecker


def test_initialization():
    checker = PersistenceChecker()
    assert checker is not None


def test_cooldown():
    checker = PersistenceChecker(cooldown_seconds=1)
    checker.record_reroute("link1", "utilization")
    assert checker.in_cooldown("link1", "utilization") is True
    time.sleep(1.1)
    assert checker.in_cooldown("link1", "utilization") is False


def test_leak_window_forgives_one_violation_not_the_whole_window():
    checker = PersistenceChecker(persistence_seconds=0.0, required_samples=5)
    for _ in range(3):
        checker.record_violation("link1", "utilization", 0.9)
    assert checker.get_violation_count("link1", "utilization") == 3

    checker.leak_window("link1", "utilization")
    # A 1:1 leak decrements by exactly one -- not a full clear_window() wipe.
    assert checker.get_violation_count("link1", "utilization") == 2


def test_leak_window_lets_chronic_intermittent_violations_still_trip_persistence():
    checker = PersistenceChecker(persistence_seconds=0.0, required_samples=3)
    # Violates more often than it recovers: net accumulation should still climb
    # past required_samples, even with an occasional clean sample leaking one off.
    checker.record_violation("link1", "utilization", 0.9)
    checker.record_violation("link1", "utilization", 0.9)
    checker.leak_window("link1", "utilization")
    checker.record_violation("link1", "utilization", 0.9)
    checker.record_violation("link1", "utilization", 0.9)
    accepted = checker.record_violation("link1", "utilization", 0.9)

    assert accepted is True
    assert checker.get_violation_count("link1", "utilization") >= 3


def test_leak_window_symmetric_pattern_never_trips_persistence():
    checker = PersistenceChecker(persistence_seconds=0.0, required_samples=3)
    # A 50/50 violate/clean pattern nets to zero every cycle -- never chronic
    # enough to trip, matching leak_window()'s "borderline, not worse" intent.
    for _ in range(5):
        checker.record_violation("link1", "utilization", 0.9)
        checker.leak_window("link1", "utilization")

    assert checker.get_violation_count("link1", "utilization") == 0
    assert checker.check_persistence("link1", "utilization") is False


def test_leak_window_on_empty_window_is_a_no_op():
    checker = PersistenceChecker(persistence_seconds=0.0, required_samples=3)
    checker.leak_window("link1", "utilization")  # no active window yet
    assert checker.get_violation_count("link1", "utilization") == 0
