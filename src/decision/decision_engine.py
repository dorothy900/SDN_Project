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

        self.path_cost = PathCost(network_state, config.get('path_cost_weights', {}))

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
        Leaky-bucket persistence update -- call this whenever a sample
        confirms the condition has genuinely cleared this sample
        (utilization back at/under threshold), not just when a reroute
        succeeds. Forgives exactly one accumulated violation (see
        PersistenceChecker.leak_window's docstring for the full rationale
        and 1:1 leak-rate reasoning).

        Originally a full clear_window() (2026-08-20, found via the
        oscillating-hotspot flapping check: `PersistenceChecker.
        evaluate_sample`'s window-clearing branch was unreachable in
        practice -- every real call site only ever invoked it with
        `is_violation=True`, so a link that stopped violating kept its
        accumulated count forever, until an actual reroute cleared it via
        `record_reroute`, silently turning "N *consecutive* violating
        samples" into "N total since the last reroute"). Switched from a
        full clear to a 1-unit leak the same day, once that fix revealed a
        second issue: strict consecutive/full-clear semantics also cannot
        see a link that violates *repeatedly but briefly* (e.g. bursts
        shorter than required_samples) -- see compliance_check.md's
        "Leaky-bucket persistence" section for the oscillating-hotspot
        evidence.

        `evaluate_service_congestion` (checks its own threshold internally)
        calls this directly on its below-threshold path. Callers that check
        a threshold themselves before invoking `evaluate_pair`/
        `evaluate_failure`/`evaluate_recovery_switchback` (this project's
        offline experiment drivers, which pre-validate a `ThresholdViolation`
        object before calling in) must call this on their own "not
        violating" branch -- see `ProposedDriver.step()` in
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

        service_type (added 2026-08-20): optional traffic class for this
        flow. When given and config/policies.yaml marks it
        `reroute_immediate` (e.g. VoIP/Video), persistence is skipped
        entirely here too -- previously this policy only applied via
        evaluate_service_congestion (the priority_policy.py scenario),
        even though every other scenario's monitored flow (flow-video-1,
        service_type="Video") is already configured as high-priority in
        policies.yaml and got no benefit from it. None (default) preserves
        prior behavior exactly -- existing callers are unaffected unless
        they opt in.
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
            persistence_result = self.persistence_checker.evaluate_sample(
                link_id=link_id,
                metric=metric,
                value=float(violation.value),
                is_violation=True,
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

        History on the offered-load self-influence correction here (both
        directions found real, not assumed):

        Removed 2026-08-20 after a real case (PRIMARY_PAIR,
        `recovery_window_seconds=5.0` correctly judged eligible at sample
        11, switchback then rejected: `original_path`'s true cost 0.2597
        vs `current_path`'s 0.2609 -- a real, if modest, 0.46% improvement
        -- ballooned to 0.4772 once the correction was applied to all 3 of
        its edges, none shared with `current_path`). Argument at the time:
        `original_path` already carried this exact flow before the
        failure, real historical evidence it can handle this load, unlike
        a genuinely untested candidate.

        Re-added the same day after a real Mininet experiment
        (`scripts/mininet_offered_load_recovery_check.py`) demonstrated the
        risk that removal explicitly accepted is not merely theoretical:
        injecting a REAL background flow (4-16 Mbps) directly onto
        `original_path`'s other edges during a real outage flipped the
        *uncorrected* decision to `accepted=True` at every one of those
        rates, while the corrected decision correctly stayed `False` --
        i.e. without this correction, a real network would have switched
        back into a path that had, in reality, just gotten worse.

        Both findings are real and in tension, because the correction
        cannot distinguish "this candidate's true margin shrank because
        background genuinely changed" from "this candidate's true margin
        was always this small, and the correction's own self-load estimate
        is what tips it" -- both produce the same symptom (rejection).
        Re-enabling is the safety-first choice: it protects against a
        demonstrated real failure mode (switching into a link that just
        got worse) at the cost of also re-blocking small-but-genuine
        improvements when background did *not* actually change (the
        PRIMARY_PAIR stable-case switchback is expected to go back to not
        completing in the offline harness's static-background scenario --
        a known, disclosed trade-off, not silently reintroduced). See
        compliance_check.md's "Re-enabling the offered-load correction for
        recovery switchback" section for both experiments' numbers.
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
        the affected link(s) have seen -- churn_score=0 (no recent
        instability) uses the permissive *_low_churn floor; churn_score=1.0
        (already flapping) uses the original fixed ceiling unchanged, so an
        already-unstable link is never treated more permissively than
        before. Linear interpolation in between. Uses the max churn_score
        across affected_links (most cautious link governs), or the ceiling
        if there are no affected links to check.

        Motivation: a real congestion.py scenario found a genuine ~10%
        real cost improvement blocked by the flat 0.15 threshold once the
        offered-load self-influence correction (a separate, legitimate
        fix) shrank the improvement margin -- on a link with zero churn
        history, where there was no real flapping risk to protect against.
        See compliance_check.md's "Churn-adaptive minimum-improvement
        threshold" section.
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
        # A 2026-08-20 threshold-offset patch used to live here (subtracting
        # an "offered-load penalty fraction" from min_abs/min_rel), added
        # when the offered-load correction was known to over-price
        # candidate paths (single capacity-agnostic fraction applied even
        # to edges the candidate already shared with current_path). That
        # root cause is now fixed directly in PathCost.calculate_path_cost
        # (per-edge real capacity, shared edges excluded) -- compare_paths
        # below already returns the correct, undistorted cost, so offsetting
        # the threshold on top of that would double-discount the same
        # correction twice. See compliance_check.md's "Per-edge, no-double-
        # counting offered-load correction" section.
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
