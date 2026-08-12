#!/usr/bin/env python3
"""
Test Graph Builder
"""
import time
import pytest
from src.monitor.network_state import NetworkState
from src.monitor.models import LinkStatistics
from src.routing.graph_builder import GraphBuilder
from datetime import datetime


def _seed_link(state: NetworkState, link_id: str) -> None:
    state.update_link_statistics(
        LinkStatistics(
            timestamp=datetime.now(), link_id=link_id, utilization=0.2,
            rx_mbps=20.0, tx_mbps=18.0, status="up", delay_ms=6.0, packet_loss=0.001,
        )
    )


def test_churned_link_costs_more_than_an_identical_untouched_link():
    """
    delta's cost term used to be a hardcoded-zero "priority" placeholder
    (always inert regardless of its weight). It's now a real link
    instability/churn signal -- this is the test that would have failed
    before that fix, since two links with identical stats always cost
    exactly the same regardless of churn history.
    """
    state = NetworkState()
    _seed_link(state, "0-2")
    _seed_link(state, "4-6")
    builder = GraphBuilder(state)

    graph = builder.build_weighted_graph()
    if not graph.has_edge("0", "2") or not graph.has_edge("4", "6"):
        pytest.skip("GEANT topology doesn't have both test edges; adjust link ids")

    cost_before = builder.get_path_cost(["0", "2"], graph)

    now = time.time()
    state.record_link_churn("0-2", timestamp=now)
    state.record_link_churn("0-2", timestamp=now)
    graph_after = builder.build_weighted_graph()
    cost_churned = builder.get_path_cost(["0", "2"], graph_after)
    cost_untouched = builder.get_path_cost(["4", "6"], graph_after)

    assert cost_churned > cost_before
    assert cost_churned > cost_untouched
