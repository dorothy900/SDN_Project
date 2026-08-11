#!/usr/bin/env python3
"""
Test Decision Engine

Focused, fast unit tests for src/decision/decision_engine.py -- the class
every other decision/stability submodule already has its own test file for
(test_change_budget.py, test_path_cost.py, test_persistence_checker.py,
test_stability_manager.py), but DecisionEngine itself did not, despite being
the class that orchestrates all of them and gaining the most new behavior
this pass (emergency failure bypass, recovery-window switch-back,
priority-aware congestion evaluation).
"""
from __future__ import annotations

from pathlib import Path

from experiments.simulation_common import PRIMARY_PAIR, build_network_state, link_id, set_link_condition
from src.decision.decision_engine import DecisionEngine
from src.decision.threshold_detector import ThresholdViolation
from src.routing.graph_builder import GraphBuilder


def _make_engine(tmp_path: Path, persistence_required_samples: int = 3):
    state = build_network_state(Path(tmp_path), seed=0)
    engine = DecisionEngine(state)
    engine.persistence_checker.required_samples = persistence_required_samples
    engine.persistence_checker.persistence_seconds = 0.0
    engine.persistence_checker.cooldown_seconds = 0.0

    src, dst = PRIMARY_PAIR
    current_path = engine.path_cost.find_best_path(src, dst)
    engine.current_paths[(src, dst)] = list(current_path)

    candidates = GraphBuilder(state).get_candidate_paths(src, dst, max_paths=3)
    candidate_path = next(p for p in candidates if p != current_path)

    return engine, state, src, dst, current_path, candidate_path


def _make_candidate_clearly_better(state, current_path, candidate_path):
    """Congest every link on current_path and clear every link on candidate_path."""
    for u, v in zip(current_path, current_path[1:]):
        set_link_condition(state, link_id(u, v), utilization=0.90, delay_bump_ms=20.0, loss_bump=0.02)
    for u, v in zip(candidate_path, candidate_path[1:]):
        set_link_condition(state, link_id(u, v), utilization=0.05, delay_bump_ms=0.0, loss_bump=0.0)


def test_initialization(tmp_path):
    engine, *_ = _make_engine(tmp_path)
    assert engine.stability is not None
    assert engine.failure_handler is not None
    assert engine.recovery_manager is not None
    assert engine.traffic_policy is not None


def test_evaluate_pair_requires_persistence_before_rerouting(tmp_path):
    engine, state, src, dst, current_path, candidate_path = _make_engine(tmp_path, persistence_required_samples=3)
    _make_candidate_clearly_better(state, current_path, candidate_path)
    hotspot_link = link_id(current_path[0], current_path[1])
    violation = ThresholdViolation(link_id=hotspot_link, metric="utilization", value=0.90, threshold=0.7, severity=0.2)

    first = engine.evaluate_pair(src, dst, current_path, candidate_path, violation, now=0.0)
    second = engine.evaluate_pair(src, dst, current_path, candidate_path, violation, now=2.0)
    assert first is None
    assert second is None

    third = engine.evaluate_pair(src, dst, current_path, candidate_path, violation, now=4.0)
    assert third is not None
    assert third["new_path"] == candidate_path


def test_evaluate_failure_bypasses_persistence(tmp_path):
    # A persistence requirement no single call could ever satisfy on its own.
    engine, state, src, dst, current_path, candidate_path = _make_engine(tmp_path, persistence_required_samples=5)
    failed_link = link_id(current_path[0], current_path[1])
    state.set_link_status(failed_link, is_up=False)

    action = engine.evaluate_failure(src, dst, current_path, failed_link, candidate_path, now=0.0)

    assert action is not None
    assert action["new_path"] == candidate_path
    assert engine.failure_handler.is_link_failed(failed_link)


def test_evaluate_failure_ignores_unaffected_link(tmp_path):
    engine, state, src, dst, current_path, candidate_path = _make_engine(tmp_path)
    unrelated_link = link_id(candidate_path[0], candidate_path[1])
    if unrelated_link in {link_id(u, v) for u, v in zip(current_path, current_path[1:])}:
        return  # topology coincidence; nothing to assert here

    action = engine.evaluate_failure(src, dst, current_path, unrelated_link, candidate_path, now=0.0)
    assert action is None


def test_recovery_switchback_waits_for_the_window(tmp_path):
    engine, *_rest, current_path, candidate_path = _make_engine(tmp_path)
    src, dst = PRIMARY_PAIR
    failed_link = link_id(current_path[0], current_path[1])

    engine.begin_recovery_watch(src, dst, failed_link, original_path=current_path, now=0.0)

    too_soon = engine.evaluate_recovery_switchback(src, dst, candidate_path, now=1.0)
    assert too_soon is None

    window = engine.recovery_manager.recovery_window_seconds
    after_window = engine.evaluate_recovery_switchback(src, dst, candidate_path, now=window + 0.1)
    assert after_window is not None
    assert after_window["new_path"] == current_path


def test_invalidate_recovery_cancels_pending_switchback(tmp_path):
    engine, *_rest, current_path, candidate_path = _make_engine(tmp_path)
    src, dst = PRIMARY_PAIR
    failed_link = link_id(current_path[0], current_path[1])

    engine.begin_recovery_watch(src, dst, failed_link, original_path=current_path, now=0.0)
    engine.invalidate_recovery(src, dst)

    window = engine.recovery_manager.recovery_window_seconds
    result = engine.evaluate_recovery_switchback(src, dst, candidate_path, now=window + 10.0)
    assert result is None


def test_priority_service_skips_persistence_low_priority_does_not(tmp_path):
    engine, state, src, dst, current_path, candidate_path = _make_engine(tmp_path, persistence_required_samples=3)
    _make_candidate_clearly_better(state, current_path, candidate_path)
    hotspot_link = link_id(current_path[0], current_path[1])

    # High priority (VoIP, config/policies.yaml reroute_immediate=True): one
    # sample above its (lower) effective threshold is enough.
    voip_action = engine.evaluate_service_congestion(
        src, dst, current_path, candidate_path, hotspot_link, utilization=0.90,
        service_type="VoIP", now=0.0,
    )
    assert voip_action is not None
    assert voip_action["new_path"] == candidate_path

    # Reset state for a clean second comparison.
    engine2, state2, src2, dst2, current_path2, candidate_path2 = _make_engine(tmp_path, persistence_required_samples=3)
    _make_candidate_clearly_better(state2, current_path2, candidate_path2)
    hotspot_link2 = link_id(current_path2[0], current_path2[1])

    # Low priority (File Transfer, reroute_immediate=False): a single sample
    # is not enough, even at the same utilization.
    file_transfer_action = engine2.evaluate_service_congestion(
        src2, dst2, current_path2, candidate_path2, hotspot_link2, utilization=0.90,
        service_type="File Transfer", now=0.0,
    )
    assert file_transfer_action is None
