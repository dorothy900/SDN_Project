#!/usr/bin/env python3
"""
Threshold Detector - Detect congestion-state transitions from link metrics.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence

from ..monitor.network_state import NetworkState


@dataclass
class ThresholdViolation:
    """Structured threshold violation used by the decision engine."""

    link_id: str
    metric: str
    value: float
    threshold: float
    severity: float


class ThresholdDetector:
    """Check link metrics against configured utilization, delay, and loss limits."""

    def __init__(self, thresholds: Optional[Dict[str, float]] = None):
        defaults = {
            "utilization": 0.7,
            "delay_ms": 100.0,
            "loss_rate": 0.01,
        }
        self.thresholds = defaults
        if thresholds:
            self.thresholds.update(thresholds)

    def check_utilization(self, link_id: str, utilization: float) -> Optional[ThresholdViolation]:
        """Return a violation once utilization exceeds the configured threshold."""
        threshold = float(self.thresholds["utilization"])
        if utilization > threshold:
            return ThresholdViolation(
                link_id=link_id,
                metric="utilization",
                value=float(utilization),
                threshold=threshold,
                severity=round(float(utilization) - threshold, 6),
            )
        return None

    def check_delay_ms(self, link_id: str, delay_ms: float) -> Optional[ThresholdViolation]:
        """Return a violation once delay exceeds the configured threshold."""
        threshold = float(self.thresholds["delay_ms"])
        if delay_ms > threshold:
            return ThresholdViolation(
                link_id=link_id,
                metric="delay_ms",
                value=float(delay_ms),
                threshold=threshold,
                severity=round(float(delay_ms) - threshold, 6),
            )
        return None

    def check_loss_rate(self, link_id: str, loss_rate: float) -> Optional[ThresholdViolation]:
        """Return a violation once packet loss exceeds the configured threshold."""
        threshold = float(self.thresholds["loss_rate"])
        if loss_rate > threshold:
            return ThresholdViolation(
                link_id=link_id,
                metric="loss_rate",
                value=float(loss_rate),
                threshold=threshold,
                severity=round(float(loss_rate) - threshold, 6),
            )
        return None

    def detect_link_violations(self, link_id: str, link_data: Dict[str, object]) -> List[ThresholdViolation]:
        """Check every supported metric for a single link record."""
        violations: List[ThresholdViolation] = []

        utilization = link_data.get("utilization")
        if utilization is not None:
            violation = self.check_utilization(link_id, float(utilization))
            if violation is not None:
                violations.append(violation)

        delay_ms = link_data.get("delay_ms")
        if delay_ms is not None:
            violation = self.check_delay_ms(link_id, float(delay_ms))
            if violation is not None:
                violations.append(violation)

        packet_loss = link_data.get("packet_loss")
        if packet_loss is not None:
            violation = self.check_loss_rate(link_id, float(packet_loss))
            if violation is not None:
                violations.append(violation)

        return violations

    def detect_all_violations(self, network_state: NetworkState) -> List[ThresholdViolation]:
        """Inspect the current network snapshot and return all active violations."""
        snapshot = network_state.get_network_state()
        violations: List[ThresholdViolation] = []
        for link_id, link_data in snapshot.get("links", {}).items():
            violations.extend(self.detect_link_violations(link_id, link_data))
        return violations

    def detect_state_changes(
        self,
        values: Sequence[float],
        metric: str = "utilization",
    ) -> List[Dict[str, object]]:
        """
        Report the exact samples where a metric crosses into or out of violation.

        This is used by the Week 4 Day 1 unit-style validation.
        """
        if metric not in self.thresholds:
            raise ValueError("Unsupported metric: %s" % metric)

        threshold = float(self.thresholds[metric])
        events: List[Dict[str, object]] = []
        previous_state = "normal"
        for index, value in enumerate(values, start=1):
            current_state = "violating" if float(value) > threshold else "normal"
            if current_state != previous_state:
                events.append(
                    {
                        "sample": index,
                        "metric": metric,
                        "value": round(float(value), 6),
                        "threshold": threshold,
                        "state": current_state,
                        "transition": f"{previous_state}->{current_state}",
                    }
                )
            previous_state = current_state
        return events
