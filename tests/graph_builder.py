#!/usr/bin/env python3
"""
Test Graph Builder
"""
import time
import networkx as nx
import pytest
from src.monitor.network_state import NetworkState
from src.monitor.models import LinkStatistics
from src.routing.congestion_model import predicted_delay_ms, predicted_loss
from src.routing.graph_builder import GraphBuilder
from datetime import datetime


def _seed_link(state: NetworkState, link_id: str, now=None) -> None:
    state.update_link_statistics(
        LinkStatistics(
            timestamp=datetime.now(), link_id=link_id, utilization=0.2,
            rx_mbps=20.0, tx_mbps=18.0, status="up", delay_ms=6.0, packet_loss=0.001,
        ),
        now=now,
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


def test_resilience_avoidance_disabled_by_default():
    """resilience_avoid_threshold defaults to None -- an edge with a maxed-out
    resilience_score must be priced exactly as normal unless a caller opts in."""
    state = NetworkState()
    now = 1000.0
    _seed_link(state, "0-2", now=now)
    for v in [0.0, 0.0, 0.0, 0.0, 0.5, 0.5, 0.5, 0.5]:  # clean baseline, then sustained shift
        state.record_loss_residual("0-2", v, now=now)
    builder = GraphBuilder(state)
    graph = builder.build_weighted_graph(now=now)
    if not graph.has_edge("0", "2"):
        pytest.skip("GEANT topology doesn't have test edge 0-2; adjust link id")
    assert graph.has_edge("0", "2")
    assert graph["0"]["2"]["weight"] < GraphBuilder.RESILIENCE_AVOID_PENALTY
    assert graph["0"]["2"]["resilience_avoided"] is False


def test_resilience_avoidance_prices_anomalous_edge_high_without_removing_it():
    """Enabled: an anomalous link stays in the graph (so a demand with no
    alternative is never black-holed) but carries a penalty large enough that
    any loop-free alternative wins."""
    state = NetworkState()
    now = 1000.0
    _seed_link(state, "0-2", now=now)
    _seed_link(state, "4-6", now=now)
    # Push "0-2" well past a real anomaly: a clean baseline, then a sustained
    # upward shift across the whole recent half (not a single outlier).
    for v in [0.0, 0.0, 0.0, 0.0, 0.5, 0.5, 0.5, 0.5]:
        state.record_loss_residual("0-2", v, now=now)
    builder = GraphBuilder(state, resilience_avoid_threshold=0.7)
    graph = builder.build_weighted_graph(now=now)
    if not graph.has_edge("0", "2") or not graph.has_edge("4", "6"):
        pytest.skip("GEANT topology doesn't have both test edges; adjust link ids")

    assert graph.has_edge("0", "2")  # not removed
    assert graph["0"]["2"]["resilience_avoided"] is True
    assert graph["0"]["2"]["weight"] >= GraphBuilder.RESILIENCE_AVOID_PENALTY
    assert graph["4"]["6"]["weight"] < GraphBuilder.RESILIENCE_AVOID_PENALTY


def test_resilience_avoidance_hysteresis_does_not_toggle_near_threshold():
    """Once latched, an edge stays avoided while its score decays back through
    avoid_threshold -- it only releases below the reuse band. A raw >= compare
    would toggle it out here, re-creating path churn."""
    state = NetworkState()
    _seed_link(state, "0-2", now=0.0)
    builder = GraphBuilder(state, resilience_avoid_threshold=0.6)
    gate = builder._resilience_gate

    # Two quick real flaps -> flap penalty ~0.98 of suppress -> latched avoided.
    state.set_link_status("0-2", is_up=True, now=0.0)
    state.set_link_status("0-2", is_up=False, now=1.0)
    state.set_link_status("0-2", is_up=True, now=2.0)
    graph = builder.build_weighted_graph(now=2.0)
    if not graph.has_edge("0", "2"):
        pytest.skip("GEANT topology doesn't have test edge 0-2; adjust link id")
    assert gate.is_avoided("0-2")

    # ~30s later the exponential penalty has decayed under avoid_threshold (0.6)
    # but not under reuse_threshold (0.375 * 0.6 = 0.225).
    later = 32.0
    score_later = state.get_resilience_score("0-2", now=later)
    assert gate.reuse_threshold <= score_later < 0.6
    graph = builder.build_weighted_graph(now=later)
    assert graph["0"]["2"]["resilience_avoided"] is True  # still latched, no toggle

    # Far enough out that the penalty finally falls under the reuse band: released.
    much_later = 90.0
    assert state.get_resilience_score("0-2", now=much_later) < gate.reuse_threshold
    graph = builder.build_weighted_graph(now=much_later)
    assert graph["0"]["2"]["resilience_avoided"] is False


def test_resilience_gate_gives_up_when_avoidance_is_too_expensive():
    """Bounded downside: if the cheapest anomaly-free route costs more than
    RESILIENCE_MAX_DETOUR_FACTOR x the cheapest route overall, the gate routes
    on unpenalised costs instead of forcing the flow onto a far worse path."""
    state = NetworkState()
    now = 1000.0
    _seed_link(state, "0-2", now=now)
    for v in [0.0, 0.0, 0.0, 0.0, 0.5, 0.5, 0.5, 0.5]:
        state.record_loss_residual("0-2", v, now=now)
    if "0" not in state.get_active_graph() or not state.get_active_graph().has_edge("0", "2"):
        pytest.skip("GEANT topology doesn't have test edge 0-2; adjust link id")
    builder = GraphBuilder(state, resilience_avoid_threshold=0.7)

    # Generous cap: the detour around the direct 0-2 link is affordable -> avoidance holds.
    builder.RESILIENCE_MAX_DETOUR_FACTOR = 1000.0
    g = builder.resilience_effective_graph("0", "2", now=now)
    assert nx.shortest_path(g, "0", "2", weight="weight") != ["0", "2"]

    # Tight cap: any detour off the 1-hop direct link is "too expensive" -> give up.
    builder.RESILIENCE_MAX_DETOUR_FACTOR = 1.01
    g = builder.resilience_effective_graph("0", "2", now=now)
    assert nx.shortest_path(g, "0", "2", weight="weight") == ["0", "2"]
    assert g["0"]["2"]["weight"] < GraphBuilder.RESILIENCE_AVOID_PENALTY
