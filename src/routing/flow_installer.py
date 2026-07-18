#!/usr/bin/env python3
"""
Flow Installer - Install flows to switches
"""
import subprocess


class FlowInstaller:
    """Install OpenFlow flows via ovs-ofctl."""

    def __init__(self):
        pass

    def install_path(self, path: list):
        """Install flows along a path (stub implementation)."""
        pass

    def install_flow(self, switch, in_port, out_port):
        pass

    def clear_flows(self, switch):
        pass
