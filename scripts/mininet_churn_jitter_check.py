#!/usr/bin/env python3
"""
Mininet Churn-Jitter Check - real, independently-measured delay_jitter/
loss_jitter tested against real churn events, to settle an open question
mininet_jitter_check.py couldn't (it has no DecisionEngine, so churn stayed
0 throughout): does churn correlate with zeta/eta once measurement is
genuinely independent, or was the ~0.30-0.40 dCor found in
hybrid_congestion_churn_matrix.py (n=213) also an injection-mechanism
artifact?

Scope decision, stated up front: this does NOT run a real DecisionEngine
driving actual reroutes -- building a full closed loop (real candidate
paths, real FlowInstaller, real threshold/hysteresis/hold-down gating) is
a much larger undertaking than this specific question needs. What actually
matters for testing "does a churn event correlate with subsequent real
jitter" is that churn event *timing* is independently scheduled, not
correlated with the traffic pattern being measured -- a real DecisionEngine
reacting to real congestion would satisfy that (it churns when u crosses a
threshold, not based on delay/loss dispersion), and so does an
independently-randomized schedule. Recording real churn events (via
NetworkState.record_link_churn(), the same production call
DecisionEngine._execute_reroute() makes) at real times drawn independent of
the rate/trial schedule tests the same underlying question -- whether a
switch event's settle-window-excluded aftermath still correlates with real
jitter -- without needing the full routing/decision stack. Documented as a
real scope limitation, not hidden.

Run as: sudo python3 scripts/mininet_churn_jitter_check.py
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
from src.monitor.network_state import NetworkState
from src.monitor.models import LinkStatistics
from experiments.independence_stats import distance_correlation, permutation_test, spearman_rho

OF_VERSION = "OpenFlow13"
HOST_LINK_DELAY_MS = 1.0
RANDOM_SEED = 42

NODE_U, NODE_V = "s5", "s14"

REQUESTED_RATES_MBPS = [4, 8, 12, 16, 18, 20, 24, 28, 35, 45]
TRIALS_PER_LEVEL = 6
IPERF_DURATION_S = 8

# Independently-scheduled churn events -- see module docstring for why
# timing independence, not a real DecisionEngine, is what this test needs.
# ~1 churn event roughly every 2 samples, spread pseudo-randomly across the
# real run rather than at fixed intervals (avoids accidentally aliasing
# with the ~8s per-sample cadence).
CHURN_EVENT_PROBABILITY_PER_SAMPLE = 0.4


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
    output_dir = PROJECT_ROOT / "results" / "churn_jitter_check"
    output_dir.mkdir(parents=True, exist_ok=True)

    plan = [
        {"rate_mbps": rate, "trial": trial}
        for rate in REQUESTED_RATES_MBPS
        for trial in range(1, TRIALS_PER_LEVEL + 1)
    ]
    rng = random.Random(RANDOM_SEED)
    rng.shuffle(plan)
    # Independent RNG stream for churn scheduling, so churn timing isn't
    # entangled with the rate/trial shuffle above.
    churn_rng = random.Random(RANDOM_SEED + 1)

    results = []
    n_churn_events = 0
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

        state = NetworkState(output_dir=output_dir)
        link_id_str = f"{NODE_U[1:]}-{NODE_V[1:]}"

        print(f"*** Real link {NODE_U}<->{NODE_V} (officially 155Mbps, configured at "
              f"{link_capacity_mbps}Mbit, interface {intf_name}) -- {len(plan)} samples, "
              f"independent churn events at ~{CHURN_EVENT_PROBABILITY_PER_SAMPLE*100:.0f}%/sample")

        qdisc_tracker.calculate_loss_rate(intf_name, su.cmd(f"tc -s qdisc show dev {intf_name}"))

        for index, sample in enumerate(plan, start=1):
            hv.cmd("kill %iperf 2>/dev/null")
            hv.cmd(f"iperf -s -u -i 1 > /tmp/iperf_server_churn_jitter.log 2>&1 &")
            time.sleep(0.3)

            t0_stats = collector.parse_ovs_port_stats(su.name)
            collector.calculate_rates(t0_stats, sample_time=time.time())

            hu.cmd(f"iperf -c {v_ip} -u -b {sample['rate_mbps']}M -t {IPERF_DURATION_S} > /tmp/iperf_client_churn_jitter.log 2>&1 &")
            time.sleep(2.0)
            ping_output = hu.cmd(f"ping -c 3 -W 2 {v_ip}")
            rtt_ms = parse_ping_avg_rtt_ms(ping_output)
            delay_ms = estimate_one_way_link_delay_ms(rtt_ms, HOST_LINK_DELAY_MS) if rtt_ms is not None else None

            # Independently-scheduled churn event, drawn before the rest of
            # this sample's dwell time elapses -- see module docstring.
            if churn_rng.random() < CHURN_EVENT_PROBABILITY_PER_SAMPLE:
                state.record_link_churn(link_id_str, timestamp=time.time())
                n_churn_events += 1

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

            now = time.time()
            state.update_link_statistics(
                LinkStatistics(
                    timestamp=datetime.now(), link_id=link_id_str, utilization=achieved_u,
                    rx_mbps=0.0, tx_mbps=0.0, status="up", delay_ms=delay_ms, packet_loss=loss,
                ),
                now=now,
            )
            delay_jitter = state.get_delay_jitter_score(link_id_str, now=now)
            loss_jitter = state.get_loss_jitter_score(link_id_str, now=now)
            churn_score = state.get_link_churn_score(link_id_str, now=now)

            results.append({
                "requested_rate_mbps": sample["rate_mbps"], "trial": sample["trial"],
                "achieved_utilization": achieved_u, "delay_ms": delay_ms, "loss": loss,
                "delay_jitter_score": delay_jitter, "loss_jitter_score": loss_jitter,
                "churn_score": churn_score,
            })
            print(f"   [{index}/{len(plan)}] rate={sample['rate_mbps']}M trial={sample['trial']}: "
                  f"u={achieved_u:.3f} delay_jitter={delay_jitter:.4f} loss_jitter={loss_jitter:.4f} "
                  f"churn={churn_score:.4f}")

        n_total = len(results)
        print(f"\n*** RESULT: {n_total} samples, {n_churn_events} independent churn events recorded")

        report_lines = [
            "# Mininet Churn-Jitter Check (real, independently-measured jitter vs real churn events)",
            "",
            f"Generated: {datetime.now().isoformat()}",
            f"Real link {NODE_U}<->{NODE_V}, officially 155Mbps, configured at {link_capacity_mbps}Mbit here.",
            f"{n_total} samples, {n_churn_events} independently-scheduled churn events "
            f"(NOT DecisionEngine-triggered -- see module docstring for why timing "
            "independence is what this test needs, not a full decision loop).",
            "",
        ]
        nonzero_churn = [r for r in results if r["churn_score"] > 0.0]
        if len(nonzero_churn) >= 5 and len({r["churn_score"] for r in results}) > 1:
            churn = [r["churn_score"] for r in results]
            dj = [r["delay_jitter_score"] for r in results]
            lj = [r["loss_jitter_score"] for r in results]
            for name, other in [("delay_jitter_score", dj), ("loss_jitter_score", lj)]:
                rho, p = permutation_test(churn, other, statistic_fn=spearman_rho, n_permutations=9999, seed=RANDOM_SEED)
                dcor, dp = permutation_test(churn, other, statistic_fn=distance_correlation, n_permutations=9999, seed=RANDOM_SEED)
                line = f"churn_score vs {name}: Spearman={rho:.4f}(p={p:.4f})  dCor={dcor:.4f}(p={dp:.4f})"
                report_lines.append(line)
                print(f"*** {line}")
        else:
            report_lines.append("Not enough churn-score variation for a meaningful test.")

        report_lines += ["", "## Raw samples", ""]
        for r in results:
            report_lines.append(
                f"- req={r['requested_rate_mbps']}M trial={r['trial']}: u={r['achieved_utilization']:.3f} "
                f"delay_jitter={r['delay_jitter_score']:.4f} loss_jitter={r['loss_jitter_score']:.4f} "
                f"churn={r['churn_score']:.4f}"
            )

        with (output_dir / "churn_jitter_samples.csv").open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(results[0].keys()))
            w.writeheader()
            w.writerows(results)
        (output_dir / "churn_jitter_report.md").write_text("\n".join(report_lines) + "\n", encoding="utf-8")
        print(f"*** Report saved: {output_dir / 'churn_jitter_report.md'}")

    finally:
        net.stop()


if __name__ == "__main__":
    main()
