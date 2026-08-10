#!/usr/bin/env python3
"""
Flow Installer - Build and verify bidirectional OpenFlow-style rules.
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional, Tuple


class FlowInstaller:
    """Create deterministic, testable OpenFlow-like rules for a selected path."""

    def __init__(self, node_mapping: Optional[Dict[str, Tuple[str, str]]] = None):
        """
        node_mapping: real graph-node-id -> (switch_name, host_name), e.g. from
        topology.py's GeantTopology.node_mapping. Without it, names are guessed
        as f"s{int(node)+1}"/f"h{int(node)+1}" -- harmless for the offline
        simulation (names are never checked against anything real there), but
        WRONG against a real Mininet deployment: topology.py actually assigns
        switch/host numbers by sorted *string* order, not node-id+1, and the
        two only coincide for single-digit node ids (confirmed: 38/40 GEANT
        nodes mismatch). Always pass the real mapping when driving a live
        Mininet network.
        """
        self.installed_rules: Dict[str, List[str]] = {}
        self.node_mapping = node_mapping

    def build_flow_rules(self, path: List[str]) -> List[Dict[str, str]]:
        """
        Build forward and reverse rules for every switch on a selected path.

        The generated rules are textual but structured closely enough to
        validate that packets would traverse the intended path in both
        directions.
        """
        if len(path) < 2:
            return []

        rules: List[Dict[str, str]] = []
        source_host = self._host_name(path[0])
        destination_host = self._host_name(path[-1])

        for index, node in enumerate(path):
            switch = self._switch_name(node)
            previous_hop = source_host if index == 0 else self._switch_name(path[index - 1])
            next_hop = destination_host if index == len(path) - 1 else self._switch_name(path[index + 1])

            forward = {
                "switch": switch,
                "direction": "forward",
                "match": f"ipv4,nw_dst={destination_host}",
                "ingress": previous_hop,
                "egress": next_hop,
                "command": self._format_rule(switch, destination_host, previous_hop, next_hop),
            }
            reverse = {
                "switch": switch,
                "direction": "reverse",
                "match": f"ipv4,nw_dst={source_host}",
                "ingress": next_hop,
                "egress": previous_hop,
                "command": self._format_rule(switch, source_host, next_hop, previous_hop),
            }
            rules.extend([forward, reverse])

        return rules

    def install_path(self, path: List[str]) -> Dict[str, List[str]]:
        """
        Replace previously installed rules with the new path rules.

        This mirrors the safe-replace behavior requested for Week 3 without
        requiring a live OVS instance during local verification.
        """
        rules = self.build_flow_rules(path)
        grouped: Dict[str, List[str]] = {}
        for rule in rules:
            grouped.setdefault(rule["switch"], []).append(rule["command"])
        self.installed_rules = grouped
        return grouped

    def install_flow(self, switch: str, rule_text: str) -> None:
        """Install a single textual flow rule into the in-memory dump."""
        self.installed_rules.setdefault(switch, []).append(rule_text)

    def clear_flows(self, switch: str) -> None:
        """Remove all rules for a switch from the simulated switch table."""
        self.installed_rules[switch] = []

    def dump_flows(self) -> str:
        """Return a readable flow dump for all switches."""
        lines: List[str] = []
        for switch in sorted(self.installed_rules):
            lines.append(f"{switch}:")
            if not self.installed_rules[switch]:
                lines.append("  <empty>")
                continue
            for rule in self.installed_rules[switch]:
                lines.append(f"  {rule}")
        return "\n".join(lines)

    def save_flow_dump(
        self,
        before_path: List[str],
        after_path: List[str],
        output_path: Path,
    ) -> None:
        """
        Persist a before/after dump that proves path-specific rules changed.
        """
        output_path.parent.mkdir(parents=True, exist_ok=True)
        before_installed = self.install_path(before_path)
        before_dump = self.dump_flows()
        after_installed = self.install_path(after_path)
        after_dump = self.dump_flows()

        with output_path.open("w", encoding="utf-8") as handle:
            handle.write("=== BEFORE ===\n")
            handle.write(before_dump or "<empty>")
            handle.write("\n\n=== AFTER ===\n")
            handle.write(after_dump or "<empty>")
            handle.write("\n")

        self.installed_rules = after_installed or before_installed

    def _switch_name(self, node: str) -> str:
        if self.node_mapping is not None and str(node) in self.node_mapping:
            return self.node_mapping[str(node)][0]
        try:
            return f"s{int(str(node)) + 1}"
        except ValueError:
            return str(node)

    def _host_name(self, node: str) -> str:
        if self.node_mapping is not None and str(node) in self.node_mapping:
            return self.node_mapping[str(node)][1]
        try:
            return f"h{int(str(node)) + 1}"
        except ValueError:
            return f"h_{node}"

    @staticmethod
    def _format_rule(switch: str, dst_host: str, ingress: str, egress: str) -> str:
        return (
            f"ovs-ofctl add-flow {switch} "
            f"priority=100,{ingress}->{dst_host},actions=output:{egress}"
        )
