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
