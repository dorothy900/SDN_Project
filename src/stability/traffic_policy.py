#!/usr/bin/env python3
"""
Traffic Policy - Class-based priority handling for stability control.
"""

from __future__ import annotations

from typing import Dict

import yaml


class TrafficPolicy:
    """Apply traffic policies based on service class and urgency."""

    def __init__(self, config_path: str = "config/policies.yaml"):
        with open(config_path, "r", encoding="utf-8") as f:
            self.config = yaml.safe_load(f)

    def get_class_config(self, service_type: str) -> Dict[str, object]:
        """Return the matched traffic-class configuration for a service."""
        for cls in self.config.get("traffic_classes", {}).values():
            if service_type in cls.get("services", []):
                return cls
        return {
            "priority_level": 3,
            "qos_threshold": 0.25,
            "reroute_immediate": False,
        }

    def get_priority_level(self, service_type: str) -> int:
        """Return the integer priority for a service."""
        return int(self.get_class_config(service_type).get("priority_level", 3))

    def should_reroute_immediately(self, service_type: str) -> bool:
        """Return whether this class bypasses slower policy reactions."""
        return bool(self.get_class_config(service_type).get("reroute_immediate", False))

    def get_effective_trigger_threshold(self, base_threshold: float, service_type: str) -> float:
        """
        Translate class priority into an effective trigger threshold.

        Higher-priority traffic reacts earlier, while low-priority traffic tolerates
        more degradation before rerouting.
        """
        priority = self.get_priority_level(service_type)
        if priority == 1:
            return base_threshold
        if priority == 2:
            return min(0.95, base_threshold + 0.08)
        return min(0.95, base_threshold + 0.16)

    def evaluate_service(self, service_type: str, utilization: float, base_threshold: float = 0.7) -> Dict[str, object]:
        """Return an interpretable decision record for one class on one sample."""
        effective_threshold = self.get_effective_trigger_threshold(base_threshold, service_type)
        should_reroute = utilization >= effective_threshold
        return {
            "service_type": service_type,
            "priority_level": self.get_priority_level(service_type),
            "utilization": round(utilization, 6),
            "effective_threshold": round(effective_threshold, 6),
            "reroute": should_reroute,
            "immediate": self.should_reroute_immediately(service_type),
        }
