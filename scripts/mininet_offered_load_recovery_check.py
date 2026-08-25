#!/usr/bin/env python3
"""
Mininet Offered-Load Recovery Risk Check - does the offered-load self-
influence correction's caution actually pay off on a real network when
background traffic genuinely worsens during an outage?

Background (see compliance_check.md's "Revoking the offered-load
correction" and the recovery-switchback fix sections, 2026-08-20): this
session found the offered-load correction blocking a real, if modest,
recovery-switchback improvement whenever the switchback candidate shares
no edges with the current path (PRIMARY_PAIR). Disabling the correction
closed that gap cleanly across the offline harness's 4-pair (later
17-pair) robustness check. But the offline harness never updates a
non-scripted edge's utilization after `build_network_state()`'s one-time
stamp -- so "the switchback candidate is still fine" was never actually
re-verified against a *changing* background there; it was true by
construction, not by live measurement. This script tests the real case
the offline harness structurally cannot: does disabling the correction
become unsafe once background traffic genuinely increases on the
recovering path *during* the outage?

Design: the --src/--dst pair (PRIMARY_PAIR by default) fails its first
hop, exactly like scripts/mininet_failure_recovery_demo.py, and is then
restored. While restored, this script sweeps several REAL iperf
background rates (--rates) injected directly between the hosts at two of
original_path's OTHER (non-failed) nodes -- every GEANT node has its own
host (topo.node_mapping), so this crosses the exact same switch-to-switch
link the switchback candidate would use, not a proxy. Real utilization on
that link is measured via StatisticsCollector (two ovs-ofctl dump-ports
polls, real rate calculation) at each rate, and PathCost.compare_paths is
evaluated on the real switchback decision TWICE per rate: once with
offered_load_mbps=None (this session's current default) and once with it
explicitly re-enabled -- looking for the rate, if any, where the two
disagree (the correction changes the outcome).

First run (2026-08-20, real result, single fixed rate=24Mbps): background
alone already made the switchback look worse than staying, so both with
and without the correction correctly rejected it -- an real, useful
result (confirms background genuinely can shift enough to matter), but
not the informative boundary case. Rewritten to sweep multiple lower
rates in one session instead of asking for repeated manual re-runs.

This has been reviewed for correctness (see compliance_check.md's
"Real Mininet offered-load risk experiment" section for the 3 bugs found
and fixed before the first run) and the first run above shows the overall
mechanism genuinely works end to end on a real network. The rate sweep
has been run once for real, on PRIMARY_PAIR only (see compliance_check.md's
"Re-enabling the offered-load correction for recovery switchback" section)
-- confirmed the correction matters at 4-16 Mbps injected background.

Parameterized 2026-08-24 (--src/--dst) so other pairs can be spot-checked
against the offline 23-pair generalization check's per-pair predictions
(compliance_check.md's "Failure/recovery node-pair generalization"
section, experiments/failure_recovery_generalization.py) without editing
this file -- e.g. a pair the offline harness found "full switchback
agreement" (`12->25`) vs. one it found "delay wins but switchback
declined" (`0->12`), to see whether that distinction holds on a real,
changing network too. NEIGHBOR_A/NEIGHBOR_B (the background-injection
edge) are already auto-derived from original_path for any pair -- no
manual review needed there. This sweep itself has NOT yet been re-run for
any pair other than the original PRIMARY_PAIR single-rate check above;
treat any --src/--dst other than the default as unverified until you run
it.

Re-parameterized again 2026-08-24 (--pairs) to sweep several node pairs in
one Mininet session -- this session's scenario design audit (finding: real
hardware had only ever cross-validated 2 of the 23 offline-generalized
pairs) asked for broader coverage without needing a fresh `sudo` session
per pair. --pairs takes precedence over --src/--dst when given; --src/--dst
remain as the single-pair shorthand for backward compatibility. Default
--pairs below spans the same tier1/tier2/tier3 background-data diversity
failure_recovery_generalization.py's own pair selection uses (2->7 is
PRIMARY_PAIR itself; 12->25 and 0->12 are the two pairs spot-checked
before this change; 3->36 and 16->23 add a tier1 and a tier3 pair neither
previously measured on real hardware).

Run as: sudo python3 scripts/mininet_offered_load_recovery_check.py
    [--pairs 2:7,12:25,0:12,3:36,16:23] [--rates R1,R2,...]
    [--src NODE] [--dst NODE]   # single-pair shorthand, ignored if --pairs given
"""
from __future__ import annotations

import argparse
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
from src.routing.graph_builder import GraphBuilder
from src.monitor.network_state import NetworkState
from src.monitor.statistics_collector import StatisticsCollector
from src.monitor.models import LinkStatistics
from src.decision.decision_engine import DecisionEngine
from experiments.simulation_common import PRIMARY_PAIR, build_network_state

OF_VERSION = "OpenFlow13"
# The flow BEING EVALUATED for switchback -- this project's real
# PRIMARY_PAIR demand (experiments/simulation_common.py), used only for
# offered_load_mbps in the cost comparison. Deliberately NOT the same
# variable as the injected background rate below -- they represent two
# different flows (ours vs. whatever shifted onto the recovering path).
# Reused as-is for any --src/--dst pair (the monitored flow's own real
# offered load doesn't depend on which pair is being checked).
FLOW_MBPS = 24.0


DEFAULT_PAIRS = "2:7,12:25,0:12,3:36,16:23"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--src", default=None, help="single-pair shorthand; ignored if --pairs is given")
    parser.add_argument("--dst", default=None, help="single-pair shorthand; ignored if --pairs is given")
    parser.add_argument(
        "--pairs", default=None,
        help=f"comma-separated src:dst GEANT node-id pairs to sweep in one session "
             f"(default when neither --pairs nor --src/--dst given: {DEFAULT_PAIRS})",
    )
    parser.add_argument(
        "--rates", default="4,8,12,16,20",
        help="comma-separated background iperf rates in Mbps to sweep (default: 4,8,12,16,20)",
    )
    return parser.parse_args()


def resolve_pairs(args: argparse.Namespace) -> list:
    if args.pairs:
        return [tuple(p.split(":")) for p in args.pairs.split(",")]
    if args.src or args.dst:
        return [(args.src or PRIMARY_PAIR[0], args.dst or PRIMARY_PAIR[1])]
    return [tuple(p.split(":")) for p in DEFAULT_PAIRS.split(",")]

# First run (2026-08-20, real result, PRIMARY_PAIR): a 24 Mbps injected
# background flow already made new_cost worse than old_cost even WITHOUT
# the offered-load correction (0.267531 vs 0.236009) -- both with and
# without the correction correctly rejected the switchback, so that run
# didn't land in the informative "boundary" zone (correction is the thing
# that flips the decision). Sweeping several lower rates in one Mininet
# session instead of one fixed rate finds where (if anywhere) that
# boundary sits, without requiring the user to manually re-run this
# multiple times. Default sweep below (--rates to override per pair).

# Background-traffic window per rate: how long the injected iperf flow runs
# before link stats are sampled (must be long enough for a real, stable
# rate to show up in two dump-ports polls), plus a cooldown so consecutive
# rates in the sweep don't overlap on the wire.
BACKGROUND_WARMUP_S = 4
BACKGROUND_DURATION_S = 12
POLL_GAP_S = 3
COOLDOWN_BETWEEN_RATES_S = 3


def get_ofport(switch, neighbor) -> str:
    conns = switch.connectionsTo(neighbor)
    if not conns:
        raise RuntimeError(f"No direct link between {switch.name} and {neighbor.name}")
    intf_on_switch = conns[0][0]
    ofport = switch.cmd(f"ovs-vsctl get Interface {intf_on_switch.name} ofport").strip()
    if not ofport.isdigit():
        raise RuntimeError(f"Could not resolve ofport for {intf_on_switch.name}: {ofport!r}")
    return ofport


def install_path_rules(net, topo, path_nodes, src_ip, dst_ip, log: list) -> None:
    switch_path = [topo.node_mapping[n][0] for n in path_nodes]
    src_host_name = topo.node_mapping[path_nodes[0]][1]
    dst_host_name = topo.node_mapping[path_nodes[-1]][1]
    for index, switch_name in enumerate(switch_path):
        switch = net.get(switch_name)
        prev_name = src_host_name if index == 0 else switch_path[index - 1]
        next_name = dst_host_name if index == len(switch_path) - 1 else switch_path[index + 1]
        egress_port = get_ofport(switch, net.get(next_name))
        ingress_port = get_ofport(switch, net.get(prev_name))
        for cmd in (
            f"ovs-ofctl -O {OF_VERSION} add-flow {switch_name} priority=100,dl_type=0x0800,nw_dst={dst_ip},actions=output:{egress_port}",
            f"ovs-ofctl -O {OF_VERSION} add-flow {switch_name} priority=100,dl_type=0x0800,nw_dst={src_ip},actions=output:{ingress_port}",
        ):
            print("   ", cmd)
            log.append(cmd)
            switch.cmd(cmd)


def clear_path_rules(net, topo, path_nodes, src_ip, dst_ip, log: list) -> None:
    for switch_name in (topo.node_mapping[n][0] for n in path_nodes):
        switch = net.get(switch_name)
        for ip in (src_ip, dst_ip):
            cmd = f"ovs-ofctl -O {OF_VERSION} del-flows {switch_name} dl_type=0x0800,nw_dst={ip}"
            print("   ", cmd)
            log.append(cmd)
            switch.cmd(cmd)


def measure_real_utilization(collector, switch, port, gap_s) -> float:
    """
    Two real dump-ports polls, `gap_s` apart. StatisticsCollector.
    calculate_rates keeps its own previous-sample state internally (keyed
    by switch+port) -- the first call only seeds that state (rates come
    back as 0.0, nothing to diff against yet); the second call, after the
    real time gap, computes the actual rate from the real byte-counter
    delta. calculate_utilization then reads capacity vs. that real rate.
    """
    first_raw = collector.parse_ovs_port_stats(switch.name)
    collector.calculate_rates(first_raw, sample_time=time.time())
    time.sleep(gap_s)
    second_raw = collector.parse_ovs_port_stats(switch.name)
    second_rated = collector.calculate_rates(second_raw, sample_time=time.time())
    by_port = {p.port: p for p in second_rated}
    if port not in by_port:
        raise RuntimeError(f"Port {port} not found in dump-ports output for {switch.name}")
    return collector.calculate_utilization(by_port[port])


def run_pair(net, topo, src_node: str, dst_node: str, background_rates_mbps: list) -> bool:
    """
    One pair's full failure -> restore -> background-rate-sweep check,
    against an already-running `net`. Returns whether a boundary (a rate
    where the offered-load correction flips the switchback decision) was
    found for this pair. Split out of main() 2026-08-24 so --pairs can run
    several pairs against a single `net.start()`/`net.stop()` -- Mininet
    startup/teardown is the dominant per-run cost, so this is what makes
    sweeping 5 pairs in one sudo session practical instead of asking for 5
    separate manual re-runs.
    """
    output_dir = PROJECT_ROOT / "results" / "mininet_offered_load_recovery_check" / f"{src_node}_{dst_node}"
    output_dir.mkdir(parents=True, exist_ok=True)
    rule_log: list = []

    print(f"\n{'='*70}\n*** PAIR {src_node} -> {dst_node}\n{'='*70}")

    # Seed every edge with the same real-data-calibrated baseline the
    # offline harness uses (experiments/simulation_common.py's
    # build_network_state -- SNDlib-grounded where matched, the fixed
    # fallback range elsewhere), NOT a bare NetworkState() -- a bare one
    # leaves every edge with no stats at all except whichever single
    # edge this script measures for real, so PathCost.calculate_path_
    # cost's "no data" flat 1.0-per-edge fallback would dominate the
    # comparison by hop count instead of by the real background-load
    # signal this script exists to test. Only the specific background
    # edge measured below gets its calibrated value overwritten with a
    # real, freshly-measured one -- everything else stays exactly the
    # baseline the rest of this session's results are grounded in.
    state = build_network_state(output_dir, seed=1)
    builder = GraphBuilder(state)
    collector = StatisticsCollector(output_dir=output_dir)

    src_host_name = topo.node_mapping[src_node][1]
    dst_host_name = topo.node_mapping[dst_node][1]
    src_host, dst_host = net.get(src_host_name), net.get(dst_host_name)
    src_ip, dst_ip = src_host.IP(), dst_host.IP()
    src_host.cmd(f"arp -s {dst_ip} {dst_host.MAC()}")
    dst_host.cmd(f"arp -s {src_ip} {src_host.MAC()}")

    original_path = builder.get_candidate_paths(src_node, dst_node, max_paths=1)[0]
    print("*** [before] original_path:", original_path)
    install_path_rules(net, topo, original_path, src_ip, dst_ip, rule_log)

    # --- Fail the first hop ---
    fail_u, fail_v = original_path[0], original_path[1]
    fail_switch_u, fail_switch_v = topo.node_mapping[fail_u][0], topo.node_mapping[fail_v][0]
    print(f"*** Failing {fail_switch_u}<->{fail_switch_v} (GEANT {fail_u}<->{fail_v})")
    net.configLinkStatus(fail_switch_u, fail_switch_v, "down")
    state.topology.set_link_status(fail_u, fail_v, is_up=False)

    new_path = builder.get_candidate_paths(src_node, dst_node, max_paths=1)[0]
    print("*** [after-failure] new_path:", new_path)
    clear_path_rules(net, topo, original_path, src_ip, dst_ip, rule_log)
    install_path_rules(net, topo, new_path, src_ip, dst_ip, rule_log)

    # --- Identify the background-traffic edge: two of original_path's
    # OTHER (non-failed) nodes -- ADJUST if your real GEANT numbering
    # differs: they must be adjacent nodes on original_path, neither
    # equal to fail_u/fail_v. ---
    remaining = [n for n in original_path if n not in (fail_u, fail_v)]
    if len(remaining) < 2:
        raise RuntimeError(
            "original_path too short to pick a background-traffic edge "
            "distinct from the failed hop -- pick a different SRC/DST pair"
        )
    neighbor_a, neighbor_b = remaining[0], remaining[1]
    bg_host_a = net.get(topo.node_mapping[neighbor_a][1])
    bg_host_b = net.get(topo.node_mapping[neighbor_b][1])
    bg_a_ip, bg_b_ip = bg_host_a.IP(), bg_host_b.IP()
    bg_host_a.cmd(f"arp -s {bg_b_ip} {bg_host_b.MAC()}")
    bg_host_b.cmd(f"arp -s {bg_a_ip} {bg_host_a.MAC()}")
    # This 2-node segment needs its own forward+reverse OpenFlow rules --
    # failMode=secure with no controller drops anything unrecognized, the
    # same reason the main SRC/DST path needed install_path_rules above.
    install_path_rules(net, topo, [neighbor_a, neighbor_b], bg_a_ip, bg_b_ip, rule_log)
    switch_a = net.get(topo.node_mapping[neighbor_a][0])
    switch_b = net.get(topo.node_mapping[neighbor_b][0])
    edge_port_on_a = int(get_ofport(switch_a, switch_b))
    edge_link_id = f"{min(neighbor_a, neighbor_b)}-{max(neighbor_a, neighbor_b)}"

    # --- Restore the failed link now -- the recovery-switchback cost
    # comparison needs original_path's edges back in the active graph
    # (a still-down link is excluded from it entirely). Only the
    # comparison behavior is under test from here on, not failure
    # detection itself, so this only needs to happen once, before the
    # whole rate sweep, not once per rate. ---
    print(f"*** Restoring {fail_switch_u}<->{fail_switch_v}")
    net.configLinkStatus(fail_switch_u, fail_switch_v, "up")
    state.topology.set_link_status(fail_u, fail_v, is_up=True)
    engine = DecisionEngine(state)

    # --- Sweep background rates in one Mininet session, so the
    # "boundary" (where offered-load correction is the deciding factor,
    # not just background alone) can be found without the user manually
    # re-running this multiple times. ---
    sweep_results = []
    for rate_mbps in background_rates_mbps:
        print(f"\n*** [rate={rate_mbps}Mbps] Injecting real background iperf: "
              f"{bg_host_a.name} -> {bg_host_b.name} (crosses GEANT edge "
              f"{neighbor_a}<->{neighbor_b}, part of the switchback candidate)")
        bg_host_b.cmd("iperf -s -u &")
        time.sleep(1)
        bg_host_a.cmd(f"iperf -c {bg_b_ip} -u -b {rate_mbps}M -t {BACKGROUND_DURATION_S} &")
        time.sleep(BACKGROUND_WARMUP_S)

        real_utilization = measure_real_utilization(collector, switch_a, edge_port_on_a, POLL_GAP_S)
        print(f"*** [rate={rate_mbps}Mbps] Real measured utilization on "
              f"{neighbor_a}<->{neighbor_b}: {real_utilization:.4f}")
        state.update_link_statistics(
            LinkStatistics(
                timestamp=datetime.now(), link_id=edge_link_id, utilization=real_utilization,
                rx_mbps=0.0, tx_mbps=0.0, status="up", delay_ms=None, packet_loss=None,
            )
        )

        comparison_without = engine.path_cost.compare_paths(new_path, original_path, min_abs_reduction=0.0, min_rel_reduction=0.0)
        comparison_with = engine.path_cost.compare_paths(
            new_path, original_path, min_abs_reduction=0.0, min_rel_reduction=0.0, offered_load_mbps=FLOW_MBPS
        )
        sweep_results.append((rate_mbps, real_utilization, comparison_without, comparison_with))
        print(f"  without correction: accepted={comparison_without['accepted']} "
              f"old_cost={comparison_without['old_cost']} new_cost={comparison_without['new_cost']}")
        print(f"  with correction:    accepted={comparison_with['accepted']} "
              f"old_cost={comparison_with['old_cost']} new_cost={comparison_with['new_cost']}")

        # Clean stop before the next rate, so consecutive iperf runs
        # don't overlap on the wire and bias the next measurement.
        # pkill by command name rather than a bash job-spec (%iperf) --
        # more robust across however mininet.node.Host.cmd() invokes
        # the shell for each host.
        bg_host_a.cmd("pkill -f iperf 2>/dev/null")
        bg_host_b.cmd("pkill -f iperf 2>/dev/null")
        time.sleep(COOLDOWN_BETWEEN_RATES_S)

    print("\n*** SWEEP SUMMARY (looking for the boundary where the correction flips the decision)")
    boundary_found = False
    for rate_mbps, real_utilization, comparison_without, comparison_with in sweep_results:
        tag = "SAME" if comparison_without["accepted"] == comparison_with["accepted"] else "*** FLIPS ***"
        if tag == "*** FLIPS ***":
            boundary_found = True
        print(f"  rate={rate_mbps:5.1f}Mbps  utilization={real_utilization:.4f}  "
              f"without_accepted={comparison_without['accepted']!s:5s}  "
              f"with_accepted={comparison_with['accepted']!s:5s}  [{tag}]")
    if boundary_found:
        print("\n  -> Found at least one rate where the correction changes the outcome -- "
              "disabling it is not free at that background level.")
    else:
        print("\n  -> No rate in this sweep flipped the decision -- either widen "
              "--rates further, or this specific edge/pair may not "
              "expose the risk at any realistic background level.")

    report_path = output_dir / "report.md"
    lines = [
        "# Offered-Load Recovery Risk Check (rate sweep)\n",
        f"Generated: {datetime.now().isoformat()}\n",
        f"original_path: {original_path}\nnew_path (post-failure): {new_path}\n",
        f"Background injected on: {neighbor_a}<->{neighbor_b}\n",
        "\n| rate (Mbps) | utilization | without_accepted | with_accepted | without_new_cost | with_new_cost |",
        "|---|---|---|---|---|---|",
    ]
    for rate_mbps, real_utilization, comparison_without, comparison_with in sweep_results:
        lines.append(
            f"| {rate_mbps} | {real_utilization:.4f} | {comparison_without['accepted']} | "
            f"{comparison_with['accepted']} | {comparison_without['new_cost']} | {comparison_with['new_cost']} |"
        )
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"*** Report saved: {report_path}")

    return boundary_found


def main() -> None:
    args = parse_args()
    pairs = resolve_pairs(args)
    background_rates_mbps = [float(r) for r in args.rates.split(",")]

    setLogLevel("info")
    sys.stdout.reconfigure(line_buffering=True)

    topo = GeantTopology()
    net = Mininet(
        topo=topo,
        switch=lambda name, **kw: OVSSwitch(name, failMode="secure", **kw),
        link=TCLink,
        controller=None,
    )

    try:
        net.start()
        print("*** Network up:", len(net.switches), "switches,", len(net.hosts), "hosts")
        print(f"*** Sweeping {len(pairs)} pair(s): {pairs}")

        pair_results = []
        for src_node, dst_node in pairs:
            boundary_found = run_pair(net, topo, src_node, dst_node, background_rates_mbps)
            pair_results.append((src_node, dst_node, boundary_found))
    finally:
        net.stop()

    print(f"\n{'='*70}\n*** CROSS-PAIR SUMMARY ({len(pair_results)} pairs)\n{'='*70}")
    for src_node, dst_node, boundary_found in pair_results:
        tag = "boundary found (correction matters)" if boundary_found else "no boundary in this rate range"
        print(f"  {src_node} -> {dst_node}: {tag}")
    n_boundary = sum(1 for _, _, b in pair_results if b)
    print(f"\n  {n_boundary}/{len(pair_results)} pairs found a rate where the offered-load "
          f"correction changes the switchback decision.")
    print("  Per-pair detail: results/mininet_offered_load_recovery_check/<src>_<dst>/report.md")


if __name__ == "__main__":
    main()
