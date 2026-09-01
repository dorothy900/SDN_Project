#!/usr/bin/env python3
"""
Decision Engine - Coordinate stability-aware rerouting decisions.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import yaml

from .change_budget import ChangeBudget
from .decision_logger import DecisionLogger
from .path_cost import PathCost
from .persistence_checker import PersistenceChecker
from .threshold_detector import ThresholdDetector, ThresholdViolation
from ..monitor.network_state import NetworkState
from ..routing.flow_installer import FlowInstaller
from ..stability.failure_handler import FailureHandler
from ..stability.recovery_manager import RecoveryManager
from ..stability.stability_manager import StabilityManager
from ..stability.traffic_policy import TrafficPolicy


class DecisionEngine:
    """
    Main decision engine for stability-aware routing.

    Combines every stability mechanism described for the "Proposed" algorithm:
    threshold detection, hysteresis (enter/release), persistence, path cost with
    a minimum-improvement gate, a rolling change budget, hold-down, emergency
    failure bypass, recovery-window-protected switch-back, and priority-aware
    traffic policy (high-priority classes trigger earlier and skip persistence).
    """

    def __init__(
        self,
        network_state: NetworkState,
        config_path: str = "config/decision.yaml",
        policies_config_path: str = "config/policies.yaml",
    ):
        self.network_state = network_state
        self.traffic_policy = TrafficPolicy(policies_config_path)

        with open(config_path, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)

        thresholds = config.get('thresholds', {})
        self.threshold_detector = ThresholdDetector(thresholds)

        hyst_config = config.get('hysteresis', {})
        self.persistence_checker = PersistenceChecker(
            persistence_seconds=hyst_config.get('persistence_seconds', 5),
            cooldown_seconds=hyst_config.get('cooldown_seconds', 10)
        )

        budget_config = config.get('change_budget', {})
        self.change_budget = ChangeBudget(
            max_updates_per_minute=budget_config.get('max_updates_per_minute', 10),
            max_path_changes_per_minute=budget_config.get('max_path_changes_per_minute', 5),
            burst_allowance=budget_config.get('burst_allowance', 3)
        )

        resilience_config = config.get('resilience_avoidance', {})
        resilience_enabled = resilience_config.get('enabled', False)
        resilience_avoid_threshold = (
            float(resilience_config['avoid_threshold']) if resilience_enabled else None
        )
        resilience_persist_seconds = (
            float(resilience_config.get('persist_seconds', 0.0)) if resilience_enabled else 0.0
        )
        self.path_cost = PathCost(
            network_state, config.get('path_cost_weights', {}),
            resilience_avoid_threshold=resilience_avoid_threshold,
            resilience_persist_seconds=resilience_persist_seconds,
        )

        self.min_improvement = config.get('minimum_improvement', {})
        self.hold_down = config.get('hold_down', {})

        hold_down_seconds = (
            float(self.hold_down.get('duration_seconds', 10))
            if self.hold_down.get('enabled', True)
            else 0.0
        )
        self.stability = StabilityManager(
            hold_down_seconds=hold_down_seconds,
            enter_threshold=float(hyst_config.get('enter_threshold', thresholds.get('utilization', 0.7))),
            release_threshold=float(hyst_config.get('release_threshold', 0.65)),
        )
        self.failure_handler = FailureHandler()
        recovery_config = config.get('recovery', {})
        self.recovery_manager = RecoveryManager(
            recovery_window_seconds=float(recovery_config.get('window_seconds', 5.0))
        )

        self.logger = DecisionLogger()
        self.flow_installer = FlowInstaller()

        self.current_paths: Dict[Tuple[str, str], List[str]] = {}
        self.original_paths: Dict[Tuple[str, str], List[str]] = {}
        self.recovery_links: Dict[Tuple[str, str], str] = {}

    def evaluate_network(self) -> List[dict]:
        """Evaluate network and decide on rerouting actions."""
        actions = []

        violations = self.threshold_detector.detect_all_violations(self.network_state)

        if not violations:
            self.logger.log_no_action("No threshold violations detected")
            return actions

        for violation in violations:
            action = self._evaluate_violation(violation)
            if action:
                actions.append(action)

        return actions

    def leak_persistence(self, link_id: str, metric: str = "utilization") -> None:
        """
        Leaky-bucket persistence update: call this whenever a sample
        confirms the condition has genuinely cleared (utilization back
        at/under threshold). Forgives exactly one accumulated violation --
        see PersistenceChecker.leak_window()'s docstring for the rationale.

        evaluate_service_congestion() (checks its own threshold internally)
        calls this on its below-threshold path automatically. Callers that
        check a threshold themselves before calling evaluate_pair()/
        evaluate_failure()/evaluate_recovery_switchback() must call this on
        their own "not violating" branch -- see ProposedDriver.step() in
        experiments/simulation_common.py for the reference integration.
        """
        self.persistence_checker.leak_window(link_id, metric)

    def evaluate_pair(
        self,
        src: str,
        dst: str,
        current_path: List[str],
        candidate_path: List[str],
        violation,
        now: Optional[float] = None,
        offered_load_mbps: Optional[float] = None,
        service_type: Optional[str] = None,
    ) -> Optional[dict]:
        """
        Evaluate an ordinary (non-emergency) congestion reroute.

        Applies, in order: hysteresis (enter/release state), hold-down,
        persistence, minimum-improvement path cost, and the change budget.

        offered_load_mbps: this flow's own raw bandwidth demand in Mbps --
        forwarded to PathCost so candidate_path is costed as if this flow
        were already routed across it (current_path already reflects that
        for real, since the flow is actually there). See PathCost.
        calculate_path_cost's docstring.

        service_type: optional traffic class for this flow. When given and
        config/policies.yaml marks it reroute_immediate (e.g. VoIP/Video),
        persistence is skipped entirely. None (default) preserves prior
        behavior exactly.
        """
        skip_persistence = bool(service_type) and self.traffic_policy.should_reroute_immediately(service_type)
        return self._evaluate_congestion(
            src, dst, current_path, candidate_path, violation, skip_persistence=skip_persistence, now=now,
            offered_load_mbps=offered_load_mbps,
        )

    def evaluate_service_congestion(
        self,
        src: str,
        dst: str,
        current_path: List[str],
        candidate_path: List[str],
        link_id: str,
        utilization: float,
        service_type: str,
        now: Optional[float] = None,
        offered_load_mbps: Optional[float] = None,
    ) -> Optional[dict]:
        """
        Evaluate a congestion reroute using the traffic-class-specific policy
        from config/policies.yaml: high-priority classes (e.g. VoIP/Video)
        trigger at a lower effective threshold and skip the persistence gate
        entirely ("reroute_immediate"), while lower-priority classes tolerate
        more utilization and still wait out persistence. Hysteresis, hold-down,
        and the change budget still protect every class equally.
        """
        base_threshold = float(self.threshold_detector.thresholds["utilization"])
        effective_threshold = self.traffic_policy.get_effective_trigger_threshold(base_threshold, service_type)
        if utilization <= effective_threshold:
            self.leak_persistence(link_id)
            self.logger.log_no_action(
                "%s utilization on %s below its effective threshold (%.4f <= %.4f)"
                % (service_type, link_id, utilization, effective_threshold)
            )
            return None

        violation = ThresholdViolation(
            link_id=link_id,
            metric="utilization",
            value=float(utilization),
            threshold=effective_threshold,
            severity=round(float(utilization) - effective_threshold, 6),
        )
        skip_persistence = self.traffic_policy.should_reroute_immediately(service_type)
        return self._evaluate_congestion(
            src, dst, current_path, candidate_path, violation, skip_persistence=skip_persistence, now=now,
            offered_load_mbps=offered_load_mbps,
        )

    def _evaluate_congestion(
        self,
        src: str,
        dst: str,
        current_path: List[str],
        candidate_path: List[str],
        violation,
        skip_persistence: bool,
        now: Optional[float] = None,
        offered_load_mbps: Optional[float] = None,
    ) -> Optional[dict]:
        """Shared hysteresis/hold-down/persistence/budget gating for a congestion reroute."""
        pair = (src, dst)
        self.current_paths[pair] = list(current_path)

        link_id = violation.link_id
        metric = violation.metric

        self.stability.update_congestion_state(link_id, float(violation.value))
        if not self.stability.is_congested(link_id):
            self.logger.log_no_action(
                "Utilization on %s has not cleared the hysteresis enter threshold" % link_id
            )
            return None

        if not self.stability.allow_reroute(pair, emergency=False, now=now):
            self.logger.log_no_action("Hold-down active for %s->%s" % pair)
            return None

        stability_used = ["threshold", "hysteresis", "minimum_improvement", "change_budget", "hold_down"]
        if skip_persistence:
            stability_used.append("priority_policy_immediate")
        else:
            persistence_result = self.persistence_checker.record_violation_sample(
                link_id=link_id,
                metric=metric,
                value=float(violation.value),
            )
            if not persistence_result["accepted"]:
                self.logger.log_no_action(
                    "Violation on %s not yet persistent (%d/%d samples)"
                    % (
                        link_id,
                        persistence_result["sample_count"],
                        self.persistence_checker.required_samples,
                    )
                )
                return None
            stability_used.append("persistence")

        if not self.change_budget.can_change_path():
            self.logger.log_no_action("Change budget exhausted")
            return None

        action = self._execute_reroute(
            pair=pair,
            current_path=current_path,
            candidate_path=candidate_path,
            reason="%s violation on %s" % (metric, link_id),
            affected_links=[link_id],
            stability_used=stability_used,
            emergency=False,
            now=now,
            offered_load_mbps=offered_load_mbps,
        )
        if action is not None:
            self.change_budget.record_path_change()
            if not skip_persistence:
                self.persistence_checker.record_reroute(link_id, metric)
        return action

    def evaluate_failure(
        self,
        src: str,
        dst: str,
        current_path: List[str],
        link_id: str,
        candidate_path: Optional[List[str]],
        now: Optional[float] = None,
        offered_load_mbps: Optional[float] = None,
    ) -> Optional[dict]:
        """
        Evaluate an emergency reroute after a link on the active path fails.

        Bypasses hysteresis, persistence, hold-down, and the minimum-improvement
        gate, since waiting out those timers during an outage is not acceptable.
        """
        pair = (src, dst)
        self.current_paths[pair] = list(current_path)

        failure = self.failure_handler.detect_failure(link_id, current_path=current_path)
        if not failure["emergency_reroute"]:
            self.logger.log_no_action(
                "Link %s failed but %s->%s is unaffected" % (link_id, src, dst)
            )
            return None

        if not candidate_path:
            self.logger.log_no_action("No alternate path available during failure of %s" % link_id)
            return None

        return self._execute_reroute(
            pair=pair,
            current_path=current_path,
            candidate_path=candidate_path,
            reason="emergency:link_failure:%s" % link_id,
            affected_links=[link_id],
            stability_used=["failure_handler", "emergency_bypass"],
            emergency=True,
            now=now,
            offered_load_mbps=offered_load_mbps,
        )

    def evaluate_resilience_avoidance(
        self,
        src: str,
        dst: str,
        current_path: List[str],
        now: Optional[float] = None,
    ) -> Optional[dict]:
        """
        Move a flow off a link the resilience gate has latched as anomalous
        (flapping or abnormal loss), even when that link is not congested and
        nothing has failed -- the case an ordinary threshold-driven reroute
        never sees, and the reason the gate needs its own trigger to work in a
        live deployment.

        Only fires when the current path actually uses a gated link and
        find_best_path (which is resilience_effective_graph, so it already
        encodes the gate's suppress/reuse hysteresis, persistence delay, and
        the P4 bounded-detour give-up) offers an alternative that itself uses
        no gated link.

        Treated as an emergency reroute with NO offered-load correction: a
        gated link is a soft failure, so the intent is "get off it", not
        "reroute only if it's clearly worth it net of our own load" -- the
        gate's own timers are the damping, and the P4 cap already refuses a
        pathologically long detour. The offered-load correction is a
        safety-first check for marginal *congestion* switch-backs; applying it
        here just strands the flow on a bad link behind a longer safe detour.
        """
        gb = self.path_cost.graph_builder
        if gb._resilience_gate is None or len(current_path) < 2:
            return None
        # Tick the gate: it only advances its persistence/hysteresis state
        # inside a graph build, and .step() only builds a graph when it is
        # already considering a congestion reroute -- so without this a
        # purely-degrading (uncongested) link would never latch, and this
        # check would be a permanent no-op. Called every step; cheap and
        # idempotent for an unchanged score at a fixed `now`.
        gb.build_weighted_graph(now=now)
        current_links = {self._link_id(u, v) for u, v in zip(current_path, current_path[1:])}
        if not any(gb._resilience_gate.is_avoided(lid) for lid in current_links):
            return None

        candidate = self.path_cost.find_best_path(src, dst, now=now)
        if not candidate or candidate == current_path:
            return None
        candidate_links = {self._link_id(u, v) for u, v in zip(candidate, candidate[1:])}
        if any(gb._resilience_gate.is_avoided(lid) for lid in candidate_links):
            return None  # the alternative is no better resilience-wise -- leave it

        pair = (src, dst)
        self.current_paths[pair] = list(current_path)
        return self._execute_reroute(
            pair=pair,
            current_path=current_path,
            candidate_path=candidate,
            reason="resilience:avoid_gated_link",
            affected_links=sorted(current_links - candidate_links),
            stability_used=["resilience_gate"],
            emergency=True,
            now=now,
            offered_load_mbps=None,
        )

    def begin_recovery_watch(
        self,
        src: str,
        dst: str,
        link_id: str,
        original_path: List[str],
        now: Optional[float] = None,
    ) -> None:
        """Start observing a restored link before allowing switch-back to it."""
        pair = (src, dst)
        self.failure_handler.recover_link(link_id)
        self.recovery_manager.start_recovery(link_id, now=now)
        self.original_paths[pair] = list(original_path)
        self.recovery_links[pair] = link_id

    def invalidate_recovery(self, src: str, dst: str) -> None:
        """Cancel a pending switch-back because the restored link flapped again."""
        pair = (src, dst)
        link_id = self.recovery_links.get(pair)
        if link_id:
            self.recovery_manager.invalidate_recovery(link_id)

    def evaluate_recovery_switchback(
        self,
        src: str,
        dst: str,
        current_path: List[str],
        now: Optional[float] = None,
        offered_load_mbps: Optional[float] = None,
    ) -> Optional[dict]:
        """
        Switch back to the original path once the recovery window confirms
        stability.

        Applies the offered-load self-influence correction (via
        offered_load_mbps -> PathCost) to this comparison, a deliberate
        safety-first choice: a real Mininet test showed that without it, a
        genuine background-traffic increase on the original path's other
        edges during the outage can make an already-worse path still look
        acceptable, and the network switches back into it. The trade-off
        this accepts: a small, genuine improvement can also get blocked
        when background did *not* actually change, since the correction
        can't distinguish the two cases from cost alone -- both look like a
        shrunk margin.
        """
        pair = (src, dst)
        link_id = self.recovery_links.get(pair)
        if not link_id or not self.recovery_manager.is_eligible_for_switchback(link_id, now=now):
            return None

        original_path = self.original_paths.get(pair)
        self.recovery_manager.complete_recovery(link_id)
        self.recovery_links.pop(pair, None)
        if not original_path or original_path == current_path:
            return None

        return self._execute_reroute(
            pair=pair,
            current_path=current_path,
            candidate_path=original_path,
            reason="recovery:stable_switchback:%s" % link_id,
            affected_links=[link_id],
            stability_used=["recovery_manager"],
            emergency=True,
            now=now,
            offered_load_mbps=offered_load_mbps,
        )

    def _churn_adaptive_min_improvement(self, affected_links: List[str], now: Optional[float] = None) -> Tuple[float, float]:
        """
        Scale the minimum-improvement thresholds by how much recent churn
        the affected link(s) have seen: churn_score=0 (no recent
        instability) uses the permissive *_low_churn floor; churn_score=1.0
        (already flapping) uses the original fixed ceiling unchanged, so an
        already-unstable link is never treated more permissively. Linear
        interpolation in between. Uses the max churn_score across
        affected_links (most cautious link governs), or the ceiling if
        there are no affected links to check.

        Without this, a genuinely small-but-real improvement on a link with
        zero churn history (no flapping risk to protect against) would be
        rejected by the same flat threshold meant to guard against
        borderline, potentially-oscillating candidates.
        """
        ceiling_abs = self.min_improvement.get("absolute_cost_reduction", 0.1)
        ceiling_rel = self.min_improvement.get("relative_cost_reduction", 0.15)
        floor_abs = self.min_improvement.get("absolute_cost_reduction_low_churn", ceiling_abs)
        floor_rel = self.min_improvement.get("relative_cost_reduction_low_churn", ceiling_rel)

        if not affected_links:
            return ceiling_abs, ceiling_rel
        churn = max(self.network_state.get_link_churn_score(lid, now=now) for lid in affected_links)
        churn = min(max(churn, 0.0), 1.0)
        min_abs = floor_abs + churn * (ceiling_abs - floor_abs)
        min_rel = floor_rel + churn * (ceiling_rel - floor_rel)
        return min_abs, min_rel

    def _execute_reroute(
        self,
        pair: Tuple[str, str],
        current_path: List[str],
        candidate_path: List[str],
        reason: str,
        affected_links: List[str],
        stability_used: List[str],
        emergency: bool,
        now: Optional[float] = None,
        offered_load_mbps: Optional[float] = None,
    ) -> Optional[dict]:
        """Shared cost-check, logging, and installation logic for any reroute."""
        min_abs, min_rel = (0.0, 0.0) if emergency else self._churn_adaptive_min_improvement(affected_links, now=now)
        # No separate threshold offset for the offered-load correction here:
        # PathCost.calculate_path_cost already prices it correctly per-edge
        # (real capacity, shared edges excluded), so compare_paths below
        # returns an undistorted cost -- offsetting the threshold on top
        # would double-discount the same correction.
        comparison = self.path_cost.compare_paths(
            current_path,
            candidate_path,
            min_abs_reduction=min_abs,
            min_rel_reduction=min_rel,
            now=now,
            offered_load_mbps=offered_load_mbps,
        )
        if not comparison["accepted"]:
            self.logger.log_no_improvement(
                current_path,
                candidate_path,
                comparison["old_cost"],
                comparison["new_cost"],
            )
            return None

        self.logger.log_reroute(
            reason=reason,
            affected_links=affected_links,
            old_path=current_path,
            new_path=candidate_path,
            old_cost=comparison["old_cost"],
            new_cost=comparison["new_cost"],
            stability_used=stability_used,
        )
        self.current_paths[pair] = list(candidate_path)
        self.stability.record_reroute(pair, emergency=emergency, now=now)
        self.flow_installer.install_path(candidate_path)

        # Record churn for every link that changed (added to or dropped from
        # the installed path) -- feeds GraphBuilder's instability/churn cost
        # term (formerly the dead "priority" slot), so a link that keeps
        # getting swapped in/out looks less attractive on the *next*
        # comparison, not just gated by the timing mechanisms above.
        old_links = {self._link_id(u, v) for u, v in zip(current_path, current_path[1:])}
        new_links = {self._link_id(u, v) for u, v in zip(candidate_path, candidate_path[1:])}
        for link_id in old_links.symmetric_difference(new_links):
            self.network_state.record_link_churn(link_id, timestamp=now)

        return {
            "type": "reroute",
            "src": pair[0],
            "dst": pair[1],
            "old_path": current_path,
            "new_path": candidate_path,
            "reason": reason,
            "old_cost": comparison["old_cost"],
            "new_cost": comparison["new_cost"],
        }

    def _evaluate_violation(self, violation) -> Optional[dict]:
        """Evaluate one threshold violation against a representative active flow."""
        link_id = violation.link_id
        affected_pair = self._find_affected_flow(link_id)
        if not affected_pair:
            return None

        src, dst = affected_pair
        current_path = self.current_paths.get(affected_pair)
        if current_path is None:
            current_path = self.path_cost.find_best_path(src, dst)
            if current_path is None:
                return None
            self.current_paths[affected_pair] = list(current_path)

        current_links = {
            self._link_id(u, v)
            for u, v in zip(current_path, current_path[1:])
        }
        try:
            candidates = self.path_cost.graph_builder.get_candidate_paths(src, dst, max_paths=4)
        except Exception:
            candidates = []
        new_path = None
        for candidate in candidates:
            if candidate != current_path:
                candidate_links = {
                    self._link_id(u, v)
                    for u, v in zip(candidate, candidate[1:])
                }
                if link_id not in current_links or link_id not in candidate_links:
                    new_path = candidate
                    break
        if new_path is None:
            new_path = self.path_cost.find_best_path(src, dst)

        if not new_path or new_path == current_path:
            self.logger.log_no_action("No better path available")
            return None

        return self.evaluate_pair(src, dst, current_path, new_path, violation)

    def _find_affected_flow(self, link_id: str) -> Optional[Tuple[str, str]]:
        """Find a flow affected by this link (simplified)."""
        # In real implementation, we'd track flow paths
        # For now, return any flow that might use this link

        endpoints = link_id.split('-')
        if len(endpoints) < 2:
            return None

        u, v = endpoints[0], endpoints[1]

        for (src, dst), path in self.current_paths.items():
            for i in range(len(path) - 1):
                a, b = path[i], path[i + 1]
                if (a == u and b == v) or (a == v and b == u):
                    return (src, dst)

        nodes = sorted(str(node) for node in self.network_state.get_active_graph().nodes())
        if len(nodes) >= 2:
            return (nodes[0], nodes[-1])

        return None

    @staticmethod
    def _link_id(u: str, v: str) -> str:
        nodes = sorted([str(u), str(v)])
        return "%s-%s" % (nodes[0], nodes[1])

    def get_decision_summary(self) -> dict:
        """Get summary of decisions made."""
        return {
            'decisions': self.logger.get_summary(),
            'change_budget': {
                'update_count': self.change_budget.get_update_count(),
                'path_change_count': self.change_budget.get_path_change_count()
            }
        }
