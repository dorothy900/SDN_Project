#!/usr/bin/env python3
"""
Traffic Policy - Class-based priority handling
"""
import yaml


class TrafficPolicy:
    """Apply traffic policies based on class."""

    def __init__(self, config_path: str = "config/policies.yaml"):
        with open(config_path, 'r', encoding='utf-8') as f:
            self.config = yaml.safe_load(f)

    def get_priority_level(self, service_type: str) -> int:
        for cls in self.config.get('traffic_classes', {}).values():
            if service_type in cls.get('services', []):
                return cls.get('priority_level', 3)
        return 3

    def should_reroute_immediately(self, service_type: str) -> bool:
        for cls in self.config.get('traffic_classes', {}).values():
            if service_type in cls.get('services', []):
                return cls.get('reroute_immediate', False)
        return False
