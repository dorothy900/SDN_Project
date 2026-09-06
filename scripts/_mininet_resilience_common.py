#!/usr/bin/env python3
"""
Shared helpers for the two real-hardware resilience checks
(mininet_link_flap_check.py, mininet_abnormal_loss_check.py): building the
GEANT network, resolving OpenFlow ports, installing/clearing the
forward+reverse rules for a path, and feeding real measured telemetry for a
small set of links into a NetworkState -- the same rate/utilisation/loss math
scripts/ryu_apps/stability_aware_te.py runs, just without a controller process
(rules pushed straight with ovs-ofctl, no LLDP).

Not a standalone script. Imported by the two check scripts.
"""
from __future__ import annotations

import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mininet.net import Mininet
from mininet.node import OVSSwitch
from mininet.link import TCLink

from topology import GeantTopology
from src.monitor.models import LinkStatistics
from src.monitor.network_state import NetworkState
from src.monitor.statistics_collector import StatisticsCollector
from src.monitor.link_capacity import resolve_link_capacity_mbps

OF_VERSION = "OpenFlow13"


def link_id_of(u: str, v: str) -> str:
    a, b = sorted([str(u), str(v)])
    return "%s-%s" % (a, b)


def build_net() -> Tuple[Mininet, GeantTopology]:
    topo = GeantTopology()
    net = Mininet(
        topo=topo,
        switch=lambda name, **kw: OVSSwitch(name, failMode="secure", **kw),
        link=TCLink,
        controller=None,
    )
    return net, topo


def get_ofport(switch, neighbor) -> int:
    conns = switch.connectionsTo(neighbor)
    if not conns:
        raise RuntimeError("No direct link between %s and %s" % (switch.name, neighbor.name))
    intf = conns[0][0]
    ofport = switch.cmd("ovs-vsctl get Interface %s ofport" % intf.name).strip()
    if not ofport.lstrip("-").isdigit():
        raise RuntimeError("Could not resolve ofport for %s: %r" % (intf.name, ofport))
    return int(ofport)


def path_intf(net, topo, a_node: str, b_node: str):
    """The Mininet interface on a_node's switch facing b_node's switch (for tc netem)."""
    sw_a = net.get(topo.node_mapping[a_node][0])
    sw_b = net.get(topo.node_mapping[b_node][0])
    conns = sw_a.connectionsTo(sw_b)
    return conns[0][0] if conns else None


def install_path_rules(net, topo, path_nodes: List[str], src_ip: str, dst_ip: str) -> None:
    switch_path = [topo.node_mapping[n][0] for n in path_nodes]
    src_host = topo.node_mapping[path_nodes[0]][1]
    dst_host = topo.node_mapping[path_nodes[-1]][1]
    for i, sw_name in enumerate(switch_path):
        switch = net.get(sw_name)
        prev_name = src_host if i == 0 else switch_path[i - 1]
        next_name = dst_host if i == len(switch_path) - 1 else switch_path[i + 1]
        egress = get_ofport(switch, net.get(next_name))
        ingress = get_ofport(switch, net.get(prev_name))
        switch.cmd("ovs-ofctl -O %s add-flow %s priority=100,dl_type=0x0800,nw_dst=%s,actions=output:%d"
                   % (OF_VERSION, sw_name, dst_ip, egress))
        switch.cmd("ovs-ofctl -O %s add-flow %s priority=100,dl_type=0x0800,nw_dst=%s,actions=output:%d"
                   % (OF_VERSION, sw_name, src_ip, ingress))


def clear_path_rules(net, topo, path_nodes: List[str], src_ip: str, dst_ip: str) -> None:
    for sw_name in [topo.node_mapping[n][0] for n in path_nodes]:
        switch = net.get(sw_name)
        for ip in (src_ip, dst_ip):
            switch.cmd("ovs-ofctl -O %s del-flows %s dl_type=0x0800,nw_dst=%s" % (OF_VERSION, sw_name, ip))


def reinstall_if_changed(net, topo, old_path, new_path, src_ip, dst_ip) -> bool:
    if list(old_path) == list(new_path):
        return False
    clear_path_rules(net, topo, old_path, src_ip, dst_ip)
    install_path_rules(net, topo, new_path, src_ip, dst_ip)
    return True


class LinkTelemetry:
    """
    Polls a fixed set of links (node-pair tuples) via ovs-ofctl dump-ports and
    pushes a LinkStatistics per link into NetworkState each tick -- the RYU
    app's telemetry path, scoped to the links a check actually cares about so
    it isn't O(switches^2). status_override / loss_override stamp values a
    counter poll can't produce (an admin link-down, an iperf-measured loss).
    """

    def __init__(self, net, topo, links: List[Tuple[str, str]]):
        self.state: Optional[NetworkState] = None
        self.collector = StatisticsCollector(output_dir=PROJECT_ROOT / "results" / "mininet_resilience")
        self.plan: List[Tuple[str, str, int]] = []  # (link_id, switch_name, ofport)
        for a, b in links:
            sw = net.get(topo.node_mapping[a][0])
            port = get_ofport(sw, net.get(topo.node_mapping[b][0]))
            lid = link_id_of(a, b)
            self.plan.append((lid, sw.name, port))
            self.collector.set_link_capacity(sw.name, port, resolve_link_capacity_mbps(lid))

    def bind(self, state: NetworkState) -> "LinkTelemetry":
        self.state = state
        return self

    def poll(self, gap_s: float = 2.0,
             status_override: Optional[Dict[str, str]] = None,
             loss_override: Optional[Dict[str, float]] = None) -> None:
        status_override = status_override or {}
        loss_override = loss_override or {}
        switches = {sw for _, sw, _ in self.plan}
        for sw in switches:
            self.collector.calculate_rates(self.collector.parse_ovs_port_stats(sw), sample_time=time.time())
        time.sleep(gap_s)
        rated: Dict[str, Dict[int, object]] = {}
        for sw in switches:
            rated[sw] = {p.port: p for p in
                         self.collector.calculate_rates(self.collector.parse_ovs_port_stats(sw), sample_time=time.time())}
        for lid, sw, port in self.plan:
            pe = rated.get(sw, {}).get(port)
            if pe is None:
                continue
            util = self.collector.calculate_utilization(pe)
            loss = loss_override.get(lid, self.collector.calculate_loss_rate(pe))
            status = status_override.get(lid) or self.state.link_monitor.get_link_status(lid) or "up"
            self.state.update_link_statistics(LinkStatistics(
                timestamp=datetime.now(), link_id=lid, utilization=util,
                rx_mbps=pe.rx_mbps, tx_mbps=pe.tx_mbps, status=status, packet_loss=loss,
            ))


def start_udp_flow(src_host, dst_host, rate_mbps: int, seconds: int, out_dir: Path) -> None:
    dst_host.cmd("iperf -s -u -i 1 > %s/iperf_server.log 2>&1 &" % out_dir)
    time.sleep(1)
    src_host.cmd("iperf -c %s -u -b %dM -t %d -i 1 > %s/iperf_client.log 2>&1 &"
                 % (dst_host.IP(), rate_mbps, seconds, out_dir))


def stop_udp_flow(src_host, dst_host) -> None:
    src_host.cmd("pkill -f iperf 2>/dev/null")
    dst_host.cmd("pkill -f iperf 2>/dev/null")


def iperf_server_loss_series(out_dir: Path) -> List[float]:
    """Per-second loss fraction from the iperf UDP server log's '(X%)' field."""
    try:
        text = (out_dir / "iperf_server.log").read_text()
    except OSError:
        return []
    return [float(m) / 100.0 for m in re.findall(r"\(([\d.]+)%\)", text)]
