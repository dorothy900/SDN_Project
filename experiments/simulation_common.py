#!/usr/bin/env python3
"""
Simulation Common - Shared harness driving the real routing/decision/stability
code for the Stage 6 comparative scenarios.

Every algorithm (static, dynamic, proposed) is driven through its actual
implementation (src/routing, src/decision, src/stability) against a shared,
seeded NetworkState built from the real GEANT topology. No per-algorithm
outcome is hardcoded: reroutes, rule-update counts, and decision timings all
fall out of the real code running against the scenario's traffic/utilization
trace.
"""

from __future__ import annotations

import random
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from src.decision.decision_engine import DecisionEngine
from src.monitor.models import LinkStatistics
from src.monitor.network_state import NetworkState
from src.routing.congestion_model import (
    BASELINE_DELAY_MS,
    BASELINE_LOSS,
    congestion_delay_bump_ms,
    congestion_loss_bump,
)
from src.routing.dynamic_baseline import DynamicBaseline
from src.routing.flow_installer import FlowInstaller
from src.routing.graph_builder import GraphBuilder
from src.routing.static_shortest_path import StaticShortestPath

from .sndlib_demand import FALLBACK_HIGH, FALLBACK_LOW, resolve_edge_demand_baseline, resolve_edge_diversity_baseline

SAMPLE_INTERVAL_S = 2.0  # matches config/topology.yaml monitoring.interval_seconds
LINK_CAPACITY_MBPS = 100.0  # flat fallback, used only when there's nothing real to
                             # look up: an edge with no published GEANT LinkLabel
                             # (resolve_link_capacity_mbps's own fallback), or a
                             # None/degenerate path (resolve_path_capacity_mbps,
                             # which uses the real per-link bottleneck instead
                             # of this flat value whenever real data exists).
BASE_TIMESTAMP = datetime(2026, 8, 24, 12, 0, 0)


def resolve_link_capacity_mbps(link_id_str: str) -> float:
    """
    Real per-link capacity (Mbps) for one GEANT edge. Thin wrapper over
    src.monitor.link_capacity's own resolver, so both PathCost's per-edge
    offered-load correction (production) and this offline harness resolve
    real capacity data the same way -- kept here only to pin this module's
    own LINK_CAPACITY_MBPS default.
    """
    from src.monitor.link_capacity import resolve_link_capacity_mbps as _resolve

    return _resolve(link_id_str, default_mbps=LINK_CAPACITY_MBPS)


def resolve_path_capacity_mbps(path: Optional[Sequence[str]]) -> float:
    """
    Bottleneck real capacity (Mbps) along a path -- the minimum of each
    hop's own real per-link capacity, mirroring how a real network's
    achievable throughput is capped by its narrowest link, not an
    arbitrary flat assumption. None/degenerate path falls back to
    LINK_CAPACITY_MBPS (nothing real to look up).
    """
    if not path or len(path) < 2:
        return LINK_CAPACITY_MBPS
    return min(resolve_link_capacity_mbps(link_id(u, v)) for u, v in zip(path, path[1:]))


# flow-video-1 (h3->h8) maps directly onto GEANT nodes ("2","7"): FlowInstaller's
# hN <-> node(N-1) convention. This pair has 3 distinct, non-trivial candidate
# paths, so it is used as the monitored primary path for every scenario.
PRIMARY_PAIR: Tuple[str, str] = ("2", "7")


def link_id(u: str, v: str) -> str:
    nodes = sorted([str(u), str(v)])
    return "%s-%s" % (nodes[0], nodes[1])


def canonical_edge(edge: Sequence[str]) -> Tuple[str, str]:
    return tuple(sorted(str(node) for node in edge))


def path_hops(path: Optional[Sequence[str]]) -> int:
    return max(len(path) - 1, 0) if path else 0


def build_network_state(output_dir: Path, seed: int = 0) -> NetworkState:
    """
    Seed a deterministic (but run-to-run jittered) GEANT network state.

    Background link baselines: a three-tier fallback, real data first.
      1. resolve_edge_demand_baseline() -- real SNDlib GEANT traffic-demand
         data, mapped by country identity (see sndlib_demand.py).
      2. resolve_edge_diversity_baseline() -- SNDlib's synthetic "nobel-eu"
         reference network, consulted only when tier 1 doesn't cover an
         edge. Explicitly NOT a realism claim (see sndlib_demand.py's tier-2
         section for why nobel-eu isn't real data) -- used only to give a
         few more edges real *structural variation* instead of the flat
         formula, kept strictly separate from tier 1 so a real-data value
         is never overridden or blended with a synthetic one.
      3. Fallback: uniform random draw across [FALLBACK_LOW, FALLBACK_HIGH]
         (the same range tiers 1/2 use), for whatever neither tier above
         covers. A narrower fallback band would create a systematic (not
         random) bias: real demand-calibrated edges between busy hub
         countries legitimately read higher than a narrow band's ceiling,
         so any candidate path touching even one uncovered node would look
         artificially cheap by comparison, deterministically. See
         sndlib_demand.py's FALLBACK_LOW/HIGH docstring.
    """
    rng = random.Random(seed)
    state = NetworkState(output_dir=output_dir)
    for index, edge in enumerate(sorted(state.topology.get_active_links(), key=canonical_edge)):
        lid = link_id(*edge)
        edge_base = resolve_edge_demand_baseline(*edge)
        if edge_base is None:
            edge_base = resolve_edge_diversity_baseline(*edge)
        if edge_base is None:
            edge_base = rng.uniform(FALLBACK_LOW, FALLBACK_HIGH)
        utilization = round(edge_base + rng.uniform(0.0, 0.01), 4)
        state.update_link_statistics(
            LinkStatistics(
                timestamp=BASE_TIMESTAMP + timedelta(seconds=index),
                link_id=lid,
                utilization=utilization,
                rx_mbps=round(20.0 + utilization * 80.0, 4),
                tx_mbps=round(18.0 + utilization * 75.0, 4),
                status="up",
                delay_ms=round(6.0 + (index % 5) * 1.1 + rng.uniform(0.0, 0.3), 4),
                packet_loss=round(0.0008 + (index % 4) * 0.0003, 6),
            )
        )
    return state


def set_link_condition(
    state: NetworkState,
    link_id_str: str,
    utilization: Optional[float] = None,
    status: Optional[str] = None,
    timestamp: Optional[datetime] = None,
    delay_bump_ms: Optional[float] = None,
    loss_bump: Optional[float] = None,
    now: Optional[float] = None,
    delay_ms_override: Optional[float] = None,
    loss_override: Optional[float] = None,
) -> None:
    """
    Overwrite one link's live statistics, preserving whatever isn't specified.

    delay_ms/packet_loss are derived fresh from the *current* utilization via
    congestion_delay_bump_ms()/congestion_loss_bump() by default -- previously
    delay_bump_ms/loss_bump defaulted to 0.0 and every real call site in this
    project only ever passed utilization=, which left delay/loss completely
    flat regardless of how congested a link became. That wasn't realistic
    (utilization/delay/loss aren't independent in a real network -- they're
    different symptoms of the same congestion) and meant beta/gamma (the
    delay/loss cost weights) had no real signal to respond to in any pilot
    scenario. Pass delay_bump_ms/loss_bump explicitly to bypass this and set
    an exact value instead (e.g. injecting an isolated, non-congestion event).

    delay_ms_override/loss_override: set delay/loss to these *exact*
    values, bypassing both the formula and the bump-over-old-value logic
    entirely. For injecting real Mininet-measured (delay, loss) samples
    instead of congestion_model.py's formula -- the formula produces
    delay_residual/loss_residual that are ~0 by construction, which is
    fine for testing the formula's own internal consistency but means any
    residual computed from it carries no real variance to analyze.
    delay_bump_ms/loss_bump still take priority if both are given by
    mistake (checked first, unchanged).
    """
    old = state.get_link_stats(link_id_str)
    resolved_utilization = utilization if utilization is not None else (float(old.utilization) if old else 0.2)
    resolved_status = status if status is not None else (old.status if old else "up")

    if delay_bump_ms is not None or loss_bump is not None:
        base_delay = float(old.delay_ms) if old and old.delay_ms else BASELINE_DELAY_MS
        base_loss = float(old.packet_loss) if old and old.packet_loss else BASELINE_LOSS
        resolved_delay = base_delay + (delay_bump_ms or 0.0)
        resolved_loss = base_loss + (loss_bump or 0.0)
    elif delay_ms_override is not None or loss_override is not None:
        resolved_delay = delay_ms_override if delay_ms_override is not None else (
            BASELINE_DELAY_MS + congestion_delay_bump_ms(resolved_utilization)
        )
        resolved_loss = loss_override if loss_override is not None else (
            BASELINE_LOSS + congestion_loss_bump(resolved_utilization)
        )
    else:
        resolved_delay = BASELINE_DELAY_MS + congestion_delay_bump_ms(resolved_utilization)
        resolved_loss = BASELINE_LOSS + congestion_loss_bump(resolved_utilization)

    state.update_link_statistics(
        LinkStatistics(
            timestamp=timestamp or datetime.now(),
            link_id=link_id_str,
            utilization=resolved_utilization,
            rx_mbps=round(20.0 + resolved_utilization * 80.0, 4),
            tx_mbps=round(18.0 + resolved_utilization * 75.0, 4),
            status=resolved_status,
            delay_ms=round(resolved_delay, 4),
            packet_loss=round(min(0.3, resolved_loss), 6),
        ),
        now=now,
    )
    if status is not None:
        # Forward the synthetic clock: set_link_status feeds LinkFlapTracker's
        # record_transition, whose exponential decay is meaningless if half the
        # events land on now_s and the reads land on real wall-clock time (the
        # same synthetic-clock hazard update_link_statistics' `now` guards
        # against). Harmless for callers that pass no now -- both default to
        # time.time().
        state.set_link_status(link_id_str, is_up=(status == "up"), now=now)


def path_metrics(state: NetworkState, path: Optional[Sequence[str]]) -> Dict[str, object]:
    """Derive aggregate delay/utilization/loss/viability from a path's real link stats."""
    if not path or len(path) < 2:
        return {"delay_ms": 0.0, "max_utilization": 1.0, "loss_rate": 0.2, "broken": True}

    delays: List[float] = []
    losses: List[float] = []
    utilizations: List[float] = []
    broken = False
    for u, v in zip(path, path[1:]):
        stats = state.get_link_stats(link_id(u, v))
        if stats is None or stats.status != "up":
            broken = True
            continue
        delays.append(float(stats.delay_ms or 0.0))
        losses.append(float(stats.packet_loss or 0.0))
        utilizations.append(float(stats.utilization))

    return {
        "delay_ms": sum(delays) if delays else 0.0,
        "max_utilization": max(utilizations) if utilizations else 1.0,
        "loss_rate": (sum(losses) / len(losses)) if losses else 0.2,
        "broken": broken,
    }


def compute_flow_metrics(state: NetworkState, path: Optional[Sequence[str]], offered_load_mbps: float) -> Dict[str, float]:
    """
    Translate a selected path's real link conditions into per-flow performance.

    Queueing delay and loss both grow with how far utilization sits above the
    0.7 operating threshold (simple M/M/1-style behavior: near-flat below it,
    rising sharply as a link saturates), not just throughput -- a path that is
    still "up" but heavily congested should look worse on every metric, not
    only slower.
    """
    metrics = path_metrics(state, path)
    if metrics["broken"]:
        delay_ms = metrics["delay_ms"] + 40.0
        loss_rate = max(float(metrics["loss_rate"]), 0.12)
        throughput_factor = 0.20
    else:
        congestion = max(0.0, float(metrics["max_utilization"]) - 0.7)
        delay_ms = metrics["delay_ms"] + 4.0 + 60.0 * congestion
        loss_rate = float(metrics["loss_rate"]) + 0.05 * congestion
        throughput_factor = max(0.15, 1.0 - float(metrics["max_utilization"]))

    path_capacity_mbps = resolve_path_capacity_mbps(path)
    throughput_mbps = min(offered_load_mbps, path_capacity_mbps) * throughput_factor
    return {
        "delay_ms": round(delay_ms, 6),
        "throughput_mbps": round(throughput_mbps, 6),
        "packet_loss": round(loss_rate, 6),
    }


class StaticDriver:
    """Baseline 1: compute the shortest path once and never change it."""

    name = "static"

    def __init__(self, state: NetworkState, src: str, dst: str, initial_path: Optional[List[str]] = None):
        self.state = state
        self.src, self.dst = src, dst
        self.installer = FlowInstaller()
        self.path = list(initial_path) if initial_path else StaticShortestPath(state.get_active_graph()).compute_path(src, dst)
        if self.path:
            self.installer.install_path(self.path)

    def step(self, **_kwargs) -> Dict[str, object]:
        t0 = time.perf_counter()
        # Static never re-evaluates; the "decision" is simply not recomputing.
        decision_time_ms = (time.perf_counter() - t0) * 1000.0
        return {"path": self.path, "reroute": False, "flow_updates": 0, "decision_time_ms": round(decision_time_ms, 6)}


class DynamicDriver:
    """Baseline 2: reroute immediately on any threshold crossing or failed link, no stability gates."""

    name = "dynamic"

    def __init__(
        self,
        state: NetworkState,
        src: str,
        dst: str,
        threshold: float = 0.7,
        initial_path: Optional[List[str]] = None,
    ):
        self.state = state
        self.src, self.dst = src, dst
        self.builder = GraphBuilder(state)
        self.baseline = DynamicBaseline(self.builder, threshold=threshold)
        self.installer = FlowInstaller()
        self.path = list(initial_path) if initial_path else self.baseline.compute_path(src, dst)
        if self.path:
            self.installer.install_path(self.path)

    def step(self, now_s: float = 0.0, topology_changed: bool = False, **_kwargs) -> Dict[str, object]:
        t0 = time.perf_counter()
        event = self.baseline.evaluate_reroute(
            self.src,
            self.dst,
            self.path,
            timestamp=BASE_TIMESTAMP + timedelta(seconds=now_s),
            topology_changed=topology_changed,
            now=now_s,
        )
        reroute = event["decision"] == "reroute"
        flow_updates = 0
        if reroute:
            new_path = event["proposed_path"]
            flow_updates = len(self.installer.build_flow_rules(new_path))
            self.installer.install_path(new_path)
            self.path = new_path
        decision_time_ms = (time.perf_counter() - t0) * 1000.0
        return {
            "path": self.path,
            "reroute": reroute,
            "flow_updates": flow_updates,
            "decision_time_ms": round(decision_time_ms, 6),
        }


class ProposedDriver:
    """
    Proposed algorithm: threshold + hysteresis + persistence + path cost with
    minimum-improvement + change budget + hold-down, plus emergency failure
    bypass and recovery-window-protected switch-back, all via DecisionEngine.
    """

    name = "proposed"

    def __init__(
        self,
        state: NetworkState,
        src: str,
        dst: str,
        config_path: str = "config/decision.yaml",
        persistence_required_samples: Optional[int] = None,
        initial_path: Optional[List[str]] = None,
        utilization_threshold: Optional[float] = None,
        offered_load_mbps: Optional[float] = None,
        service_type: Optional[str] = None,
        resilience_avoid_threshold: Optional[float] = None,
    ):
        self.state = state
        self.src, self.dst = src, dst
        # This flow's traffic class -- forwarded to DecisionEngine.evaluate_pair
        # so config/policies.yaml's per-class policy (severity-scaled
        # skip-persistence for high-priority classes) applies here too, not
        # just in priority_policy.py's own evaluate_service_congestion path.
        # None (default) preserves prior behavior exactly.
        self.service_type = service_type
        # This flow's own raw bandwidth demand in Mbps -- self-influence /
        # offered-load accounting. Carries the flow's raw Mbps rather than a
        # single precomputed utilization fraction, so PathCost can convert
        # it per-edge using each edge's own real capacity (a uniform
        # fraction based on one link's capacity would systematically
        # misprice other edges -- see PathCost.calculate_path_cost's
        # docstring). Applied to candidate paths only when comparing
        # reroute options, and only to edges the candidate doesn't already
        # share with the current path, since the current path already
        # reflects this flow's real contribution on those. None (default)
        # preserves prior behavior exactly.
        self.offered_load_mbps = offered_load_mbps
        self.engine = DecisionEngine(state, config_path=config_path)
        if persistence_required_samples is not None:
            self.engine.persistence_checker.required_samples = persistence_required_samples
            self.engine.persistence_checker.persistence_seconds = 0.0
            self.engine.persistence_checker.cooldown_seconds = 0.0
        if utilization_threshold is not None:
            # The "threshold" concept lives in two places that must move
            # together: the raw violation check (ThresholdDetector) and the
            # hysteresis enter/release gate (StabilityManager) -- overriding
            # only one is a no-op, since _evaluate_congestion requires both
            # a violation AND is_congested() to proceed.
            self.engine.threshold_detector.thresholds["utilization"] = utilization_threshold
            self.engine.stability.enter_threshold = utilization_threshold
            self.engine.stability.release_threshold = max(0.0, utilization_threshold - 0.05)
        if resilience_avoid_threshold is not None:
            # Same override pattern as utilization_threshold above -- lets a caller enable
            # resilience avoidance without needing a custom decision.yaml. None (default)
            # preserves config/decision.yaml's own resilience_avoidance.enabled setting.
            self.engine.path_cost.graph_builder.resilience_avoid_threshold = resilience_avoid_threshold

        self.path = list(initial_path) if initial_path else self.engine.path_cost.find_best_path(src, dst)
        pair = (src, dst)
        self.engine.current_paths[pair] = list(self.path) if self.path else []
        self.original_path = list(self.path) if self.path else []
        if self.path:
            self.engine.flow_installer.install_path(self.path)
        self.watching_recovery = False

    def step(
        self,
        now_s: float = 0.0,
        hotspot_link: Optional[str] = None,
        hotspot_utilization: Optional[float] = None,
        **_kwargs,
    ) -> Dict[str, object]:
        t0 = time.perf_counter()
        pair = (self.src, self.dst)
        reroute = False
        flow_updates = 0

        if self.watching_recovery:
            action = self.engine.evaluate_recovery_switchback(
                self.src, self.dst, self.path, now=now_s,
                offered_load_mbps=self.offered_load_mbps,
            )
            if action:
                self.path = action["new_path"]
                flow_updates += len(self.engine.flow_installer.build_flow_rules(self.path))
                reroute = True
                self.watching_recovery = False

        if hotspot_link is not None and hotspot_utilization is not None:
            violation = self.engine.threshold_detector.check_utilization(hotspot_link, hotspot_utilization)
            if violation is not None:
                candidate = self.engine.path_cost.find_best_path(self.src, self.dst, now=now_s)
                if candidate and candidate != self.path:
                    action = self.engine.evaluate_pair(
                        self.src, self.dst, self.path, candidate, violation, now=now_s,
                        offered_load_mbps=self.offered_load_mbps,
                        service_type=self.service_type,
                    )
                    if action:
                        flow_updates += len(self.engine.flow_installer.build_flow_rules(candidate))
                        self.path = candidate
                        reroute = True
            else:
                # Utilization has cleared the threshold entirely; let hysteresis
                # state reset so a future crossing is treated as a fresh entry,
                # and leak the persistence window by one unit too -- see
                # DecisionEngine.leak_persistence's docstring: a full clear
                # here would erase standing evidence of a chronic-but-brief
                # problem the instant it dips below threshold even once; a
                # 1:1 leak still forgives an isolated spike within 1-2 clean
                # samples but lets repeated short violations net upward).
                self.engine.stability.update_congestion_state(hotspot_link, hotspot_utilization)
                self.engine.leak_persistence(hotspot_link)

        # Independent of congestion: move off a link the resilience gate has
        # latched as anomalous even if it isn't the hotspot and nothing failed.
        # No-op unless resilience_avoidance is enabled and the current path
        # actually uses a gated link (the gate's own hysteresis / persistence /
        # give-up already made that call).
        if not reroute:
            resilience_action = self.engine.evaluate_resilience_avoidance(
                self.src, self.dst, self.path, now=now_s,
            )
            if resilience_action:
                self.path = resilience_action["new_path"]
                flow_updates += len(self.engine.flow_installer.build_flow_rules(self.path))
                reroute = True

        decision_time_ms = (time.perf_counter() - t0) * 1000.0
        return {
            "path": self.path,
            "reroute": reroute,
            "flow_updates": flow_updates,
            "decision_time_ms": round(decision_time_ms, 6),
        }

    def on_link_failure(self, failed_link_id: str, now_s: float) -> Dict[str, object]:
        current_links = {link_id(u, v) for u, v in zip(self.path, self.path[1:])}
        if failed_link_id not in current_links:
            return {"reroute": False, "flow_updates": 0}
        candidate = self.engine.path_cost.find_best_path(self.src, self.dst, now=now_s)
        action = self.engine.evaluate_failure(
            self.src, self.dst, self.path, failed_link_id, candidate, now=now_s,
            offered_load_mbps=self.offered_load_mbps,
        )
        if action:
            flow_updates = len(self.engine.flow_installer.build_flow_rules(candidate))
            self.path = candidate
            return {"reroute": True, "flow_updates": flow_updates}
        return {"reroute": False, "flow_updates": 0}

    def on_link_recovered(self, recovered_link_id: str, now_s: float) -> None:
        self.engine.begin_recovery_watch(
            self.src, self.dst, recovered_link_id, original_path=self.original_path, now=now_s
        )
        self.watching_recovery = True

    def on_link_flap(self) -> None:
        self.engine.invalidate_recovery(self.src, self.dst)
        self.watching_recovery = False


def make_drivers(
    state: NetworkState,
    src: str = PRIMARY_PAIR[0],
    dst: str = PRIMARY_PAIR[1],
    threshold: float = 0.7,
    persistence_required_samples: Optional[int] = None,
    offered_load_mbps: Optional[float] = None,
    service_type: Optional[str] = None,
    resilience_avoid_threshold: Optional[float] = None,
) -> Dict[str, object]:
    """
    Construct one driver per algorithm sharing the same seeded NetworkState,
    all starting from the same initial path (the current cost-optimal path
    when the experiment begins) so later divergence reflects each algorithm's
    own reaction, not a different starting point. Static is deliberately
    initialised onto this same converged path too -- it then never adapts,
    which is the property Stage 6 actually exercises (its own hop-count
    tie-break behavior is already validated independently in Stage 3).

    offered_load_mbps: opt-in self-influence / offered-load accounting --
    forwarded to ProposedDriver only (static/dynamic don't go through
    PathCost.compare_paths' asymmetric old/new costing). None (default)
    preserves every existing caller's behavior exactly.

    resilience_avoid_threshold: override the avoid threshold for links with a
    real resilience anomaly (see NetworkState.get_resilience_score / ResilienceGate).
    config/decision.yaml now enables resilience avoidance by default at 0.57,
    so ProposedDriver already has it on; pass this only to use a different
    threshold in one experiment. Forwarded to ProposedDriver only -- deliberately
    not given to dynamic: resilience awareness is a proposed-only stability
    mechanism, the same way persistence/hysteresis are, and dynamic stays the
    naive, gate-free baseline throughout.
    """
    initial_path = GraphBuilder(state).get_candidate_paths(src, dst, max_paths=1)
    initial_path = initial_path[0] if initial_path else StaticShortestPath(state.get_active_graph()).compute_path(src, dst)
    return {
        "static": StaticDriver(state, src, dst, initial_path=initial_path),
        "dynamic": DynamicDriver(state, src, dst, threshold=threshold, initial_path=initial_path),
        "proposed": ProposedDriver(
            state,
            src,
            dst,
            persistence_required_samples=persistence_required_samples,
            initial_path=initial_path,
            utilization_threshold=threshold,
            offered_load_mbps=offered_load_mbps,
            service_type=service_type,
            resilience_avoid_threshold=resilience_avoid_threshold,
        ),
    }
