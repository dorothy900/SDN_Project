#!/usr/bin/env python3
"""
Mininet Jitter Check - real, independently-measured delay_jitter_score vs
loss_jitter_score (zeta/eta's underlying signals), to answer a question the
offline hybrid_congestion_churn_matrix.py experiment couldn't: are these two
genuinely independent, or does more delay dispersion really come with more
loss dispersion?

Why a real experiment is needed here: the offline hybrid_congestion_churn_
matrix.py experiment injects delay/loss via a nearest-neighbor lookup into
the same real Mininet sample every time, so within any rolling window,
delay_jitter and loss_jitter are mechanically driven by the same "which
real sample got picked" latent factor, not real, independently-varying
measurements -- an offline Spearman(delay_jitter, loss_jitter) of 1.000 is
too clean to trust given that.

This script feeds genuinely separate real measurements (real ping-based
delay, real tc-qdisc-based loss -- see mininet_loss_saturation_check.py
for why qdisc counters, not OVS's, are used for loss) through
NetworkState.update_link_statistics() in real time on a real, saturating
GEANT link (s5-s14), using the exact same DelayJitterTracker/
LossJitterTracker production code path (default window_seconds=60,
saturation_ms=150/saturation=0.20) -- not a synthetic clock, not a shared
lookup. Randomized request-rate order and trial count high enough that
multiple real samples fall inside a single 60s rolling window.

Run as: sudo python3 scripts/mininet/jitter_check.py
"""
from __future__ import annotations

import csv
import random
import sys
import time
from datetime import datetime
from pathlib import Path

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
from src.monitor.network_state import NetworkState
from src.monitor.models import LinkStatistics
from experiments.common.independence_stats import distance_correlation, permutation_test, spearman_rho

OF_VERSION = "OpenFlow13"
HOST_LINK_DELAY_MS = 1.0
RANDOM_SEED = 42

# Real GEANT link (nodes 12<->20), officially "155 Mbps", scaled to 20Mbit
# by topology.py -- the one real link in this project confirmed to actually
# saturate within this VM's iperf generation ceiling (see
# mininet_loss_saturation_check.py). Same link used throughout this
# session's loss/jitter work, for consistency.
NODE_U, NODE_V = "s5", "s14"

# Wide spread, more trials than the original loss-saturation pass, so
# consecutive real samples (each ~8s apart) densely populate the 60s
# rolling jitter window -- need several real samples inside one window for
# the independence test to have any power at all.
REQUESTED_RATES_MBPS = [4, 8, 12, 16, 18, 20, 24, 28, 35, 45]
TRIALS_PER_LEVEL = 6
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
    output_dir = PROJECT_ROOT / "results" / "jitter_check"
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

        # NetworkState with production DEFAULTS -- no synthetic clock, no
        # custom window/saturation -- this is meant to test the exact
        # trackers that ship in production, fed by genuinely real,
        # independent measurements.
        state = NetworkState(output_dir=output_dir)
        link_id_str = f"{NODE_U[1:]}-{NODE_V[1:]}"

        print(f"*** Real link {NODE_U}<->{NODE_V} (officially 155Mbps, configured at "
              f"{link_capacity_mbps}Mbit, interface {intf_name}) -- {len(plan)} samples, "
              f"requested rates {REQUESTED_RATES_MBPS}Mbit, randomized order")

        qdisc_tracker.calculate_loss_rate(intf_name, su.cmd(f"tc -s qdisc show dev {intf_name}"))

        for index, sample in enumerate(plan, start=1):
            hv.cmd("kill %iperf 2>/dev/null")
            hv.cmd(f"iperf -s -u -i 1 > /tmp/iperf_server_jitter.log 2>&1 &")
            time.sleep(0.3)

            t0_stats = collector.parse_ovs_port_stats(su.name)
            collector.calculate_rates(t0_stats, sample_time=time.time())

            hu.cmd(f"iperf -c {v_ip} -u -b {sample['rate_mbps']}M -t {IPERF_DURATION_S} > /tmp/iperf_client_jitter.log 2>&1 &")
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

            if port_entry is None or delay_ms is None or loss is None:
                print(f"   [{index}/{len(plan)}] rate={sample['rate_mbps']}M trial={sample['trial']}: SKIPPED")
                continue

            achieved_u = collector.calculate_utilization(port_entry)

            # Feed the real production trackers via the real facade, real
            # wall-clock time throughout (this is a live experiment, not an
            # offline synthetic-clock one -- see update_link_statistics's
            # docstring for why that distinction matters).
            now = time.time()
            state.update_link_statistics(
                LinkStatistics(
                    timestamp=datetime.now(), link_id=link_id_str, utilization=achieved_u,
                    rx_mbps=0.0, tx_mbps=0.0, status="up", delay_ms=delay_ms, packet_loss=loss,
                )
            )
            delay_jitter = state.get_delay_jitter_score(link_id_str, now=now)
            loss_jitter = state.get_loss_jitter_score(link_id_str, now=now)

            results.append({
                "requested_rate_mbps": sample["rate_mbps"], "trial": sample["trial"],
                "achieved_utilization": achieved_u, "delay_ms": delay_ms, "loss": loss,
                "delay_jitter_score": delay_jitter, "loss_jitter_score": loss_jitter,
            })
            print(f"   [{index}/{len(plan)}] rate={sample['rate_mbps']}M trial={sample['trial']}: "
                  f"u={achieved_u:.3f} delay={delay_ms:.3f}ms loss={loss:.5f} "
                  f"delay_jitter={delay_jitter:.4f} loss_jitter={loss_jitter:.4f}")

        n_total = len(results)
        nonzero = [r for r in results if r["delay_jitter_score"] > 0.0 or r["loss_jitter_score"] > 0.0]
        print(f"\n*** RESULT: {n_total} samples, {len(nonzero)} with non-cold-start jitter (either score > 0)")

        report_lines = [
            "# Mininet Jitter Check (real, independently-measured delay/loss jitter)",
            "",
            f"Generated: {datetime.now().isoformat()}",
            f"Real link {NODE_U}<->{NODE_V}, officially 155Mbps, configured at {link_capacity_mbps}Mbit here.",
            f"{n_total} samples, {len(nonzero)} with non-cold-start jitter scores.",
            "",
        ]
        both_nonzero = [r for r in results if r["delay_jitter_score"] > 0.0 and r["loss_jitter_score"] > 0.0]
        if len(both_nonzero) >= 5:
            dj = [r["delay_jitter_score"] for r in both_nonzero]
            lj = [r["loss_jitter_score"] for r in both_nonzero]
            rho, p = permutation_test(dj, lj, statistic_fn=spearman_rho, n_permutations=9999, seed=RANDOM_SEED)
            dcor, dp = permutation_test(dj, lj, statistic_fn=distance_correlation, n_permutations=9999, seed=RANDOM_SEED)
            report_lines.append(f"n(both jitter scores > 0) = {len(both_nonzero)}")
            report_lines.append(f"Spearman rho(delay_jitter, loss_jitter) = {rho:.4f}, p={p:.4f}")
            report_lines.append(f"dCor(delay_jitter, loss_jitter) = {dcor:.4f}, p={dp:.4f}")
            print(f"*** n(both>0)={len(both_nonzero)}  Spearman={rho:.4f} (p={p:.4f})  dCor={dcor:.4f} (p={dp:.4f})")
        else:
            report_lines.append(
                f"Not enough samples with both jitter scores > 0 (n={len(both_nonzero)}) for a meaningful test."
            )

        report_lines += ["", "## Raw samples", ""]
        for r in results:
            report_lines.append(
                f"- req={r['requested_rate_mbps']}M trial={r['trial']}: u={r['achieved_utilization']:.3f} "
                f"delay={r['delay_ms']:.3f}ms loss={r['loss']:.5f} "
                f"delay_jitter={r['delay_jitter_score']:.4f} loss_jitter={r['loss_jitter_score']:.4f}"
            )

        with (output_dir / "jitter_samples.csv").open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(results[0].keys()))
            w.writeheader()
            w.writerows(results)
        (output_dir / "jitter_report.md").write_text("\n".join(report_lines) + "\n", encoding="utf-8")
        print(f"*** Report saved: {output_dir / 'jitter_report.md'}")

    finally:
        net.stop()


if __name__ == "__main__":
    main()
