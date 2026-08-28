#!/usr/bin/env python3
"""
Mininet Correlation Check - empirically test whether utilization, delay, and
packet loss are actually correlated on a real link, using real generated
traffic, not the offline simulator's hand-chosen queueing curves.

Motivation: GraphBuilder's cost formula assumes utilization/delay/loss are
"different symptoms of the same congestion" (the reason for beta/gamma's
residual pricing), and the offline simulator enforces that as a hard
deterministic function (congestion_model.py). Both are justified by real
queueing-theory intuition, but neither is evidence from this project's own
real network. This script generates that evidence: real UDP
traffic (iperf) sweeps one real link through a range of utilization levels,
and at each level, real delay (src/monitor/delay_prober.py, ping-based) and
real packet loss (StatisticsCollector, OVS drop counters -- the same
production code the live system uses) are measured. Utilization itself is
also measured from real OVS byte counters (StatisticsCollector.calculate_rates),
not read back from the iperf target -- iperf's requested bitrate and the link's
actually-achieved throughput diverge near saturation (netem/htb drops), so the
achieved value is what should correlate with delay/loss, not the requested one.

Spearman rank correlation (not Pearson) is used to test monotonic dependence
without assuming a specific functional form.

Run as: sudo python3 scripts/mininet_correlation_check.py
"""
from __future__ import annotations

import csv
import sys
import time
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from mininet.net import Mininet
from mininet.node import OVSSwitch
from mininet.link import TCLink
from mininet.log import setLogLevel

from topology import GeantTopology
from src.monitor.delay_prober import parse_ping_avg_rtt_ms, estimate_one_way_link_delay_ms
from src.monitor.statistics_collector import StatisticsCollector

OF_VERSION = "OpenFlow13"
HOST_LINK_DELAY_MS = 1.0

# One representative real GEANT link, same pair used throughout this
# project's other Mininet checks (s1<->s2, GEANT nodes "0"<->"1"-ish
# adjacency already exercised by mininet_delay_measurement.py's edge list).
NODE_U, NODE_V = "s1", "s2"

# Target utilization levels to sweep (fraction of this link's real
# configured bandwidth -- topology.py resolves per-link bandwidth from
# real GEANT data, see topo.get_link_bw_mbps() in main()). Achieved
# utilization is measured independently, not assumed to hit these
# exactly -- see module docstring.
TARGET_UTILIZATION_LEVELS = [0.1, 0.3, 0.5, 0.7, 0.9]
TRIALS_PER_LEVEL = 2
IPERF_DURATION_S = 5


def get_ofport(switch, neighbor) -> str:
    conns = switch.connectionsTo(neighbor)
    if not conns:
        raise RuntimeError(f"No direct link between {switch.name} and {neighbor.name}")
    intf_on_switch = conns[0][0]
    ofport = switch.cmd(f"ovs-vsctl get Interface {intf_on_switch.name} ofport").strip()
    if not ofport.isdigit():
        raise RuntimeError(f"Could not resolve ofport for {intf_on_switch.name}: {ofport!r}")
    return ofport


def install_single_link_rules(net, su, sv, hu, hv) -> None:
    u_ip, v_ip = hu.IP(), hv.IP()
    port_u_to_v = get_ofport(su, sv)
    port_u_to_hu = get_ofport(su, hu)
    port_v_to_u = get_ofport(sv, su)
    port_v_to_hv = get_ofport(sv, hv)
    su.cmd(f"ovs-ofctl -O {OF_VERSION} add-flow {su.name} priority=100,dl_type=0x0800,nw_dst={v_ip},actions=output:{port_u_to_v}")
    su.cmd(f"ovs-ofctl -O {OF_VERSION} add-flow {su.name} priority=100,dl_type=0x0800,nw_dst={u_ip},actions=output:{port_u_to_hu}")
    sv.cmd(f"ovs-ofctl -O {OF_VERSION} add-flow {sv.name} priority=100,dl_type=0x0800,nw_dst={u_ip},actions=output:{port_v_to_u}")
    sv.cmd(f"ovs-ofctl -O {OF_VERSION} add-flow {sv.name} priority=100,dl_type=0x0800,nw_dst={v_ip},actions=output:{port_v_to_hv}")
    return port_u_to_v


def spearman_rho(x: list, y: list) -> float:
    def rank(values):
        order = sorted(range(len(values)), key=lambda i: values[i])
        ranks = [0.0] * len(values)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
                j += 1
            avg_rank = (i + j) / 2 + 1
            for k in range(i, j + 1):
                ranks[order[k]] = avg_rank
            i = j + 1
        return ranks

    n = len(x)
    rx, ry = rank(x), rank(y)
    mean_rx, mean_ry = sum(rx) / n, sum(ry) / n
    cov = sum((rx[i] - mean_rx) * (ry[i] - mean_ry) for i in range(n))
    var_x = sum((r - mean_rx) ** 2 for r in rx)
    var_y = sum((r - mean_ry) ** 2 for r in ry)
    if var_x == 0 or var_y == 0:
        return 0.0
    return cov / (var_x * var_y) ** 0.5


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
    output_dir = PROJECT_ROOT / "results" / "correlation_check"
    output_dir.mkdir(parents=True, exist_ok=True)

    results = []
    try:
        net.start()
        print("*** Network up:", len(net.switches), "switches,", len(net.hosts), "hosts")

        su, sv = net.get(NODE_U), net.get(NODE_V)
        # Attached hosts follow this project's h<N> naming from topology.py.
        hu = net.get(f"h{NODE_U[1:]}")
        hv = net.get(f"h{NODE_V[1:]}")
        u_ip, v_ip = hu.IP(), hv.IP()

        sending_port = install_single_link_rules(net, su, sv, hu, hv)
        hu.cmd(f"arp -s {v_ip} {hv.MAC()}")
        hv.cmd(f"arp -s {u_ip} {hu.MAC()}")

        collector = StatisticsCollector(
            output_dir=output_dir, config_path=str(PROJECT_ROOT / "config" / "topology.yaml")
        )
        link_capacity_mbps = topo.get_link_bw_mbps(NODE_U, NODE_V)
        collector.set_link_capacity(su.name, int(sending_port), link_capacity_mbps)

        print(f"*** Sweeping real link {NODE_U}<->{NODE_V} (real configured capacity "
              f"{link_capacity_mbps}Mbit) through {len(TARGET_UTILIZATION_LEVELS)} "
              f"utilization levels x {TRIALS_PER_LEVEL} trials, measuring real utilization/delay/loss...")

        for level in TARGET_UTILIZATION_LEVELS:
            target_mbps = level * link_capacity_mbps
            for trial in range(1, TRIALS_PER_LEVEL + 1):
                hv.cmd("kill %iperf 2>/dev/null")
                hv.cmd(f"iperf -s -u -i 1 > /tmp/iperf_server_{NODE_U}_{NODE_V}.log 2>&1 &")
                time.sleep(0.3)

                t0_stats = collector.parse_ovs_port_stats(su.name)
                collector.calculate_rates(t0_stats, sample_time=time.time())

                hu.cmd(f"iperf -c {v_ip} -u -b {target_mbps}M -t {IPERF_DURATION_S} > /tmp/iperf_client.log 2>&1 &")
                time.sleep(1.5)
                ping_output = hu.cmd(f"ping -c 3 -W 2 {v_ip}")
                rtt_ms = parse_ping_avg_rtt_ms(ping_output)
                delay_ms = estimate_one_way_link_delay_ms(rtt_ms, HOST_LINK_DELAY_MS) if rtt_ms is not None else None
                time.sleep(2.5)  # let iperf finish its IPERF_DURATION_S burst before sampling t1

                t1_raw = collector.parse_ovs_port_stats(su.name)
                t1_stats = collector.calculate_rates(t1_raw, sample_time=time.time())
                port_entry = next((p for p in t1_stats if str(p.port) == str(sending_port)), None)

                hv.cmd("kill %iperf 2>/dev/null")

                if port_entry is None:
                    print(f"   [u_target={level:.1f} trial={trial}] SKIPPED: no port stats for port {sending_port}")
                    continue

                achieved_u = collector.calculate_utilization(port_entry)
                loss = collector.calculate_loss_rate(port_entry)

                results.append({
                    "target_utilization": level, "trial": trial,
                    "achieved_utilization": achieved_u,
                    "delay_ms": delay_ms, "loss": loss,
                })
                print(f"   [u_target={level:.1f} trial={trial}] achieved_u={achieved_u:.3f} "
                      f"delay={delay_ms} ms loss={loss:.5f}")

        clean = [r for r in results if r["delay_ms"] is not None]
        u_vals = [r["achieved_utilization"] for r in clean]
        delay_vals = [r["delay_ms"] for r in clean]
        loss_vals = [r["loss"] for r in clean]

        rho_delay = spearman_rho(u_vals, delay_vals) if len(clean) >= 3 else None
        rho_loss = spearman_rho(u_vals, loss_vals) if len(clean) >= 3 else None

        print(f"\n*** RESULT: {len(clean)}/{len(results)} samples usable")
        print(f"*** Spearman rho(utilization, delay) = {rho_delay}")
        print(f"*** Spearman rho(utilization, loss)  = {rho_loss}")

        with (output_dir / "correlation_samples.csv").open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(results[0].keys()))
            w.writeheader()
            w.writerows(results)

        (output_dir / "correlation_report.md").write_text(
            "# Real Link Utilization/Delay/Loss Correlation Check\n\n"
            f"Generated: {datetime.now().isoformat()}\n\n"
            f"## Method\nReal iperf UDP traffic swept link {NODE_U}<->{NODE_V} through "
            f"{len(TARGET_UTILIZATION_LEVELS)} target utilization levels x {TRIALS_PER_LEVEL} trials. "
            f"Achieved utilization measured from real OVS byte counters (StatisticsCollector, same "
            f"production code the live system uses), delay from real ping RTT (delay_prober.py), "
            f"loss from real OVS drop counters (StatisticsCollector.calculate_loss_rate). "
            f"Spearman rank correlation (monotonic dependence, not a specific functional form).\n\n"
            f"## Result\n{len(clean)}/{len(results)} samples usable.\n\n"
            f"- Spearman rho(utilization, delay) = {rho_delay}\n"
            f"- Spearman rho(utilization, loss) = {rho_loss}\n\n"
            f"## Raw samples\n\n"
            + "\n".join(
                f"- target_u={r['target_utilization']:.1f} trial={r['trial']}: "
                f"achieved_u={r['achieved_utilization']:.3f} delay={r['delay_ms']} ms loss={r['loss']:.5f}"
                for r in results
            )
            + "\n",
            encoding="utf-8",
        )
        print(f"*** Report saved: {output_dir / 'correlation_report.md'}")

    finally:
        net.stop()


if __name__ == "__main__":
    main()
