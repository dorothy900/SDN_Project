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
    # what offered_load_mbps directly modifies.
    state.update_link_statistics(
        LinkStatistics(
            timestamp=datetime.now(), link_id=link_id, utilization=utilization,
            rx_mbps=0.0, tx_mbps=0.0, status="up", delay_ms=None, packet_loss=None,
        )
    )


# "0-2" has no real published GEANT LinkLabel -> falls back to 100 Mbps;
# "4-6" has a real label -> resolves to 150 Mbps (see
# src/monitor/link_capacity.py). Deliberately using one of each here so
# these tests exercise real per-edge capacity, not a single assumed value.
def test_offered_load_mbps_increases_this_paths_cost():
    """
    Fixed 2026-08-12 ("self-influence / offered-load accounting", logged as
    a known limitation since 2026-08-11): a path's cost should go up if
    pricing it as though a specific flow's own bandwidth demand were already
    routed across it. Reworked 2026-08-20 to take raw Mbps, converted
    per-edge via that edge's own real capacity (0-2 is 100 Mbps here, so
    20 Mbps -> a 0.2 utilization bump, matching the old fixed-fraction test).
    """
    state = NetworkState()
    _seed_link(state, "0-2", utilization=0.3)
    cost = PathCost(state)
    graph = cost.graph_builder.build_weighted_graph()
    if not graph.has_edge("0", "2"):
        pytest.skip("GEANT topology doesn't have test edge 0-2; adjust link id")

    baseline = cost.calculate_path_cost(["0", "2"])
    loaded = cost.calculate_path_cost(["0", "2"], offered_load_mbps=20.0)

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
    with_load = cost.compare_paths(["0", "2"], ["4", "6"], offered_load_mbps=30.0)

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

    # This flow itself would add 0.5 utilization to "4-6" (150 Mbps -> 75 Mbps).
    with_load = cost.compare_paths(
        ["0", "2"], ["4", "6"], min_abs_reduction=0.05, min_rel_reduction=0.5,
        offered_load_mbps=75.0,
    )
    assert with_load["accepted"] is False


def test_offered_load_mbps_clamps_at_one_not_above():
    state = NetworkState()
    _seed_link(state, "0-2", utilization=0.9)
    cost = PathCost(state)
    graph = cost.graph_builder.build_weighted_graph()
    if not graph.has_edge("0", "2"):
        pytest.skip("GEANT topology doesn't have test edge 0-2; adjust link id")

    loaded = cost.calculate_path_cost(["0", "2"], offered_load_mbps=50.0)
    # 50/100 = 0.5 bump; utilization would be 1.4 uncapped; clamped at 1.0
    assert loaded == pytest.approx(cost.weights["alpha"] * 1.0 + 0.001)


def test_offered_load_not_double_counted_on_edges_shared_with_current_path():
    """
    Found 2026-08-20 (multi-pair robustness check): a candidate that reuses
    part of the current path (a real, common case -- rerouting around one
    congested hop while keeping the rest of the route) was getting this
    flow's own future load bumped onto those *shared* edges too, even
    though they already carry the flow's real, current contribution today.
    That double-counted the correction and could make a genuinely-better
    candidate look worse than the current path outright.
    """
    state = NetworkState()
    _seed_link(state, "0-2", utilization=0.85)  # only on current path (congested)
    _seed_link(state, "2-4", utilization=0.20)  # shared by both paths
    _seed_link(state, "4-6", utilization=0.05)  # only on candidate path
    cost = PathCost(state)
    graph = cost.graph_builder.build_weighted_graph()
    if not all(graph.has_edge(u, v) for u, v in (("0", "2"), ("2", "4"), ("4", "6"))):
        pytest.skip("GEANT topology doesn't have all three test edges; adjust link ids")

    old_path = ["0", "2", "4"]
    new_path = ["2", "4", "6"]  # shares edge "2-4" with old_path

    comparison = cost.compare_paths(old_path, new_path, offered_load_mbps=40.0)
    expected_new_cost = cost.calculate_path_cost(
        new_path, offered_load_mbps=40.0, exclude_edges=frozenset({"2-4"})
    )
    double_counted_new_cost = cost.calculate_path_cost(new_path, offered_load_mbps=40.0)

    assert comparison["new_cost"] == pytest.approx(round(expected_new_cost, 6))
    assert comparison["new_cost"] < double_counted_new_cost
