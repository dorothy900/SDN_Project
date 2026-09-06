#!/usr/bin/env python3
"""
Real-traffic congestion probe for the RYU stability-aware TE app: injects
real, ramping iperf UDP background traffic directly on the monitored pair
("0","12")'s real hotspot link (0-4, real switches s1<->s35), and watches
for a genuine reroute decision to fire once real measured utilization
crosses the demonstration threshold (0.3 -- see MONITORED_PAIRS's own
comment in scripts/ryu/stability_aware_te.py for why 0.3 and not the
real 0.7 production default).

The background flow is installed directly via ovs-ofctl (bypassing RYU
entirely, same single-link injection pattern as
scripts/mininet/independence_check.py) between h1 and h35 -- the hosts
directly attached to the hotspot link's own two switches -- so it saturates
exactly that one link, independent of whatever path RYU is managing for the
actual monitored flow (h1 -> h5).

Run as: sudo python3 scripts/ryu/ryu_real_congestion_probe.py
(with `ryu-manager --observe-links scripts/ryu/stability_aware_te.py`
already running in a separate terminal, from ryu-env)
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mininet.net import Mininet
from mininet.node import OVSSwitch, RemoteController
from mininet.link import TCLink
from mininet.log import setLogLevel

from topology import GeantTopology

OF_VERSION = "OpenFlow13"
# Ramp toward, not past, this VM's own real iperf UDP generation ceiling
# (~50-55Mbps, established elsewhere in this project's real-hardware scripts) --
# these levels correspond to roughly 5%/15%/25%/35%/45% of the hotspot link's
# real 100Mbps configured capacity, comfortably past the demonstration
# threshold (0.3) without needing to saturate the link outright.
BACKGROUND_RATES_MBPS = [5, 15, 25, 35, 45]
STEP_SETTLE_S = 12  # > POLL_INTERVAL_S + DECISION_INTERVAL_S, so a real decision cycle sees each step


def get_ofport(switch, neighbor) -> str:
    conns = switch.connectionsTo(neighbor)
    if not conns:
        raise RuntimeError(f"No direct link between {switch.name} and {neighbor.name}")
    intf_on_switch = conns[0][0]
    ofport = switch.cmd(f"ovs-vsctl get Interface {intf_on_switch.name} ofport").strip()
    if not ofport.isdigit():
        raise RuntimeError(f"Could not resolve ofport for {intf_on_switch.name}: {ofport!r}")
    return ofport


def main() -> None:
    setLogLevel("info")
    topo = GeantTopology()
    net = Mininet(
        topo=topo,
        switch=lambda name, **kw: OVSSwitch(name, failMode="secure", **kw),
        link=TCLink,
        controller=None,
    )
    net.addController("c0", controller=RemoteController, ip="127.0.0.1", port=6653)

    try:
        net.start()
        print("*** Network up:", len(net.switches), "switches,", len(net.hosts), "hosts")

        h1, h5, h35 = net.get("h1"), net.get("h5"), net.get("h35")
        s1, s35 = net.get("s1"), net.get("s35")
        h1.cmd(f"arp -s {h5.IP()} {h5.MAC()}")
        h5.cmd(f"arp -s {h1.IP()} {h1.MAC()}")
        h1.cmd(f"arp -s {h35.IP()} {h35.MAC()}")
        h35.cmd(f"arp -s {h1.IP()} {h1.MAC()}")

        print("*** REAL FORWARDING TEST: retrying ping h1 -> h5 for up to 90s (monitored flow, before congestion)")
        deadline = time.time() + 90
        while time.time() < deadline:
            result = h1.cmd(f"ping -c 2 -W 2 {h5.IP()}")
            if "0 received" not in result:
                print(result)
                break
            time.sleep(5)
        else:
            print("*** Baseline ping never succeeded -- aborting before inducing congestion")
            return

        # Direct single-link rules for the background flow (h1 <-> h35), bypassing RYU
        # entirely -- this saturates exactly link 0-4, independent of RYU's own
        # management of the real monitored pair's path.
        print("*** Installing direct single-link rules for background traffic (h1 <-> h35)")
        port_s1_to_s35 = get_ofport(s1, s35)
        port_s1_to_h1 = get_ofport(s1, h1)
        port_s35_to_s1 = get_ofport(s35, s1)
        port_s35_to_h35 = get_ofport(s35, h35)
        s1.cmd(f"ovs-ofctl -O {OF_VERSION} add-flow s1 priority=200,dl_type=0x0800,nw_dst={h35.IP()},actions=output:{port_s1_to_s35}")
        s1.cmd(f"ovs-ofctl -O {OF_VERSION} add-flow s1 priority=200,dl_type=0x0800,nw_dst={h1.IP()},actions=output:{port_s1_to_h1}")
        s35.cmd(f"ovs-ofctl -O {OF_VERSION} add-flow s35 priority=200,dl_type=0x0800,nw_dst={h1.IP()},actions=output:{port_s35_to_s1}")
        s35.cmd(f"ovs-ofctl -O {OF_VERSION} add-flow s35 priority=200,dl_type=0x0800,nw_dst={h35.IP()},actions=output:{port_s35_to_h35}")

        h35.cmd("iperf -s -u -i 1 > /tmp/iperf_server_bg.log 2>&1 &")
        time.sleep(0.5)

        print("*** Ramping real background UDP traffic on link 0-4 (h1 -> h35):", BACKGROUND_RATES_MBPS)
        for rate in BACKGROUND_RATES_MBPS:
            print(f"    -> {rate} Mbps for {STEP_SETTLE_S}s")
            h1.cmd(f"iperf -c {h35.IP()} -u -b {rate}M -t {STEP_SETTLE_S + 3} > /tmp/iperf_client_bg.log 2>&1 &")
            time.sleep(STEP_SETTLE_S)
            # Check the actual monitored flow's real RTT while congestion is live.
            print("    h1 -> h5 during congestion:", h1.cmd(f"ping -c 2 -W 2 {h5.IP()}").strip().splitlines()[-2:])

        h1.cmd("kill %iperf 2>/dev/null")
        h35.cmd("kill %iperf 2>/dev/null")
        print("*** Idling 10s -- watch the ryu-manager terminal for REROUTE DECISION and real utilization")
        time.sleep(10)
    finally:
        net.stop()


if __name__ == "__main__":
    main()
