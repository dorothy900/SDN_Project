#!/usr/bin/env python3
"""
OpenDaylight REST Client - Collect statistics from controller

NOTE: this is an optional, controller-mediated path to the same port/link
statistics that `StatisticsCollector.parse_ovs_port_stats()` already gets by
querying OVS directly via `ovs-ofctl` -- that path is complete and is what
TrafficMonitor actually uses to drive rate/utilization calculation and, from
there, path-cost-based routing. `get_port_statistics()` below is intentionally
left unparsed (it returns `[]` even on a successful response): finishing it
against OpenDaylight's real RESTCONF port-statistics schema without a live
controller to verify against would be guessing at an untestable contract.
Complete it only once there is a real ODL instance available to validate
against; until then, the OVS-direct path is the actual data source.
"""

import json
import time
from typing import Dict, List, Optional

import requests


class ODLClient:
    """Client for interacting with OpenDaylight REST API."""

    def __init__(
        self,
        ip: str = "127.0.0.1",
        port: int = 8181,
        username: str = "admin",
        password: str = "admin",
    ):
        self.base_url = f"http://{ip}:{port}/restconf"
        self.auth = (username, password)
        self.session = requests.Session()
        self.session.auth = self.auth
        self.session.headers.update({
            "Accept": "application/json",
            "Content-Type": "application/json",
        })

    def is_connected(self) -> bool:
        """Check if controller is reachable."""
        try:
            response = self.session.get(
                f"{self.base_url}/operational/opendaylight-inventory:nodes",
                timeout=5,
            )
            return response.status_code == 200
        except Exception:
            return False

    def get_connected_nodes(self) -> List[str]:
        """Get list of connected nodes/switches."""
        try:
            response = self.session.get(
                f"{self.base_url}/operational/opendaylight-inventory:nodes",
                timeout=10,
            )
            if response.status_code != 200:
                return []

            data = response.json()
            nodes = data.get("nodes", {}).get("node", [])
            node_ids = []

            for node in nodes:
                node_id = node.get("id", "")
                if node_id and node_id != "controller-config":
                    node_ids.append(node_id)

            return node_ids
        except Exception as e:
            print(f"Error fetching nodes: {e}")
            return []

    def get_port_statistics(
        self,
        node_id: str,
    ) -> List[Dict]:
        """
        Get port statistics for a specific node.
        Returns raw statistics data from ODL.
        """
        try:
            response = self.session.get(
                f"{self.base_url}/operational/opendaylight-inventory:nodes/node/{node_id}/table/{node_id}:0",
                timeout=10,
            )
            if response.status_code != 200:
                return []

            return []
        except Exception as e:
            print(f"Error fetching port stats for {node_id}: {e}")
            return []
