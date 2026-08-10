#!/usr/bin/env python3
"""
Test Stability Manager
"""
import pytest
import time
from src.stability.stability_manager import StabilityManager


def test_initialization():
    manager = StabilityManager()
    assert manager is not None


def test_hold_down():
    manager = StabilityManager(hold_down_seconds=1)
    pair = ("h1", "h2")
    manager.start_hold_down(pair)
    assert manager.is_in_hold_down(pair) is True
    time.sleep(1.1)
    assert manager.is_in_hold_down(pair) is False


def test_rejects_release_threshold_above_enter_threshold():
    # release > enter lets the state release while still above the enter
    # point, flapping instead of damping -- must be caught at construction.
    with pytest.raises(ValueError, match="release_threshold"):
        StabilityManager(enter_threshold=0.70, release_threshold=0.80)


def test_rejects_negative_hold_down():
    with pytest.raises(ValueError, match="hold_down_seconds"):
        StabilityManager(hold_down_seconds=-1)


def test_rejects_threshold_outside_unit_range():
    with pytest.raises(ValueError, match="enter_threshold"):
        StabilityManager(enter_threshold=1.5, release_threshold=0.65)
