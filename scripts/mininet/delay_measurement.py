#!/usr/bin/env python3
"""
Mininet Delay Measurement - real per-link delay via active probing (ping),
not a fabricated number.

Unlike utilization (byte counters) and packet loss (OVS drop counters, see
StatisticsCollector.calculate_loss_rate), delay/latency has no equivalent
field anywhere in `ovs-ofctl dump-ports` -- there is nothing to parse.
Real delay measurement needs a different mechanism: actually send a packet
and time how long it takes. Mininet makes this genuinely possible (not just
simulate-able) because GeantTopology's links are configured with real
tc-netem delay (topology.py: addLink(..., delay=self.link_delay)) -- traffic
crossing them really is delayed at the kernel level, and every switch has a
directly-attached host we can ping from.

Method, per real inter-switch link (u, v):
  1. Install explicit forward+reverse OpenFlow rules so a ping between the
     two switches' attached hosts crosses *exactly* this one link (same
     install_path_rules()/get_ofport() approach as
     mininet_path_verification.py and mininet_failure_recovery_demo.py,
     applied to a 2-node "path").
  2. Ping host_u -> host_v (3 packets), parse the real average RTT.
  3. Convert round-trip time to a one-way link-delay estimate
     (src/monitor/delay_prober.py: subtract the known host<->switch link
     overhead, halve the rest).
  4. Clear those rules before moving to the next link.

This measures every real GEANT link (61 edges) on the full 40-switch
topology and writes the results to results/mininet_check/delay_measurement.md
and .csv, and also demonstrates writing a measured value into a real
NetworkState/LinkStatistics record (proving this plugs into the same
pipeline StatisticsCollector already feeds, not a separate one-off number).

Run as: sudo python3 scripts/mininet/delay_measurement.py
"""
from __future__ import annotations

import csv
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

import networkx as nx
from mininet.net import Mininet
from mininet.node import OVSSwitch
from mininet.link import TCLink
from mininet.log import setLogLevel

from topology import GeantTopology
from src.monitor.delay_prober import parse_ping_avg_rtt_ms, estimate_one_way_link_delay_ms
from src.monitor.network_state import NetworkState
from src.monitor.models import LinkStatistics

OF_VERSION = "OpenFlow13"
HOST_LINK_DELAY_MS = 1.0  # matches topology.py's host<->switch addLink(delay="1ms")


def get_ofport(switch, neighbor) -> str:
    conns = switch.connectionsTo(neighbor)
    if not conns:
        raise RuntimeError(f"No direct link between {switch.name} and {neighbor.name}")
    intf_on_switch = conns[0][0]
    ofport = switch.cmd(f"ovs-vsctl get Interface {intf_on_switch.name} ofport").strip()
    if not ofport.isdigit():
        raise RuntimeError(f"Could not resolve ofport for {intf_on_switch.name}: {ofport!r}")
    return ofport


def install_single_link_rules(net, topo, node_u, node_v) -> None:
    """Forward+reverse rules so host_u<->host_v crosses exactly this one link."""
    switch_u, host_u = topo.node_mapping[node_u]
    switch_v, host_v = topo.node_mapping[node_v]
    su, sv = net.get(switch_u), net.get(switch_v)
    hu, hv = net.get(host_u), net.get(host_v)
    u_ip, v_ip = hu.IP(), hv.IP()

    port_u_to_v = get_ofport(su, sv)
    port_u_to_hu = get_ofport(su, hu)
    port_v_to_u = get_ofport(sv, su)
    port_v_to_hv = get_ofport(sv, hv)

    su.cmd(f"ovs-ofctl -O {OF_VERSION} add-flow {switch_u} priority=100,dl_type=0x0800,nw_dst={v_ip},actions=output:{port_u_to_v}")
    su.cmd(f"ovs-ofctl -O {OF_VERSION} add-flow {switch_u} priority=100,dl_type=0x0800,nw_dst={u_ip},actions=output:{port_u_to_hu}")
    sv.cmd(f"ovs-ofctl -O {OF_VERSION} add-flow {switch_v} priority=100,dl_type=0x0800,nw_dst={u_ip},actions=output:{port_v_to_u}")
    sv.cmd(f"ovs-ofctl -O {OF_VERSION} add-flow {switch_v} priority=100,dl_type=0x0800,nw_dst={v_ip},actions=output:{port_v_to_hv}")


def clear_single_link_rules(net, topo, node_u, node_v) -> None:
    switch_u, host_u = topo.node_mapping[node_u]
    switch_v, host_v = topo.node_mapping[node_v]
    su, sv = net.get(switch_u), net.get(switch_v)
    hu, hv = net.get(host_u), net.get(host_v)
    u_ip, v_ip = hu.IP(), hv.IP()
    su.cmd(f"ovs-ofctl -O {OF_VERSION} del-flows {switch_u} dl_type=0x0800,nw_dst={v_ip}")
    su.cmd(f"ovs-ofctl -O {OF_VERSION} del-flows {switch_u} dl_type=0x0800,nw_dst={u_ip}")
    sv.cmd(f"ovs-ofctl -O {OF_VERSION} del-flows {switch_v} dl_type=0x0800,nw_dst={u_ip}")
    sv.cmd(f"ovs-ofctl -O {OF_VERSION} del-flows {switch_v} dl_type=0x0800,nw_dst={v_ip}")


def main() -> None:
    setLogLevel("info")
    sys.stdout.reconfigure(line_buffering=True)

    topo = GeantTopology()
    net = Mininet(
        topo=topo,
        switch=lambda name, **kw: OVSSwitch(name, failMode="secure", **kw),
        link=TCLink,
        controller=None,
    )

    # Real GEANT inter-switch edges (same source topology.py itself reads).
    graph = nx.Graph(nx.read_graphml(PROJECT_ROOT / "data" / "Geant2012.graphml"))
    graph.remove_edges_from(nx.selfloop_edges(graph))
    edges = sorted(graph.edges(), key=lambda e: tuple(sorted(map(str, e))))

    results = []
    try:
        net.start()
        print("*** Network up:", len(net.switches), "switches,", len(net.hosts), "hosts")
        print(f"*** Measuring real delay on {len(edges)} links via ping...")

        state = NetworkState(output_dir=PROJECT_ROOT / "results" / "mininet_check")

        for index, (u, v) in enumerate(edges, start=1):
            switch_u, host_u = topo.node_mapping[str(u)]
            switch_v, host_v = topo.node_mapping[str(v)]
            hu, hv = net.get(host_u), net.get(host_v)

            install_single_link_rules(net, topo, str(u), str(v))
            hu.cmd(f"arp -s {hv.IP()} {hv.MAC()}")
            hv.cmd(f"arp -s {hu.IP()} {hu.MAC()}")

            ping_output = hu.cmd(f"ping -c 3 -W 2 {hv.IP()}")
            rtt_ms = parse_ping_avg_rtt_ms(ping_output)
            link_delay_ms = estimate_one_way_link_delay_ms(rtt_ms, HOST_LINK_DELAY_MS) if rtt_ms is not None else None

            clear_single_link_rules(net, topo, str(u), str(v))

            link_id = f"{switch_u}-{switch_v}"
            results.append({
                "link_id": link_id, "node_u": u, "node_v": v,
                "round_trip_ms": rtt_ms, "estimated_one_way_delay_ms": link_delay_ms,
            })
            print(f"   [{index}/{len(edges)}] {link_id}: RTT={rtt_ms} ms -> one-way≈{link_delay_ms} ms")

            if link_delay_ms is not None:
                # Demonstrates writing a real measured value into the same
                # NetworkState/LinkStatistics pipeline StatisticsCollector
                # already feeds -- not a separate, disconnected number.
                state.update_link_statistics(
                    LinkStatistics(
                        timestamp=datetime.now(), link_id=link_id, utilization=0.0,
                        rx_mbps=0.0, tx_mbps=0.0, status="up", delay_ms=link_delay_ms,
                    )
                )

        measured = [r for r in results if r["estimated_one_way_delay_ms"] is not None]
        failed = [r for r in results if r["estimated_one_way_delay_ms"] is None]
        print(f"\n*** RESULT: {len(measured)}/{len(results)} links measured successfully")

        output_dir = PROJECT_ROOT / "results" / "mininet_check"
        output_dir.mkdir(parents=True, exist_ok=True)
        with (output_dir / "delay_measurement.csv").open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(results[0].keys()))
            w.writeheader()
            w.writerows(results)

        avg_delay = sum(r["estimated_one_way_delay_ms"] for r in measured) / len(measured) if measured else 0.0
        (output_dir / "delay_measurement_report.md").write_text(
            "# Mininet Real Delay Measurement Report\n\n"
            f"Generated: {datetime.now().isoformat()}\n\n"
            f"## Method\nActive ping probing, one real link at a time (explicit OpenFlow rules force "
            f"traffic across exactly one hop), RTT converted to one-way delay via "
            f"src/monitor/delay_prober.py (subtract known host-link overhead, halve).\n\n"
            f"## Result\n{len(measured)}/{len(results)} of {len(edges)} real GEANT links measured "
            f"successfully. Average one-way delay: {avg_delay:.3f} ms "
            f"(topology.py configures {topo.link_delay} per inter-switch link -- this is independent "
            f"real-world confirmation, not reading the same config value back).\n\n"
            f"Failed links (if any): {[r['link_id'] for r in failed]}\n",
            encoding="utf-8",
        )
        print(f"*** Report saved: {output_dir / 'delay_measurement_report.md'}")

    finally:
        net.stop()


if __name__ == "__main__":
    main()
