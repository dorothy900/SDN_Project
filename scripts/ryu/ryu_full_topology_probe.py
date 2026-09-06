#!/usr/bin/env python3
"""
Mininet side of the real-GEANT-topology probe for the RYU stability-aware TE
app: brings up the actual 40-switch/61-edge GEANT topology (not the earlier
2-switch toy) against a real remote RYU controller, so switch registration,
LLDP link discovery, and real port-stats telemetry can all be checked
against the real topology size before building step 4/5 (flow pushing +
decision loop) on top.

failMode=secure (not the Mininet default "standalone"): GEANT is cyclic
(61 edges over 40 nodes), and standalone's implicit "flood unmatched
traffic" table-miss action self-multiplies on a cyclic topology into a
broadcast storm (this project already hit and fixed this once -- see
scripts/mininet/path_verification.py's own docstring).

Run as: sudo python3 scripts/ryu/ryu_full_topology_probe.py
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

        # h1/h5 = GEANT nodes 0/12, the app's real MONITORED_PAIRS entry. Static ARP
        # (not broadcast resolution) matches this project's existing Mininet scripts'
        # own convention -- this app does no ARP/L2 learning, only IP-destination-match
        # rules, so ARP has to be resolved another way regardless.
        h1, h5 = net.get("h1"), net.get("h5")
        h1.cmd(f"arp -s {h5.IP()} {h5.MAC()}")
        h5.cmd(f"arp -s {h1.IP()} {h1.MAC()}")

        # Retry ping over a real window instead of guessing a fixed settle time -- the
        # app's own decision loop retries every DECISION_INTERVAL_S until switch
        # registration + LLDP discovery are both ready for this pair's path, and how
        # long that actually takes varies with real machine/OVS load, not something
        # worth hardcoding a single sleep for.
        print("*** REAL FORWARDING TEST: retrying ping h1 -> h5 for up to 90s (the app's real monitored pair)")
        deadline = time.time() + 90
        succeeded = False
        attempt = 0
        while time.time() < deadline:
            attempt += 1
            result = h1.cmd(f"ping -c 2 -W 2 {h5.IP()}")
            received = "0 received" not in result
            print(f"    attempt {attempt}: {'OK' if received else 'no reply yet'}")
            if received:
                succeeded = True
                print(result)
                break
            time.sleep(5)
        if not succeeded:
            print("*** Still no reply after 90s -- this is a real finding, not expected; see the ryu-manager log")

        print("*** Unmanaged pairs (no rules installed -- 100% loss expected, for contrast)")
        pairs = [("h13", "h27"), ("h20", "h33")]
        for a, b in pairs:
            ha, hb = net.get(a), net.get(b)
            print(f"    ping {a} -> {b}: ", ha.cmd(f"ping -c 3 -W 2 {hb.IP()}").strip().splitlines()[-2:])

        print("*** Idling 15s -- watch the ryu-manager terminal for real, nonzero REAL LINK STATS")
        time.sleep(15)
    finally:
        net.stop()


if __name__ == "__main__":
    main()
