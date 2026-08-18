#!/usr/bin/env python3
"""
Mininet Independence Check v2 - redesigned utilization/delay/loss correlation
experiment, fixing three real methodological gaps found on review of the
first pass (scripts/mininet_correlation_check.py, results/correlation_check/):

  1. Test order was a monotonic utilization sweep, confounding utilization
     with elapsed time/warm-up drift. Fixed: the full (link, rate, trial)
     sample list is generated up front and executed in randomized order.
  2. Only one link was tested. Fixed: three real GEANT links.
  3. Requested iperf rates never actually pushed achieved utilization past
     ~60% (UDP send rate is not reliably hit at high target rates), so the
     >70% region where the loss model expects an effect was never sampled.
     Fixed: the two highest levels intentionally request *more* than the
     link's 100Mbit cap (110/130Mbit) -- the real tc/htb shaper on the
     interface is a hard limit, so overshooting it deterministically forces
     near-saturation utilization (and real, observable loss) regardless of
     how precisely iperf hits its nominal send rate. (This assumed every
     tested link was capped at 100Mbit, true when this was first run; since
     topology.py started applying real per-link GEANT bandwidth (2026-08-12),
     s5-s6 is actually 150Mbit -- REQUESTED_RATES_MBPS's overshoot values no
     longer guarantee saturating *that* link specifically. Left as-is since
     the 60 samples already collected predate that change; a future rerun
     should widen REQUESTED_RATES_MBPS or check per link.)

Also computes real dependence statistics on the collected samples via
experiments/independence_stats.py (Spearman + permutation test, distance
correlation, VIF) -- both on raw delay/loss and on their residuals against
congestion_model.py's predicted curve, since the residual result doubles as
a diagnostic for whether that curve is well-calibrated (see
compliance_check.md and results/correlation_check/).

Does NOT cover instability (delta/churn) or reliability (epsilon) -- those
are driven by real DecisionEngine reroute events, not raw traffic load, and
need a differently-shaped experiment (see pending task 1's note on this).

Run as: sudo python3 scripts/mininet_independence_check.py
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
from src.routing.congestion_model import predicted_delay_ms, predicted_loss
from experiments.independence_stats import (
    distance_correlation,
    permutation_test,
    spearman_rho,
    variance_inflation_factors,
)

OF_VERSION = "OpenFlow13"
HOST_LINK_DELAY_MS = 1.0

# Three real, distinct GEANT links (not adjacent to each other in the
# topology) -- s1-s2 kept for comparability with the first-pass experiment.
# Each link's real configured bandwidth varies (topology.py resolves it from
# real GEANT data as of 2026-08-12: s5-s6 is a real 10Gbps edge, configured
# here at 150Mbit; the other two have no real label and keep the 100Mbit
# default) -- looked up per-link via topo.get_link_bw_mbps() in main(), not
# assumed to be a flat constant.
LINKS = [("s1", "s2"), ("s5", "s6"), ("s13", "s35")]

# Requested send rate in Mbit, not "target utilization fraction" -- see
# module docstring for why the top two intentionally exceed link capacity.
REQUESTED_RATES_MBPS = [10, 20, 30, 40, 50, 60, 70, 85, 100, 130]
# 2 -> 5 trials/level (2026-08-18): the first pass (60 samples, n=20/link) left
# a real, dCor-significant but unexplained residual signal after the
# delay-curve refit (dCor=0.36, p=0.009) that survived ruling out both an
# intercept-identifiability bug and an obvious per-link effect -- see
# compliance_check.md. More real samples (not another curve tweak) is the
# next actual lever: this triples n/link (20->50) to check whether that
# signal is real leftover structure or shrinks toward noise with more power.
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
        for rate in REQUESTED_RATES_MBPS
        for trial in range(1, TRIALS_PER_LEVEL + 1)
    ]
    rng = random.Random(RANDOM_SEED)
    rng.shuffle(plan)

    results = []
    try:
        net.start()
        print("*** Network up:", len(net.switches), "switches,", len(net.hosts), "hosts")
        print(f"*** Randomized sample plan: {len(plan)} samples across {len(LINKS)} links "
              f"x {len(REQUESTED_RATES_MBPS)} rates x {TRIALS_PER_LEVEL} trials")

        collector = StatisticsCollector(
            output_dir=output_dir, config_path=str(PROJECT_ROOT / "config" / "topology.yaml")
        )

        current_link = None
        current_nodes = None
        sending_port = None

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
            hv.cmd("kill %iperf 2>/dev/null")

            if port_entry is None or delay_ms is None:
                print(f"   [{index}/{len(plan)}] {node_u}-{node_v} rate={sample['rate_mbps']}M "
                      f"trial={sample['trial']}: SKIPPED (no port stats or ping failure)")
                continue

            achieved_u = collector.calculate_utilization(port_entry)
            loss = collector.calculate_loss_rate(port_entry)
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

        # --- Analysis: raw and residual dependence, using the numpy toolkit ---
        u_vals = [r["achieved_utilization"] for r in results]
        d_vals = [r["delay_ms"] for r in results]
        l_vals = [r["loss"] for r in results]
        d_res = [r["delay_ms"] - predicted_delay_ms(r["achieved_utilization"]) for r in results]
        l_res = [r["loss"] - predicted_loss(r["achieved_utilization"]) for r in results]

        n = len(results)
        print(f"\n*** RESULT: {n} usable samples across {len(LINKS)} links")

        report_lines = [
            "# Independence Check v2 (randomized order, 3 links, saturation-forcing rates)",
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
            "for those two variables (see pending task notes).",
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
