#!/usr/bin/env python3
"""
Decision Engine - Coordinate all rerouting decisions
Combines threshold detection, persistence checks, path cost, and change budget
"""

from typing import Dict, List, Optional, Tuple

import yaml

from .change_budget import ChangeBudget
from .decision_logger import DecisionLogger
from .path_cost import PathCost
from .persistence_checker import PersistenceChecker
from .threshold_detector import ThresholdDetector
from ..monitor.network_state import NetworkState
from ..routing.flow_installer import FlowInstaller


class DecisionEngine:
    """Main decision engine for stability-aware routing."""

    def __init__(self, network_state: NetworkState, config_path: str = "config/decision.yaml"):
        self.network_state = network_state

        with open(config_path, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)

        self.threshold_detector = ThresholdDetector(config.get('thresholds', {}))

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

        self.logger = DecisionLogger()
        self.flow_installer = FlowInstaller()

        self.current_paths: Dict[Tuple[str, str], List[str]] = {}
        self.last_hold_down_end: Dict[Tuple[str, str], float] = {}

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

    def _evaluate_violation(self, violation) -> Optional[dict]:
        """Evaluate a single violation and decide on action."""
        link_id = violation.link_id
        metric = violation.metric

        # Check cooldown
        if self.persistence_checker.in_cooldown(link_id, metric):
            self.logger.log_cooldown(link_id)
            return None

        # Check persistence
        if not self.persistence_checker.check_persistence(link_id, metric):
            self.persistence_checker.record_violation(link_id, metric, violation.value)
            self.logger.log_no_action(f"Violation on {link_id} not yet persistent")
            return None

        # Find affected flows and their paths
        # For now, we'll find a representative path that uses this link
        # In real implementation, we'd track all active flows

        affected_pair = self._find_affected_flow(link_id)
        if not affected_pair:
            return None

        src, dst = affected_pair
        old_path = self.current_paths.get(affected_pair)

        if not old_path:
            # Get initial path
            old_path = self.path_cost.find_best_path(src, dst)
            if old_path:
                self.current_paths[affected_pair] = old_path
            else:
                return None

        new_path = self.path_cost.find_best_path(src, dst)

        if not new_path or new_path == old_path:
            self.logger.log_no_action("No better path available")
            return None

        # Check if improvement meets threshold
        if not self.path_cost.is_improvement(
            old_path, new_path,
            self.min_improvement.get('absolute_cost_reduction', 0.1),
            self.min_improvement.get('relative_cost_reduction', 0.15)
        ):
            old_cost = self.path_cost.calculate_path_cost(old_path)
            new_cost = self.path_cost.calculate_path_cost(new_path)
            self.logger.log_no_improvement(old_path, new_path, old_cost, new_cost)
            return None

        # Check change budget
        if not self.change_budget.can_change_path():
            self.logger.log_no_action("Change budget exhausted")
            return None

        # All checks passed - execute reroute
        old_cost = self.path_cost.calculate_path_cost(old_path)
        new_cost = self.path_cost.calculate_path_cost(new_path)

        stability_used = ["threshold", "persistence", "change_budget", "min_improvement"]

        self.logger.log_reroute(
            reason=f"{metric} violation on {link_id}",
            affected_links=[link_id],
            old_path=old_path,
            new_path=new_path,
            old_cost=old_cost,
            new_cost=new_cost,
            stability_used=stability_used
        )

        self.current_paths[affected_pair] = new_path
        self.change_budget.record_path_change()
        self.persistence_checker.record_reroute(link_id, metric)
        self.flow_installer.install_path(new_path)

        return {
            'type': 'reroute',
            'src': src,
            'dst': dst,
            'old_path': old_path,
            'new_path': new_path,
            'reason': f"{metric} violation"
        }

    def _find_affected_flow(self, link_id: str) -> Optional[Tuple[str, str]]:
        """Find a flow affected by this link (simplified)."""
        # In real implementation, we'd track flow paths
        # For now, return any flow that might use this link

        # Get link endpoints
        endpoints = link_id.split('-')
        if len(endpoints) < 2:
            return None

        u, v = endpoints[0], endpoints[1]

        # Find any path in current_paths that uses this link
        for (src, dst), path in self.current_paths.items():
            for i in range(len(path) - 1):
                a, b = path[i], path[i + 1]
                if (a == u and b == v) or (a == v and b == u):
                    return (src, dst)

        # If no existing path, pick any pair crossing this area
        nodes = list(self.network_state.topology.graph.nodes())
        if len(nodes) >= 2:
            return (nodes[0], nodes[-1])

        return None

    def get_decision_summary(self) -> dict:
        """Get summary of decisions made."""
        return {
            'decisions': self.logger.get_summary(),
            'change_budget': {
                'update_count': self.change_budget.get_update_count(),
                'path_change_count': self.change_budget.get_path_change_count()
            }
        }
