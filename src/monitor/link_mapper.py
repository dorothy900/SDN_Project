#!/usr/bin/env python3
"""
Link Mapper - Map logical links to physical ports
Week 2 Day 2: Maps ODL ports to link capacities and computes directional utilization
"""
from typing import Dict, Tuple, Optional


class LinkMapper:
    """Map network links to switch ports and track capacities."""

    def __init__(self):
        self.link_port_map: Dict[str, Tuple[Tuple[str, int], Tuple[str, int]]] = {}
        self.port_link_map: Dict[Tuple[str, int], Tuple[str, bool]] = {}  # (switch, port) -> (link_id, is_direction_a)
    
    def register_link(
        self,
        link_id: str,
        switch_a: str,
        port_a: int,
        switch_b: str,
        port_b: int,
    ) -> None:
        """
        Register a bidirectional link between two switches.
        
        Args:
            link_id: Unique identifier for the link
            switch_a: First switch name
            port_a: Port on first switch
            switch_b: Second switch name
            port_b: Port on second switch
        """
        self.link_port_map[link_id] = ((switch_a, port_a), (switch_b, port_b))
        self.port_link_map[(switch_a, port_a)] = (link_id, True)
        self.port_link_map[(switch_b, port_b)] = (link_id, False)
    
    def get_link_for_port(
        self,
        switch: str,
        port: int,
    ) -> Optional[Tuple[str, bool]]:
        """
        Get link ID and direction for a given switch-port.
        
        Args:
            switch: Switch name
            port: Port number
        
        Returns:
            (link_id, is_direction_a) if found, None otherwise
        """
        return self.port_link_map.get((switch, port))
    
    def get_ports_for_link(
        self,
        link_id: str,
    ) -> Optional[Tuple[Tuple[str, int], Tuple[str, int]]]:
        """
        Get the two switch-port pairs for a link.
        
        Args:
            link_id: Link identifier
        
        Returns:
            ((switch_a, port_a), (switch_b, port_b)) if found, None otherwise
        """
        return self.link_port_map.get(link_id)
    
    def get_all_link_ids(self) -> list:
        """Get list of all registered link IDs."""
        return list(self.link_port_map.keys())
