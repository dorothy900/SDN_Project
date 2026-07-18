#!/usr/bin/env python3
"""
Link Mapper - Map logical links to physical ports
"""
from typing import Dict, Optional, Tuple


class LinkMapper:
    """Map network links to switch ports."""

    def __init__(self):
        self.link_port_map: Dict[str, Tuple[str, int, str, int]] = {}  # link -> (s1, p1, s2, p2)

    def register_link(self, link_id: str, switch1: str, port1: int, switch2: str, port2: int):
        self.link_port_map[link_id] = (switch1, port1, switch2, port2)

    def get_link_ports(self, link_id: str) -> Optional[Tuple[str, int, str, int]]:
        return self.link_port_map.get(link_id)
