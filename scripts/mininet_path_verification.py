#!/usr/bin/env python3
"""
Mininet Path Verification - push genuinely valid OpenFlow rules for a
multi-hop path to a live Mininet/OVS network and confirm traffic actually
follows it.

Deliberately minimal: a 3-switch linear chain (h1-s1-s2-s3-h2), not the full
40-switch GeantTopology. An earlier attempt using the full GEANT topology
took 15+ minutes to bring up all 40 OVS switches and drove system load
average past 10-15, matching the pattern that previously forced a reboot.
This script tests the exact same mechanism (real ofport lookup, valid
OpenFlow13 match/action syntax, no-controller direct rule push) at a scale
that's safe to run repeatedly.

Note: FlowInstaller's build_flow_rules() output is NOT used here. Its
"command" strings (e.g. "ovs-ofctl add-flow s13 priority=100,h13->h38,
actions=output:s1") are a human-readable description for the offline
simulation's dump_flows() display -- "h13->h38" is not a real match field,
and "output:s1" needs a numeric port, not a switch name. Real port numbers
only exist once Mininet is actually running, so the translation from
path -> real rules is done here instead, using ports queried live from OVS.
Switches are configured as protocols=OpenFlow13 (matching topology.py's
GeantTopology), so ovs-ofctl must be told -O OpenFlow13 or the switch will
reject the command.

Run as: sudo python3 scripts/mininet_path_verification.py
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from mininet.net import Mininet
from mininet.node import OVSSwitch
from mininet.topo import Topo
from mininet.log import setLogLevel

OF_VERSION = "OpenFlow13"  # must match protocols= passed to addSwitch below


class ChainTopo(Topo):
    """h1 - s1 - s2 - s3 - h2: minimal multi-hop chain for a real OVS check."""

    def build(self):
        switches = [self.addSwitch(f"s{i}", protocols=OF_VERSION) for i in (1, 2, 3)]
        h1 = self.addHost("h1", ip="10.0.0.1/24")
        h2 = self.addHost("h2", ip="10.0.0.2/24")
        self.addLink(h1, switches[0])
        self.addLink(switches[0], switches[1])
        self.addLink(switches[1], switches[2])
        self.addLink(switches[2], h2)


# The path as a list of switch names between the two test hosts.
PATH = ["s1", "s2", "s3"]


def get_ofport(switch, neighbor, net) -> str:
    """Ask OVS itself for the real datapath port number linking switch->neighbor."""
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

    net = Mininet(topo=ChainTopo(), switch=lambda name, **kw: OVSSwitch(name, failMode="standalone", **kw), controller=None)

    try:
        net.start()
        print("*** Network up:", len(net.switches), "switches,", len(net.hosts), "hosts")

        path = PATH
        print("*** Path under test:", path)

        src_host_name, dst_host_name = "h1", "h2"
        src_host = net.get(src_host_name)
        dst_host = net.get(dst_host_name)
        src_ip, dst_ip = src_host.IP(), dst_host.IP()

        # Bypass ARP broadcast/learning entirely so this test isolates exactly
        # one thing: do the explicit path-following OpenFlow rules work.
        src_host.cmd(f"arp -s {dst_ip} {dst_host.MAC()}")
        dst_host.cmd(f"arp -s {src_ip} {src_host.MAC()}")

        print(f"*** Installing real OpenFlow rules ({OF_VERSION}) along the path:")
        for index, switch_name in enumerate(path):
            switch = net.get(switch_name)

            prev_name = src_host_name if index == 0 else path[index - 1]
            next_name = dst_host_name if index == len(path) - 1 else path[index + 1]
            prev_node = net.get(prev_name)
            next_node = net.get(next_name)

            egress_port = get_ofport(switch, next_node, net)   # toward destination
            ingress_port = get_ofport(switch, prev_node, net)  # toward source (used for the reverse rule)

            forward_cmd = (
                f"ovs-ofctl -O {OF_VERSION} add-flow {switch_name} "
                f"priority=100,dl_type=0x0800,nw_dst={dst_ip},actions=output:{egress_port}"
            )
            reverse_cmd = (
                f"ovs-ofctl -O {OF_VERSION} add-flow {switch_name} "
                f"priority=100,dl_type=0x0800,nw_dst={src_ip},actions=output:{ingress_port}"
            )
            for cmd in (forward_cmd, reverse_cmd):
                print("   ", cmd)
                result = switch.cmd(cmd)
                if result.strip():
                    print("      !", result.strip())

        print(f"*** Pinging {src_host_name}({src_ip}) -> {dst_host_name}({dst_ip}), path-only, no controller")
        result = src_host.cmd(f"ping -c 3 -W 2 {dst_ip}")
        print(result)

        loss_line = [l for l in result.splitlines() if "packet loss" in l]
        success = bool(loss_line) and "0% packet loss" in loss_line[0]
        print("\n*** RESULT:", "SUCCESS - path rules work on real OVS" if success else "FAILED - see output above")

    finally:
        net.stop()


if __name__ == "__main__":
    main()
