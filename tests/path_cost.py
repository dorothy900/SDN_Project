#!/usr/bin/env python3
"""
Test Path Cost
"""
from datetime import datetime

import pytest
from src.monitor.network_state import NetworkState
from src.monitor.models import LinkStatistics
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


def _seed_link(state: NetworkState, link_id: str, utilization: float) -> None:
    # delay_ms/packet_loss left None so beta/gamma's residual terms stay
    # exactly 0 -- isolates these tests to alpha's utilization term, which is
    # what offered_load_utilization directly modifies.
    state.update_link_statistics(
        LinkStatistics(
            timestamp=datetime.now(), link_id=link_id, utilization=utilization,
            rx_mbps=0.0, tx_mbps=0.0, status="up", delay_ms=None, packet_loss=None,
        )
    )


def test_offered_load_utilization_increases_this_paths_cost():
    """
    Fixed 2026-08-12 ("self-influence / offered-load accounting", logged as
    a known limitation since 2026-08-11): a path's cost should go up if
    pricing it as though a specific flow's own bandwidth demand were already
    routed across it.
    """
    state = NetworkState()
    _seed_link(state, "0-2", utilization=0.3)
    cost = PathCost(state)
    graph = cost.graph_builder.build_weighted_graph()
    if not graph.has_edge("0", "2"):
        pytest.skip("GEANT topology doesn't have test edge 0-2; adjust link id")

    baseline = cost.calculate_path_cost(["0", "2"])
    loaded = cost.calculate_path_cost(["0", "2"], offered_load_utilization=0.2)

    assert loaded > baseline
    # delay_ms/packet_loss are None here, so only alpha*utilization moves.
    assert loaded == pytest.approx(baseline + cost.weights["alpha"] * 0.2)


def test_compare_paths_applies_offered_load_to_new_path_only():
    """
    The whole point of the fix: the *current* path already really carries
    this flow, so its cost shouldn't change; only the candidate (which
    doesn't carry it yet) should be costed as if it will.
    """
    state = NetworkState()
    _seed_link(state, "0-2", utilization=0.2)
    _seed_link(state, "4-6", utilization=0.2)
    cost = PathCost(state)
    graph = cost.graph_builder.build_weighted_graph()
    if not graph.has_edge("0", "2") or not graph.has_edge("4", "6"):
        pytest.skip("GEANT topology doesn't have both test edges; adjust link ids")

    without = cost.compare_paths(["0", "2"], ["4", "6"])
    with_load = cost.compare_paths(["0", "2"], ["4", "6"], offered_load_utilization=0.3)

    assert with_load["old_cost"] == without["old_cost"]
    assert with_load["new_cost"] > without["new_cost"]


def test_offered_load_can_flip_the_accept_decision():
    """
    A candidate that looks like a strong win when its own future load is
    ignored can stop looking like one once that load is correctly priced in
    -- this is the actual bug: candidates looked artificially cheap.
    """
    state = NetworkState()
    _seed_link(state, "0-2", utilization=0.30)  # current path
    _seed_link(state, "4-6", utilization=0.05)  # candidate path
    cost = PathCost(state)
    graph = cost.graph_builder.build_weighted_graph()
    if not graph.has_edge("0", "2") or not graph.has_edge("4", "6"):
        pytest.skip("GEANT topology doesn't have both test edges; adjust link ids")

    without = cost.compare_paths(
        ["0", "2"], ["4", "6"], min_abs_reduction=0.05, min_rel_reduction=0.5
    )
    assert without["accepted"] is True

    # This flow itself would add 0.5 utilization to whatever it's routed onto.
    with_load = cost.compare_paths(
        ["0", "2"], ["4", "6"], min_abs_reduction=0.05, min_rel_reduction=0.5,
        offered_load_utilization=0.5,
    )
    assert with_load["accepted"] is False


def test_offered_load_utilization_clamps_at_one_not_above():
    state = NetworkState()
    _seed_link(state, "0-2", utilization=0.9)
    cost = PathCost(state)
    graph = cost.graph_builder.build_weighted_graph()
    if not graph.has_edge("0", "2"):
        pytest.skip("GEANT topology doesn't have test edge 0-2; adjust link id")

    loaded = cost.calculate_path_cost(["0", "2"], offered_load_utilization=0.5)
    # utilization would be 1.4 uncapped; clamped at 1.0 -> alpha*1.0 + 0.001
    assert loaded == pytest.approx(cost.weights["alpha"] * 1.0 + 0.001)
