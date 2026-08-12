#!/usr/bin/env python3
"""
Test Graph Builder
"""
import time
import pytest
from src.monitor.network_state import NetworkState
from src.monitor.models import LinkStatistics
from src.routing.congestion_model import predicted_delay_ms, predicted_loss
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


def _seed_link_with(state: NetworkState, link_id: str, utilization: float, delay_ms: float, packet_loss: float) -> None:
    state.update_link_statistics(
        LinkStatistics(
            timestamp=datetime.now(), link_id=link_id, utilization=utilization,
            rx_mbps=20.0, tx_mbps=18.0, status="up", delay_ms=delay_ms, packet_loss=packet_loss,
        )
    )


def test_link_matching_utilization_prediction_contributes_no_extra_delay_loss_cost():
    """
    beta/gamma now price *residuals* against what utilization already
    predicts (fixed 2026-08-12, replacing raw delay_ms/packet_loss -- see
    GraphBuilder._calculate_edge_cost's docstring for why raw values
    double-count the same congestion signal alpha already prices). A link
    whose measured delay/loss exactly match the utilization-based
    prediction should cost the same as if beta/gamma were zero.
    """
    state = NetworkState()
    u = 0.5
    _seed_link_with(state, "0-2", utilization=u, delay_ms=predicted_delay_ms(u), packet_loss=predicted_loss(u))
    builder = GraphBuilder(state)
    graph = builder.build_weighted_graph()
    if not graph.has_edge("0", "2"):
        pytest.skip("GEANT topology doesn't have test edge 0-2; adjust link id")

    cost = builder.get_path_cost(["0", "2"], graph)
    expected = builder.weights["alpha"] * u + 0.001  # delta/epsilon are zero: fresh state, status up
    assert cost == pytest.approx(expected, abs=1e-9)


def test_link_with_anomalous_delay_beyond_prediction_costs_more():
    """A link whose delay is worse than its utilization alone would predict must cost more."""
    state = NetworkState()
    u = 0.5
    _seed_link_with(state, "0-2", utilization=u, delay_ms=predicted_delay_ms(u), packet_loss=predicted_loss(u))
    _seed_link_with(state, "4-6", utilization=u, delay_ms=predicted_delay_ms(u) + 50.0, packet_loss=predicted_loss(u))
    builder = GraphBuilder(state)
    graph = builder.build_weighted_graph()
    if not graph.has_edge("0", "2") or not graph.has_edge("4", "6"):
        pytest.skip("GEANT topology doesn't have both test edges; adjust link ids")

    cost_normal = builder.get_path_cost(["0", "2"], graph)
    cost_anomalous = builder.get_path_cost(["4", "6"], graph)
    assert cost_anomalous > cost_normal


def test_link_performing_better_than_predicted_costs_less_not_just_never_penalized():
    """Residuals are signed (not clamped at zero): beating the utilization-based prediction should lower cost."""
    state = NetworkState()
    u = 0.5
    _seed_link_with(state, "0-2", utilization=u, delay_ms=predicted_delay_ms(u), packet_loss=predicted_loss(u))
    _seed_link_with(state, "4-6", utilization=u, delay_ms=predicted_delay_ms(u) - 4.0, packet_loss=predicted_loss(u))
    builder = GraphBuilder(state)
    graph = builder.build_weighted_graph()
    if not graph.has_edge("0", "2") or not graph.has_edge("4", "6"):
        pytest.skip("GEANT topology doesn't have both test edges; adjust link ids")

    cost_predicted = builder.get_path_cost(["0", "2"], graph)
    cost_better_than_predicted = builder.get_path_cost(["4", "6"], graph)
    assert cost_better_than_predicted < cost_predicted
