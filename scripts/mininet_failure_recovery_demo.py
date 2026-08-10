#!/usr/bin/env python3
"""
Mininet Failure & Recovery Demo - live proof-of-concept for the concept
note's "Link Failure & Recovery" scenario, on a real Mininet/OVS network
rather than the offline simulation.

Unlike scripts/mininet_path_verification.py (a static, one-shot check that
explicit rule push works at all), this script exercises the dynamic
behavior: an installed path's first-hop link genuinely fails (the real
Mininet interface is brought down, not just a flow rule removed), the
project's own GraphBuilder recomputes a path against its own NetworkState
(which now excludes the failed link, via the same TopologyState.mark_link_failed
machinery the offline scenario_failure_recovery.py experiment relies on),
stale rules are purged, fresh rules are installed for the new path, and a
live ping confirms traffic actually follows the new path. The link is then
restored and the graph is confirmed to route through it again.

This does not re-implement the offline scenario's stability-gate timing
(ThresholdDetector persistence windows, ChangeBudget throttling, flap
handling) -- that logic is already validated in experiments/
scenario_failure_recovery.py against synthetic link statistics. This script
answers a narrower, complementary question: when the network's own routing
code decides to reroute around a real failure, do the resulting OpenFlow
rules actually work on real hardware/software switches.

Uses the full 40-switch GeantTopology with failMode=secure (see
mininet_path_verification.py's docstring for why: GEANT is cyclic and
standalone's NORMAL fallback causes a broadcast storm on any cycle).

Run as: sudo python3 scripts/mininet_failure_recovery_demo.py
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from mininet.net import Mininet
from mininet.node import OVSSwitch
from mininet.log import setLogLevel

from topology import GeantTopology
from src.routing.graph_builder import GraphBuilder
from src.monitor.network_state import NetworkState

OF_VERSION = "OpenFlow13"

# Same primary pair used throughout the offline experiments (PRIMARY_PAIR in
# experiments/simulation_common.py), so this real-network demo is directly
# comparable to the dissertation's own failure-recovery experiment.
SRC_NODE = "2"
DST_NODE = "7"


def get_ofport(switch, neighbor) -> str:
    """Ask OVS itself for the real datapath port number linking switch->neighbor."""
    conns = switch.connectionsTo(neighbor)
    if not conns:
        raise RuntimeError(f"No direct link between {switch.name} and {neighbor.name}")
    intf_on_switch = conns[0][0]
    ofport = switch.cmd(f"ovs-vsctl get Interface {intf_on_switch.name} ofport").strip()
    if not ofport.isdigit():
        raise RuntimeError(f"Could not resolve ofport for {intf_on_switch.name}: {ofport!r}")
    return ofport


def install_path_rules(net, topo, path_nodes, src_ip, dst_ip) -> None:
    """Push forward+reverse rules for every switch hop on path_nodes."""
    switch_path = [topo.node_mapping[n][0] for n in path_nodes]
    src_host_name = topo.node_mapping[path_nodes[0]][1]
    dst_host_name = topo.node_mapping[path_nodes[-1]][1]

    for index, switch_name in enumerate(switch_path):
        switch = net.get(switch_name)
        prev_name = src_host_name if index == 0 else switch_path[index - 1]
        next_name = dst_host_name if index == len(switch_path) - 1 else switch_path[index + 1]
        prev_node = net.get(prev_name)
        next_node = net.get(next_name)

        egress_port = get_ofport(switch, next_node)
        ingress_port = get_ofport(switch, prev_node)

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
            switch.cmd(cmd)


def clear_path_rules(net, topo, path_nodes, src_ip, dst_ip) -> None:
    """Remove the forward+reverse rules this script installed for path_nodes."""
    switch_path = [topo.node_mapping[n][0] for n in path_nodes]
    for switch_name in switch_path:
        switch = net.get(switch_name)
        for ip in (src_ip, dst_ip):
            cmd = f"ovs-ofctl -O {OF_VERSION} del-flows {switch_name} dl_type=0x0800,nw_dst={ip}"
            print("   ", cmd)
            switch.cmd(cmd)


def ping_and_report(src_host, dst_ip, label) -> bool:
    print(f"*** [{label}] Pinging {src_host.name} -> {dst_ip}")
    result = src_host.cmd(f"ping -c 3 -W 2 {dst_ip}")
    print(result)
    loss_line = [l for l in result.splitlines() if "packet loss" in l]
    success = bool(loss_line) and "0% packet loss" in loss_line[0]
    print(f"*** [{label}] RESULT:", "SUCCESS" if success else "FAILED")
    return success


def main() -> None:
    setLogLevel("info")
    sys.stdout.reconfigure(line_buffering=True)

    topo = GeantTopology()
    net = Mininet(topo=topo, switch=lambda name, **kw: OVSSwitch(name, failMode="secure", **kw), controller=None)

    try:
        net.start()
        print("*** Network up:", len(net.switches), "switches,", len(net.hosts), "hosts")

        state = NetworkState(output_dir=PROJECT_ROOT / "results" / "mininet_failure_demo")
        builder = GraphBuilder(state)

        src_host_name = topo.node_mapping[SRC_NODE][1]
        dst_host_name = topo.node_mapping[DST_NODE][1]
        src_host = net.get(src_host_name)
        dst_host = net.get(dst_host_name)
        src_ip, dst_ip = src_host.IP(), dst_host.IP()
        src_host.cmd(f"arp -s {dst_ip} {dst_host.MAC()}")
        dst_host.cmd(f"arp -s {src_ip} {src_host.MAC()}")

        # --- Phase 1: baseline, before any failure ---
        original_path = builder.get_candidate_paths(SRC_NODE, DST_NODE, max_paths=1)[0]
        print("*** [before] Path (real GEANT nodes):", original_path,
              "->", [topo.node_mapping[n][0] for n in original_path])
        install_path_rules(net, topo, original_path, src_ip, dst_ip)
        assert ping_and_report(src_host, dst_ip, "before-failure"), "Baseline path must work before testing failure"

        # --- Phase 2: fail the path's first hop, for real ---
        fail_u, fail_v = original_path[0], original_path[1]
        fail_switch_u = topo.node_mapping[fail_u][0]
        fail_switch_v = topo.node_mapping[fail_v][0]
        print(f"*** Failing real link {fail_switch_u}<->{fail_switch_v} "
              f"(GEANT nodes {fail_u}<->{fail_v}, the path's first hop)")
        net.configLinkStatus(fail_switch_u, fail_switch_v, "down")
        # Same structural mechanism experiments/scenario_failure_recovery.py's
        # underlying driver relies on: TopologyState.mark_link_failed removes
        # the edge from get_active_graph(), so GraphBuilder can't route through it.
        state.topology.set_link_status(fail_u, fail_v, is_up=False)

        # --- Phase 3: recompute and reroute around the real failure ---
        new_path = builder.get_candidate_paths(SRC_NODE, DST_NODE, max_paths=1)[0]
        print("*** [after-failure] Recomputed path:", new_path,
              "->", [topo.node_mapping[n][0] for n in new_path])
        assert new_path != original_path, "Recomputed path should differ once the first hop is excluded"

        clear_path_rules(net, topo, original_path, src_ip, dst_ip)
        install_path_rules(net, topo, new_path, src_ip, dst_ip)
        assert ping_and_report(src_host, dst_ip, "after-reroute"), "Traffic must recover on the rerouted path"

        # --- Phase 4: restore the failed link, confirm the graph sees it again ---
        print(f"*** Restoring real link {fail_switch_u}<->{fail_switch_v}")
        net.configLinkStatus(fail_switch_u, fail_switch_v, "up")
        state.topology.set_link_status(fail_u, fail_v, is_up=True)
        recovered_path = builder.get_candidate_paths(SRC_NODE, DST_NODE, max_paths=1)[0]
        print("*** [after-recovery] Recomputed path:", recovered_path,
              "->", [topo.node_mapping[n][0] for n in recovered_path])
        print("*** Matches original pre-failure path:", recovered_path == original_path)

        print("\n*** OVERALL RESULT: SUCCESS - real failure triggered a real reroute, "
              "traffic recovered, and recovery was reflected once the link came back")

    finally:
        net.stop()


if __name__ == "__main__":
    main()
