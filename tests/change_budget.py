#!/usr/bin/env python3
"""
Test Change Budget
"""
import pytest
import time
from src.decision.change_budget import ChangeBudget


def test_initialization():
    budget = ChangeBudget()
    assert budget is not None


def test_budget_limits():
    budget = ChangeBudget(max_path_changes_per_minute=2)
    assert budget.can_change_path() is True
    budget.record_path_change()
    assert budget.can_change_path() is True
    budget.record_path_change()
    assert budget.can_change_path() is False


def test_rejects_negative_limits():
    with pytest.raises(ValueError, match="max_updates_per_minute"):
        ChangeBudget(max_updates_per_minute=-1)


def test_rejects_path_change_budget_exceeding_update_budget():
    # A path change is itself an update, so its own budget cannot be looser.
    with pytest.raises(ValueError, match="max_path_changes_per_minute"):
        ChangeBudget(max_updates_per_minute=2, max_path_changes_per_minute=5)
