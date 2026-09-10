#!/usr/bin/env python3
"""
Mininet side of the RYU connectivity probe - a minimal 2-switch topology
pointed at a real remote RYU controller (127.0.0.1:6653), generating real
traffic (pingAll) so the controller's periodic port-stats polling has
something real to report back.

Run as: sudo python3 scripts/ryu/ryu_connectivity_probe.py
(with `ryu-manager scripts/ryu/connectivity_probe.py` already running
in a separate terminal, from ryu-env)
"""
from __future__ import annotations

import time

from mininet.net import Mininet
from mininet.node import OVSSwitch, RemoteController
from mininet.link import TCLink
from mininet.log import setLogLevel


def main() -> None:
    setLogLevel("info")
    net = Mininet(switch=OVSSwitch, link=TCLink, controller=None)
    net.addController("c0", controller=RemoteController, ip="127.0.0.1", port=6653)

    s1 = net.addSwitch("s1", protocols="OpenFlow13")
    s2 = net.addSwitch("s2", protocols="OpenFlow13")
    h1 = net.addHost("h1")
    h2 = net.addHost("h2")
    net.addLink(h1, s1)
    net.addLink(s1, s2)
    net.addLink(s2, h2)

    try:
        net.start()
        print("*** Waiting 3s for switches to connect to RYU...")
        time.sleep(3)
        print("*** Generating real traffic (pingAll) so port stats have something to report")
        net.pingAll()
        print("*** Idling 15s -- watch the ryu-manager terminal for '*** REAL STATS' lines")
        time.sleep(15)
    finally:
        net.stop()


if __name__ == "__main__":
    main()
