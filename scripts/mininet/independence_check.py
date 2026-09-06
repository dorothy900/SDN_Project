#!/usr/bin/env python3
"""
Mininet Independence Check v2 - real-hardware utilization/delay/loss
correlation experiment across four GEANT links, in randomized sample order.
Does NOT cover instability (delta/churn) or reliability (epsilon) -- those
need a differently-shaped experiment (see decision_churn_independence.py).

Run as: sudo python3 scripts/mininet/independence_check.py
"""
from __future__ import annotations

import csv
import random
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[2]
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
from experiments.independence_stats import (
    distance_correlation,
    permutation_test,
    spearman_rho,
    variance_inflation_factors,
)

OF_VERSION = "OpenFlow13"
HOST_LINK_DELAY_MS = 1.0

# Four real, distinct GEANT links (topology.py resolves real bandwidth per link).
# The first three sit at 100/150Mbit, comfortably above this VM's own iperf UDP
# generation ceiling (~50-55Mbit/s), so achieved_utilization on them tops out
# around 0.55 regardless of requested rate. s5-s14 ("155 Mbps" tier, scaled to
# 20Mbit) gets its own lower rate schedule below so the same generation ceiling
# reaches real saturation on it, covering the u>0.55 region the other three miss.
LINKS = [("s1", "s2"), ("s5", "s6"), ("s13", "s35"), ("s5", "s14")]

# Requested send rate in Mbit, not "target utilization fraction". The top two
# levels per link intentionally exceed real link capacity: the tc/htb shaper is
# a hard limit, so overshooting it deterministically forces near-saturation
# utilization (and real, observable loss) regardless of how precisely iperf
# hits its nominal send rate -- this is what samples the high-utilization region.
LINK_RATES_MBPS: Dict[Tuple[str, str], List[int]] = {
    ("s1", "s2"): [10, 20, 30, 40, 50, 60, 70, 85, 100, 130],
    ("s5", "s6"): [10, 20, 30, 40, 50, 60, 70, 85, 100, 130],
    ("s13", "s35"): [10, 20, 30, 40, 50, 60, 70, 85, 100, 130],
    ("s5", "s14"): [4, 8, 12, 16, 20, 24, 28, 35, 45, 60],
}
# 5, not the earlier pilot's 2: a smaller pilot left an unexplained dCor-significant
# residual signal (dCor=0.36, p=0.009); more real samples is the lever to check whether
# that's real structure or shrinks toward noise.
TRIALS_PER_LEVEL = 5
IPERF_DURATION_S = 8
RANDOM_SEED = 42


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
    u_ip, v_ip = hu.IP(), hv.IP()
    su.cmd(f"ovs-ofctl -O {OF_VERSION} del-flows {su.name} dl_type=0x0800,nw_dst={v_ip}")
    su.cmd(f"ovs-ofctl -O {OF_VERSION} del-flows {su.name} dl_type=0x0800,nw_dst={u_ip}")
    sv.cmd(f"ovs-ofctl -O {OF_VERSION} del-flows {sv.name} dl_type=0x0800,nw_dst={u_ip}")
    sv.cmd(f"ovs-ofctl -O {OF_VERSION} del-flows {sv.name} dl_type=0x0800,nw_dst={v_ip}")


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
    output_dir = PROJECT_ROOT / "results" / "independence_check"
    output_dir.mkdir(parents=True, exist_ok=True)

    # Build the full randomized sample plan up front -- this is what fixes
    # the time/utilization confound from the first-pass experiment.
    plan = [
        {"link": link, "rate_mbps": rate, "trial": trial}
        for link in LINKS
        for rate in LINK_RATES_MBPS[link]
        for trial in range(1, TRIALS_PER_LEVEL + 1)
    ]
    rng = random.Random(RANDOM_SEED)
    rng.shuffle(plan)

    results = []
    try:
        net.start()
        print("*** Network up:", len(net.switches), "switches,", len(net.hosts), "hosts")
        print(f"*** Randomized sample plan: {len(plan)} samples across {len(LINKS)} links, "
              f"{TRIALS_PER_LEVEL} trials/rate")

        collector = StatisticsCollector(
            output_dir=output_dir, config_path=str(PROJECT_ROOT / "config" / "topology.yaml")
        )
        # Real tc drop counters, not OVS port drop= -- OVS's counter is structurally
        # blind to tc/htb shaping drops, which matters most on s5-s14 (expected to saturate).
        qdisc_tracker = QdiscLossTracker()

        current_link = None
        current_nodes = None
        sending_port = None
        current_intf_name = None

        for index, sample in enumerate(plan, start=1):
            node_u, node_v = sample["link"]
            if current_link != (node_u, node_v):
                if current_nodes is not None:
                    su, sv, hu, hv = current_nodes
                    clear_single_link_rules(su, sv, hu, hv)
                su, sv = net.get(node_u), net.get(node_v)
                hu, hv = net.get(f"h{node_u[1:]}"), net.get(f"h{node_v[1:]}")
                hu.cmd(f"arp -s {hv.IP()} {hv.MAC()}")
                hv.cmd(f"arp -s {hu.IP()} {hu.MAC()}")
                sending_port = install_single_link_rules(su, sv, hu, hv)
                collector.set_link_capacity(su.name, int(sending_port), topo.get_link_bw_mbps(node_u, node_v))
                current_intf_name = su.connectionsTo(sv)[0][0].name
                # Seed the qdisc tracker with a first snapshot so this link's
                # first real sample already has a delta to compute against.
                qdisc_tracker.calculate_loss_rate(current_intf_name, su.cmd(f"tc -s qdisc show dev {current_intf_name}"))
                current_link = (node_u, node_v)
                current_nodes = (su, sv, hu, hv)
            su, sv, hu, hv = current_nodes
            v_ip = hv.IP()

            hv.cmd("kill %iperf 2>/dev/null")
            hv.cmd(f"iperf -s -u -i 1 > /tmp/iperf_server_{node_u}_{node_v}.log 2>&1 &")
            time.sleep(0.3)

            t0_stats = collector.parse_ovs_port_stats(su.name)
            collector.calculate_rates(t0_stats, sample_time=time.time())

            hu.cmd(f"iperf -c {v_ip} -u -b {sample['rate_mbps']}M -t {IPERF_DURATION_S} > /tmp/iperf_client.log 2>&1 &")
            time.sleep(2.0)
            ping_output = hu.cmd(f"ping -c 3 -W 2 {v_ip}")
            rtt_ms = parse_ping_avg_rtt_ms(ping_output)
            delay_ms = estimate_one_way_link_delay_ms(rtt_ms, HOST_LINK_DELAY_MS) if rtt_ms is not None else None
            time.sleep(4.5)  # let the 8s iperf burst finish before sampling t1

            t1_raw = collector.parse_ovs_port_stats(su.name)
            t1_stats = collector.calculate_rates(t1_raw, sample_time=time.time())
            port_entry = next((p for p in t1_stats if str(p.port) == str(sending_port)), None)
            tc_output = su.cmd(f"tc -s qdisc show dev {current_intf_name}")
            loss = qdisc_tracker.calculate_loss_rate(current_intf_name, tc_output)
            hv.cmd("kill %iperf 2>/dev/null")

            if port_entry is None or delay_ms is None or loss is None:
                print(f"   [{index}/{len(plan)}] {node_u}-{node_v} rate={sample['rate_mbps']}M "
                      f"trial={sample['trial']}: SKIPPED (no port stats, ping failure, or no qdisc delta)")
                continue

            achieved_u = collector.calculate_utilization(port_entry)
            results.append({
                "link": f"{node_u}-{node_v}", "requested_rate_mbps": sample["rate_mbps"],
                "trial": sample["trial"], "achieved_utilization": achieved_u,
                "delay_ms": delay_ms, "loss": loss,
            })
            print(f"   [{index}/{len(plan)}] {node_u}-{node_v} rate={sample['rate_mbps']}M "
                  f"trial={sample['trial']}: achieved_u={achieved_u:.3f} delay={delay_ms:.3f}ms loss={loss:.5f}")

        if current_nodes is not None:
            su, sv, hu, hv = current_nodes
            clear_single_link_rules(su, sv, hu, hv)

        # Dependence stats on both raw delay/loss and their residuals against
        # congestion_model.py's predicted curve -- the residual result doubles as
        # a calibration check for that curve.
        u_vals = [r["achieved_utilization"] for r in results]
        d_vals = [r["delay_ms"] for r in results]
        l_vals = [r["loss"] for r in results]
        d_res = [r["delay_ms"] - predicted_delay_ms(r["achieved_utilization"]) for r in results]
        l_res = [r["loss"] - predicted_loss(r["achieved_utilization"]) for r in results]

        n = len(results)
        print(f"\n*** RESULT: {n} usable samples across {len(LINKS)} links")

        report_lines = [
            "# Independence Check v2 (randomized order, 4 links incl. one below-generation-ceiling)",
            "",
            f"Generated: {datetime.now().isoformat()}",
            f"Samples: {n} (target {len(plan)}), links: {', '.join(f'{u}-{v}' for u, v in LINKS)}",
            "",
            "## Pairwise monotonic dependence (Spearman + permutation test)",
            "",
        ]
        for name, x, y in [
            ("utilization vs delay (raw)", u_vals, d_vals),
            ("utilization vs loss (raw)", u_vals, l_vals),
            ("utilization vs delay_residual", u_vals, d_res),
            ("utilization vs loss_residual", u_vals, l_res),
        ]:
            rho, p = permutation_test(x, y, statistic_fn=spearman_rho, n_permutations=9999, seed=RANDOM_SEED)
            line = f"- {name}: rho={rho:.4f}, p={p:.4f}"
            print(f"*** {line}")
            report_lines.append(line)

        report_lines += ["", "## Pairwise dependence beyond monotonic (distance correlation)", ""]
        for name, x, y in [
            ("utilization vs delay (raw)", u_vals, d_vals),
            ("utilization vs loss (raw)", u_vals, l_vals),
            ("utilization vs delay_residual", u_vals, d_res),
            ("utilization vs loss_residual", u_vals, l_res),
        ]:
            dcor, dcor_p = permutation_test(x, y, statistic_fn=distance_correlation, n_permutations=9999, seed=RANDOM_SEED)
            line = f"- {name}: dCor={dcor:.4f}, p={dcor_p:.4f}"
            print(f"*** {line}")
            report_lines.append(line)

        report_lines += ["", "## Joint collinearity (VIF, raw utilization/delay/loss)", ""]
        vifs = variance_inflation_factors({"utilization": u_vals, "delay": d_vals, "loss": l_vals})
        for name, vif in vifs.items():
            line = f"- VIF({name}) = {vif:.3f}"
            print(f"*** {line}")
            report_lines.append(line)

        report_lines += ["", "## Joint collinearity (VIF, utilization + residuals)", ""]
        vifs_res = variance_inflation_factors({"utilization": u_vals, "delay_residual": d_res, "loss_residual": l_res})
        for name, vif in vifs_res.items():
            line = f"- VIF({name}) = {vif:.3f}"
            print(f"*** {line}")
            report_lines.append(line)

        report_lines += [
            "",
            "## Not covered by this experiment",
            "",
            "delta (link instability/churn) and epsilon (reliability) are driven by real "
            "DecisionEngine reroute events, not raw traffic load -- this experiment only "
            "injects traffic via static OpenFlow rules, no DecisionEngine is running, so "
            "churn stays at 0 throughout. A separate, decision-driven experiment is needed "
            "for those two variables (see decision_churn_independence.py).",
            "",
            "## Raw samples",
            "",
        ]
        for r in results:
            report_lines.append(
                f"- {r['link']} req={r['requested_rate_mbps']}M trial={r['trial']}: "
                f"u={r['achieved_utilization']:.3f} delay={r['delay_ms']:.3f}ms loss={r['loss']:.5f}"
            )

        with (output_dir / "independence_samples.csv").open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(results[0].keys()))
            w.writeheader()
            w.writerows(results)
        (output_dir / "independence_report.md").write_text("\n".join(report_lines) + "\n", encoding="utf-8")
        print(f"*** Report saved: {output_dir / 'independence_report.md'}")

    finally:
        net.stop()


if __name__ == "__main__":
    main()
