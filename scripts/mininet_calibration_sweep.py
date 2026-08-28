#!/usr/bin/env python3
"""
Mininet Multi-Link Calibration Sweep - real (utilization, delay, loss)
measurements across multiple real GEANT edges, reported against
congestion_model.py's predicted curves, to check whether the production
delay/loss curves' calibration holds beyond the single link they were
originally fit on.

Run as: sudo python3 scripts/mininet_calibration_sweep.py
    [--edges 12:20,21:27] [--rates 4,8,12,16,18,20,24,28,35,45] [--trials 3]
"""
from __future__ import annotations

import argparse
import csv
import random
import statistics as st
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
from src.routing.congestion_model import predicted_delay_ms, predicted_loss
from experiments.independence_stats import distance_correlation, permutation_test, spearman_rho

OF_VERSION = "OpenFlow13"
HOST_LINK_DELAY_MS = 1.0
RANDOM_SEED = 42

# congestion_delay_bump_ms's high-utilization shape rests on 22 of 23 real u>0.6 samples
# coming from ONE link (12<->20) -- these two edges break that single-link confound.
# Every real GEANT edge whose LinkLabel scales to a Mininet capacity this VM
# can actually saturate (see src/monitor/link_capacity.py's REAL_LINK_LABEL_
# TO_MBPS: "155 Mbps" -> 20Mbit, well under the ~50-55Mbit generation
# ceiling established in results/correlation_check and results/
# independence_check). Confirmed by direct graph query: these are the only
# two "155 Mbps" edges in the whole 61-edge topology.
DEFAULT_EDGES = [("12", "20"), ("21", "27")]
REQUESTED_RATES_MBPS = [4, 8, 12, 16, 18, 20, 24, 28, 35, 45]
TRIALS_PER_LEVEL = 3
IPERF_DURATION_S = 8
# Let the UDP flow reach steady state before either measurement window opens
# (iperf itself, unlike TCP, has no slow-start, but the switch/queue needs a
# moment to reach a steady occupancy). Measurement then happens entirely
# inside the ping call below -- see the loop's own comment for why this
# matters: utilization and delay must be measured over the same window or
# they describe different conditions.
WARMUP_S = 2.0
PING_COUNT = 5
PING_TIMEOUT_S = 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--edges", default=",".join("%s:%s" % e for e in DEFAULT_EDGES),
        help="comma-separated GEANT node-id pairs, e.g. 12:20,21:27",
    )
    parser.add_argument("--rates", default=",".join(str(r) for r in REQUESTED_RATES_MBPS))
    parser.add_argument("--trials", type=int, default=TRIALS_PER_LEVEL)
    return parser.parse_args()


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


def clear_single_link_rules(su, sv, hu, hv) -> None:
    for switch, ip in ((su, hv.IP()), (su, hu.IP()), (sv, hu.IP()), (sv, hv.IP())):
        switch.cmd(f"ovs-ofctl -O {OF_VERSION} del-flows {switch.name} dl_type=0x0800,nw_dst={ip}")


def measure_one_edge(net, topo, collector, node_u, node_v, rates, trials, output_dir) -> list:
    su_name, hu_name = topo.node_mapping[node_u]
    sv_name, hv_name = topo.node_mapping[node_v]
    su, sv = net.get(su_name), net.get(sv_name)
    hu, hv = net.get(hu_name), net.get(hv_name)
    u_ip, v_ip = hu.IP(), hv.IP()

    sending_port = install_single_link_rules(su, sv, hu, hv)
    hu.cmd(f"arp -s {v_ip} {hv.MAC()}")
    hv.cmd(f"arp -s {u_ip} {hu.MAC()}")

    link_capacity_mbps = topo.get_link_bw_mbps(su_name, sv_name)
    collector.set_link_capacity(su.name, int(sending_port), link_capacity_mbps)
    qdisc_tracker = QdiscLossTracker()
    intf_name = su.connectionsTo(sv)[0][0].name

    print(f"*** Real edge GEANT {node_u}<->{node_v} ({su_name}<->{sv_name}), "
          f"configured at {link_capacity_mbps}Mbit, interface {intf_name}")

    plan = [{"rate_mbps": rate, "trial": trial} for rate in rates for trial in range(1, trials + 1)]
    rng = random.Random(RANDOM_SEED)
    rng.shuffle(plan)

    qdisc_tracker.calculate_loss_rate(intf_name, su.cmd(f"tc -s qdisc show dev {intf_name}"))

    results = []
    for index, sample in enumerate(plan, start=1):
        hv.cmd("kill %iperf 2>/dev/null")
        hv.cmd("iperf -s -u -i 1 > /tmp/iperf_server_calib.log 2>&1 &")
        time.sleep(0.3)

        hu.cmd(f"iperf -c {v_ip} -u -b {sample['rate_mbps']}M -t {IPERF_DURATION_S} > /tmp/iperf_client_calib.log 2>&1 &")
        time.sleep(WARMUP_S)

        # Utilization and delay must come from the SAME window, or the two
        # aren't comparable: averaging utilization over a window wider than
        # (or offset from) the delay snapshot it's compared against makes
        # them describe different conditions, not the same one twice.
        # Bracketing t0/t1 tightly around the ping call below keeps
        # "achieved_utilization" describing exactly the window
        # "real_delay_ms" was measured in.
        t0_stats = collector.parse_ovs_port_stats(su.name)
        collector.calculate_rates(t0_stats, sample_time=time.time())

        ping_output = hu.cmd(f"ping -c {PING_COUNT} -W {PING_TIMEOUT_S} {v_ip}")
        rtt_ms = parse_ping_avg_rtt_ms(ping_output)
        delay_ms = estimate_one_way_link_delay_ms(rtt_ms, HOST_LINK_DELAY_MS) if rtt_ms is not None else None

        t1_raw = collector.parse_ovs_port_stats(su.name)
        t1_stats = collector.calculate_rates(t1_raw, sample_time=time.time())
        port_entry = next((p for p in t1_stats if str(p.port) == str(sending_port)), None)
        tc_output = su.cmd(f"tc -s qdisc show dev {intf_name}")
        loss = qdisc_tracker.calculate_loss_rate(intf_name, tc_output)
        hv.cmd("kill %iperf 2>/dev/null")

        if port_entry is None or loss is None or delay_ms is None:
            print(f"   [{index}/{len(plan)}] rate={sample['rate_mbps']}M trial={sample['trial']}: SKIPPED")
            continue

        achieved_u = collector.calculate_utilization(port_entry)
        # Layer-1 (single-link) curves only -- this does not calibrate simulation_common.py's
        # separate Layer-2 per-flow overlay, which only applies downstream of a path selection.
        predicted_delay = predicted_delay_ms(achieved_u)
        predicted_loss_v = predicted_loss(achieved_u)
        results.append({
            "edge": f"{node_u}-{node_v}",
            "requested_rate_mbps": sample["rate_mbps"], "trial": sample["trial"],
            "achieved_utilization": round(achieved_u, 6),
            "real_delay_ms": round(delay_ms, 4), "real_loss": round(loss, 6),
            "predicted_delay_ms": round(predicted_delay, 4), "predicted_loss": round(predicted_loss_v, 6),
            "delay_residual_ms": round(delay_ms - predicted_delay, 4),
            "loss_residual": round(loss - predicted_loss_v, 6),
        })
        print(f"   [{index}/{len(plan)}] rate={sample['rate_mbps']}M trial={sample['trial']}: "
              f"u={achieved_u:.3f} real_delay={delay_ms:.1f}ms (pred {predicted_delay:.1f}) "
              f"real_loss={loss:.5f} (pred {predicted_loss_v:.5f})")

    clear_single_link_rules(su, sv, hu, hv)
    return results


def main() -> None:
    args = parse_args()
    edges = [tuple(pair.split(":")) for pair in args.edges.split(",")]
    rates = [float(r) for r in args.rates.split(",")]
    trials = args.trials

    setLogLevel("info")
    sys.stdout.reconfigure(line_buffering=True)

    topo = GeantTopology()
    net = Mininet(
        topo=topo,
        switch=lambda name, **kw: OVSSwitch(name, failMode="secure", **kw),
        link=TCLink,
        controller=None,
    )
    output_dir = PROJECT_ROOT / "results" / "mininet_calibration_sweep"
    output_dir.mkdir(parents=True, exist_ok=True)

    all_results: list = []
    try:
        net.start()
        print("*** Network up:", len(net.switches), "switches,", len(net.hosts), "hosts")
        collector = StatisticsCollector(
            output_dir=output_dir, config_path=str(PROJECT_ROOT / "config" / "topology.yaml")
        )

        for node_u, node_v in edges:
            edge_results = measure_one_edge(net, topo, collector, node_u, node_v, rates, trials, output_dir)
            all_results.extend(edge_results)

        if not all_results:
            print("*** No usable samples collected -- nothing to report.")
            return

        with (output_dir / "calibration_samples.csv").open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(all_results[0].keys()))
            w.writeheader()
            w.writerows(all_results)
        print(f"\n*** Wrote {output_dir / 'calibration_samples.csv'} ({len(all_results)} samples, "
              f"{len(edges)} real edges)")

        report_lines = [
            "# Multi-Link Calibration Sweep", "",
            f"Generated: {datetime.now().isoformat()}",
            f"Edges: {edges}", f"{len(all_results)} total samples across {len(edges)} real GEANT links.", "",
        ]
        for node_u, node_v in edges:
            edge_key = f"{node_u}-{node_v}"
            subset = [r for r in all_results if r["edge"] == edge_key]
            if not subset:
                continue
            delay_resid = [r["delay_residual_ms"] for r in subset]
            loss_resid = [r["loss_residual"] for r in subset]
            u_vals = [r["achieved_utilization"] for r in subset]
            loss_vals = [r["real_loss"] for r in subset]
            n_nonzero_loss = sum(1 for l in loss_vals if l > 0.0)
            line = (
                f"## Edge {edge_key} (n={len(subset)}, {n_nonzero_loss} nonzero-loss)\n\n"
                f"- delay residual (real - Layer1 predicted): mean={st.mean(delay_resid):.1f}ms, "
                f"median={st.median(delay_resid):.1f}ms\n"
                f"- loss residual (real - Layer1 predicted): mean={st.mean(loss_resid):.5f}\n"
            )
            if n_nonzero_loss >= 3 and len(set(u_vals)) > 1:
                rho, p = permutation_test(u_vals, loss_vals, statistic_fn=spearman_rho, n_permutations=9999, seed=RANDOM_SEED)
                dcor = distance_correlation(u_vals, loss_vals)
                line += f"- Spearman rho(u, real loss) = {rho:.4f}, p={p:.4f}\n- dCor(u, real loss) = {dcor:.4f}\n"
                print(f"*** {edge_key}: Spearman rho(u,loss)={rho:.4f} p={p:.4f} dCor={dcor:.4f}")
            report_lines.append(line)
            print(f"*** {edge_key}: mean delay residual={st.mean(delay_resid):.1f}ms, "
                  f"mean loss residual={st.mean(loss_resid):.5f}")

        if len(edges) > 1:
            all_delay_resid = [r["delay_residual_ms"] for r in all_results]
            report_lines.append(
                f"## Cross-edge (all {len(edges)} edges combined, n={len(all_results)})\n\n"
                f"- delay residual: mean={st.mean(all_delay_resid):.1f}ms, stdev={st.stdev(all_delay_resid):.1f}ms\n\n"
                "If this mean/spread looks similar to the single-link (GEANT 12<->20) figure already "
                "documented in congestion_delay_bump_ms's own docstring, the high-u underprediction is "
                "corroborated across a second, independent link -- not just that one link's idiosyncrasy. "
                "If it differs substantially, that itself is new information the single-link fit couldn't see.\n"
            )
            print(f"\n*** Cross-edge delay residual: mean={st.mean(all_delay_resid):.1f}ms, "
                  f"stdev={st.stdev(all_delay_resid):.1f}ms")

        (output_dir / "calibration_report.md").write_text("\n".join(report_lines) + "\n", encoding="utf-8")
        print(f"*** Report saved: {output_dir / 'calibration_report.md'}")

    finally:
        net.stop()


if __name__ == "__main__":
    main()
