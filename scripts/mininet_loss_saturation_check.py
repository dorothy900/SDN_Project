#!/usr/bin/env python3
"""
Mininet Loss Saturation Check - dedicated fix for the utilization-loss
measurement that failed twice (results/correlation_check/,
results/independence_check/): loss stayed exactly 0.0 across 70 combined
real samples, even at requested rates deliberately exceeding a 100Mbit link
cap. Root cause established by that point: achieved utilization never
exceeded ~50-55% regardless of requested rate, on multiple links -- the test
VM's own iperf UDP generation throughput is the real ceiling, not link
shaping (which is a hard tc/htb limit and should be trivial to exceed by
just requesting more).

Fix: stop fighting the generation ceiling, sidestep it. Test against a real
GEANT link whose *actual* configured capacity is already below what this VM
can generate. data/Geant2012.graphml has two real "155 Mbps" edges (the
project's smallest labeled tier); topology.py scales that to 20Mbit
(src/monitor/link_capacity.py) -- comfortably below the ~50-55Mbit/s ceiling
already observed twice, so genuine saturation (and therefore observable
loss) no longer depends on out-generating a VM's own packet-generation
throughput.

Also feeds into pending task: once real loss samples exist, fit
congestion_loss_bump's onset/scale to them the same way congestion_model's
scale_ms was fit to real delay samples.

Update, same day: sidestepping the generation ceiling worked (achieved
utilization reached 0.89), but loss *still* read 0.0 across all 30 samples.
A live diagnostic (tc -s qdisc show vs. ovs-ofctl dump-ports on the same
interface, same moment) found the real reason: OVS's own port drop= counter
does not see tc-netem/htb shaping drops at all -- confirmed 29104 real
dropped packets in `tc`'s own counters while OVS reported drop=0 for that
same interface. These are two separate accounting layers, not two views of
the same counter. Switched to src/monitor/qdisc_stats.py, which reads tc's
own counters directly, instead of StatisticsCollector.calculate_loss_rate()
(which remains correct for what it measures -- OVS-datapath-level drops --
just structurally blind to shaping-induced loss, which is exactly this
project's own dominant loss mechanism under congestion).

Run as: sudo python3 scripts/mininet_loss_saturation_check.py
"""
from __future__ import annotations

import csv
import random
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
from src.monitor.qdisc_stats import QdiscLossTracker
from experiments.independence_stats import distance_correlation, permutation_test, spearman_rho

OF_VERSION = "OpenFlow13"
HOST_LINK_DELAY_MS = 1.0
RANDOM_SEED = 42

# Real GEANT link (GEANT nodes 12<->20), officially "155 Mbps" per
# data/Geant2012.graphml's LinkLabel -- scaled to 20Mbit by topology.py,
# comfortably below this VM's own iperf UDP generation ceiling (~50-55Mbit/s,
# established in results/correlation_check/ and results/independence_check/).
NODE_U, NODE_V = "s5", "s14"

# Spans well below and well above the link's real ~20Mbit cap.
REQUESTED_RATES_MBPS = [4, 8, 12, 16, 18, 20, 24, 28, 35, 45]
TRIALS_PER_LEVEL = 3
IPERF_DURATION_S = 8


def get_ofport(switch, neighbor) -> str:
    conns = switch.connectionsTo(neighbor)
    if not conns:
        raise RuntimeError(f"No direct link between {switch.name} and {neighbor.name}")
    intf_on_switch = conns[0][0]
    ofport = switch.cmd(f"ovs-vsctl get Interface {intf_on_switch.name} ofport").strip()
    if not ofport.isdigit():
        raise RuntimeError(f"Could not resolve ofport for {intf_on_switch.name}: {ofport!r}")
    return ofport


def install_single_link_rules(su, sv, hu, hv) -> str:
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
    output_dir = PROJECT_ROOT / "results" / "loss_saturation_check"
    output_dir.mkdir(parents=True, exist_ok=True)

    plan = [
        {"rate_mbps": rate, "trial": trial}
        for rate in REQUESTED_RATES_MBPS
        for trial in range(1, TRIALS_PER_LEVEL + 1)
    ]
    rng = random.Random(RANDOM_SEED)
    rng.shuffle(plan)

    results = []
    try:
        net.start()
        print("*** Network up:", len(net.switches), "switches,", len(net.hosts), "hosts")

        su, sv = net.get(NODE_U), net.get(NODE_V)
        hu, hv = net.get(f"h{NODE_U[1:]}"), net.get(f"h{NODE_V[1:]}")
        u_ip, v_ip = hu.IP(), hv.IP()

        sending_port = install_single_link_rules(su, sv, hu, hv)
        hu.cmd(f"arp -s {v_ip} {hv.MAC()}")
        hv.cmd(f"arp -s {u_ip} {hu.MAC()}")

        collector = StatisticsCollector(
            output_dir=output_dir, config_path=str(PROJECT_ROOT / "config" / "topology.yaml")
        )
        link_capacity_mbps = topo.get_link_bw_mbps(NODE_U, NODE_V)
        collector.set_link_capacity(su.name, int(sending_port), link_capacity_mbps)
        qdisc_tracker = QdiscLossTracker()
        intf_name = su.connectionsTo(sv)[0][0].name

        print(f"*** Real link {NODE_U}<->{NODE_V} (GEANT nodes 12<->20, officially 155Mbps, "
              f"configured here at {link_capacity_mbps}Mbit, interface {intf_name}) -- {len(plan)} samples, "
              f"requested rates {REQUESTED_RATES_MBPS}Mbit, randomized order")

        # Seed the qdisc tracker with a first snapshot so the first real
        # sample below already has a delta to compute against.
        qdisc_tracker.calculate_loss_rate(intf_name, su.cmd(f"tc -s qdisc show dev {intf_name}"))

        for index, sample in enumerate(plan, start=1):
            hv.cmd("kill %iperf 2>/dev/null")
            hv.cmd(f"iperf -s -u -i 1 > /tmp/iperf_server_loss.log 2>&1 &")
            time.sleep(0.3)

            t0_stats = collector.parse_ovs_port_stats(su.name)
            collector.calculate_rates(t0_stats, sample_time=time.time())

            hu.cmd(f"iperf -c {v_ip} -u -b {sample['rate_mbps']}M -t {IPERF_DURATION_S} > /tmp/iperf_client_loss.log 2>&1 &")
            time.sleep(2.0)
            ping_output = hu.cmd(f"ping -c 3 -W 2 {v_ip}")
            rtt_ms = parse_ping_avg_rtt_ms(ping_output)
            delay_ms = estimate_one_way_link_delay_ms(rtt_ms, HOST_LINK_DELAY_MS) if rtt_ms is not None else None
            time.sleep(4.5)

            t1_raw = collector.parse_ovs_port_stats(su.name)
            t1_stats = collector.calculate_rates(t1_raw, sample_time=time.time())
            port_entry = next((p for p in t1_stats if str(p.port) == str(sending_port)), None)
            tc_output = su.cmd(f"tc -s qdisc show dev {intf_name}")
            loss = qdisc_tracker.calculate_loss_rate(intf_name, tc_output)
            hv.cmd("kill %iperf 2>/dev/null")

            if port_entry is None or loss is None:
                print(f"   [{index}/{len(plan)}] rate={sample['rate_mbps']}M trial={sample['trial']}: SKIPPED")
                continue

            achieved_u = collector.calculate_utilization(port_entry)
            results.append({
                "requested_rate_mbps": sample["rate_mbps"], "trial": sample["trial"],
                "achieved_utilization": achieved_u, "delay_ms": delay_ms, "loss": loss,
            })
            print(f"   [{index}/{len(plan)}] rate={sample['rate_mbps']}M trial={sample['trial']}: "
                  f"achieved_u={achieved_u:.3f} delay={delay_ms} ms loss={loss:.5f}")

        u_vals = [r["achieved_utilization"] for r in results]
        loss_vals = [r["loss"] for r in results]
        n_nonzero_loss = sum(1 for l in loss_vals if l > 0.0)

        print(f"\n*** RESULT: {len(results)} samples, {n_nonzero_loss} with nonzero loss")
        report_lines = [
            "# Loss Saturation Check", "",
            f"Generated: {datetime.now().isoformat()}",
            f"Real link {NODE_U}<->{NODE_V}, GEANT nodes 12<->20, officially 155Mbps, "
            f"configured at {link_capacity_mbps}Mbit here.",
            f"{len(results)} samples, {n_nonzero_loss} with nonzero measured loss.",
            "",
        ]
        if n_nonzero_loss >= 3 and len(set(u_vals)) > 1:
            rho, p = permutation_test(u_vals, loss_vals, statistic_fn=spearman_rho, n_permutations=9999, seed=RANDOM_SEED)
            dcor = distance_correlation(u_vals, loss_vals)
            report_lines.append(f"Spearman rho(utilization, loss) = {rho:.4f}, p={p:.4f}")
            report_lines.append(f"dCor(utilization, loss) = {dcor:.4f}")
            print(f"*** Spearman rho(u, loss) = {rho:.4f}, p={p:.4f}")
            print(f"*** dCor(u, loss) = {dcor:.4f}")
        else:
            report_lines.append("Not enough nonzero-loss samples for a meaningful correlation test.")

        report_lines += ["", "## Raw samples", ""]
        for r in results:
            report_lines.append(
                f"- req={r['requested_rate_mbps']}M trial={r['trial']}: "
                f"u={r['achieved_utilization']:.3f} delay={r['delay_ms']} ms loss={r['loss']:.5f}"
            )

        with (output_dir / "loss_samples.csv").open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(results[0].keys()))
            w.writeheader()
            w.writerows(results)
        (output_dir / "loss_report.md").write_text("\n".join(report_lines) + "\n", encoding="utf-8")
        print(f"*** Report saved: {output_dir / 'loss_report.md'}")

    finally:
        net.stop()


if __name__ == "__main__":
    main()
