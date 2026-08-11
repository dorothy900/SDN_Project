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
