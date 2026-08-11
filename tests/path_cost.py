#!/usr/bin/env python3
"""
Test Path Cost
"""
import pytest
from src.monitor.network_state import NetworkState
from src.decision.path_cost import PathCost


def test_initialization():
    state = NetworkState()
    cost = PathCost(state)
    assert cost is not None


def test_empty_path():
    state = NetworkState()
    cost = PathCost(state)
    result = cost.calculate_path_cost([])
    assert result == float('inf')
