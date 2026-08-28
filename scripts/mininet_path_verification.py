#!/usr/bin/env python3
"""
Mininet Path Verification - pushes genuinely valid OpenFlow rules for a
real GraphBuilder-computed path across the project's full 40-switch
GeantTopology and confirms traffic actually follows it.

Run as: sudo python3 scripts/mininet_path_verification.py
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from mininet.net import Mininet
from mininet.node import OVSSwitch
from mininet.link import TCLink
from mininet.log import setLogLevel

from topology import GeantTopology
from src.routing.graph_builder import GraphBuilder
from src.monitor.network_state import NetworkState

OF_VERSION = "OpenFlow13"  # must match protocols= passed in topology.py's addSwitch

# Same primary pair used throughout the offline experiments in this project
# (flow-video-1's own h3->h8, GEANT nodes "2"->"7"), so this real-network
# check is directly comparable to what the simulation already validated.
SRC_NODE = "2"
DST_NODE = "7"


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
    # Redirected-to-file stdout is block-buffered by default, which can make
    # a killed/timed-out run look "stuck" at whatever Mininet's own (flushed)
    # log last printed, even if this script's own prints already ran past it.
    sys.stdout.reconfigure(line_buffering=True)

    topo = GeantTopology()
    # secure (not standalone): GEANT is cyclic (61 edges over 40 nodes), and
    # standalone's implicit "NORMAL" table-miss action is a plain L2-learning
    # fallback with no loop prevention -- on a cyclic topology that means any
    # unmatched/broadcast packet floods forever and self-multiplies (confirmed
    # on a smaller cyclic subgraph: one switch's NORMAL rule hit 16 million
    # packets in ~2 minutes). secure mode drops unmatched traffic by default
    # instead, which also fits this project's actual design (no reliance on
    # switch auto-learning, only explicit pushed rules). ARP is unaffected
    # since both test hosts get static ARP entries below.
    net = Mininet(
        topo=topo,
        switch=lambda name, **kw: OVSSwitch(name, failMode="secure", **kw),
        link=TCLink,
        controller=None,
    )
    output_dir = PROJECT_ROOT / "results" / "mininet_check"
    output_dir.mkdir(parents=True, exist_ok=True)
    installed_rules: list[str] = []

    try:
        net.start()
        print("*** Network up:", len(net.switches), "switches,", len(net.hosts), "hosts")

        state = NetworkState(output_dir=output_dir)
        builder = GraphBuilder(state)
        path_nodes = builder.get_candidate_paths(SRC_NODE, DST_NODE, max_paths=1)[0]
        path = [topo.node_mapping[n][0] for n in path_nodes]
        print("*** Path under test (real GEANT nodes):", path_nodes, "->", path)

        src_host_name = topo.node_mapping[path_nodes[0]][1]
        dst_host_name = topo.node_mapping[path_nodes[-1]][1]
        src_host = net.get(src_host_name)
        dst_host = net.get(dst_host_name)
        src_ip, dst_ip = src_host.IP(), dst_host.IP()

        # Bypass ARP broadcast/learning entirely so this test isolates exactly
        # one thing: do the explicit path-following OpenFlow rules work.
        src_host.cmd(f"arp -s {dst_ip} {dst_host.MAC()}")
        dst_host.cmd(f"arp -s {src_ip} {src_host.MAC()}")

        # Rules are built here, not from FlowInstaller.build_flow_rules() -- its "command"
        # strings are a human-readable description for the offline simulator's display
        # (switch names instead of numeric ports), not valid ovs-ofctl input. Real port
        # numbers only exist once Mininet is running, so they're queried live from OVS below.
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
                installed_rules.append(cmd)
                result = switch.cmd(cmd)
                if result.strip():
                    print("      !", result.strip())

        print(f"*** Pinging {src_host_name}({src_ip}) -> {dst_host_name}({dst_ip}), path-only, no controller")
        ping_output = src_host.cmd(f"ping -c 3 -W 2 {dst_ip}")
        print(ping_output)

        loss_line = [l for l in ping_output.splitlines() if "packet loss" in l]
        success = bool(loss_line) and "0% packet loss" in loss_line[0]
        print("\n*** RESULT:", "SUCCESS - path rules work on real OVS" if success else "FAILED - see output above")

        report_path = output_dir / "verification_report.md"
        report_path.write_text(
            "# Mininet Path Verification Report\n\n"
            f"Generated: {datetime.now().isoformat()}\n\n"
            f"## Network\n{len(net.switches)} switches, {len(net.hosts)} hosts "
            "(full GeantTopology, failMode=secure, no controller)\n\n"
            f"## Path Under Test\nGEANT nodes: {path_nodes}\nSwitches: {path}\n\n"
            "## Installed OpenFlow Rules\n```\n" + "\n".join(installed_rules) + "\n```\n\n"
            f"## Ping Result ({src_host_name} -> {dst_host_name})\n```\n{ping_output}```\n\n"
            f"## Result\n{'SUCCESS' if success else 'FAILED'}\n",
            encoding="utf-8",
        )
        print(f"*** Report saved: {report_path}")

    finally:
        net.stop()


if __name__ == "__main__":
    main()
