#!/usr/bin/env python3
"""
Mininet Link-Flap Check - real-hardware exercise of the resilience layer's
flap signal (LinkFlapTracker) and its interaction with the recovery-window
switch-back. Offline artefacts drive flaps with synthetic transition
schedules; this brings a real OVS link genuinely down and up several times
under a live iperf flow.

A real link on the monitored pair's initial path is flapped. One unmodified
ProposedDriver (resilience avoidance ON, config default) is driven the same
way the RYU app drives it: each down calls on_link_failure(), the last
recovery calls on_link_recovered() to open a recovery watch, and every poll
calls step(). Path changes are pushed as real ovs-ofctl rules.

Checks:
  1. get_resilience_score crosses avoid_threshold within the first few flaps.
  2. While the gate is latched, the recovery-window switch-back is refused --
     the flow stays on the detour even when the link is momentarily back up
     (the original path is priced past any alternative, never removed).
  3. Once the link stops flapping and the score decays below the reuse band,
     the gate releases and the switch-back goes through -- proving the
     avoidance is not a permanent black-hole.

Run as: sudo python3 scripts/mininet/link_flap_check.py
Writes results/mininet_resilience/flap_{timeline.csv,report.md}.
"""
from __future__ import annotations

import csv
import re
import sys
import time
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from mininet.log import setLogLevel

from src.monitor.network_state import NetworkState
from src.routing.resilience_gate import ResilienceGate
from experiments.common.simulation_common import ProposedDriver
from scripts.mininet._common import (
    build_net, clear_path_rules, install_path_rules, link_id_of,
    reinstall_if_changed, start_udp_flow, stop_udp_flow,
)

SRC_NODE, DST_NODE = "2", "7"
AVOID_THRESHOLD = 0.57
REUSE_THRESHOLD = AVOID_THRESHOLD * ResilienceGate.REUSE_SUPPRESS_RATIO
FLAP_CYCLES = 4
DOWN_SECONDS = 3.0
UP_SECONDS = 6.0
SETTLE_SECONDS = 130          # score half-life is ~20s; needs to fall below the reuse band
POLL_SECONDS = 2.0
IPERF_RATE_MBPS = 6


def _links(path):
    return {link_id_of(a, b) for a, b in zip(path, path[1:])}


def _iperf_mean_loss(server_log: Path, t_from: float, t_to: float) -> float:
    """Mean per-second UDP loss fraction from the iperf server log between two run-relative times."""
    try:
        lines = server_log.read_text().splitlines()
    except OSError:
        return 0.0
    losses = []
    for ln in lines:
        m = re.search(r"(\d+\.\d+)-\s*(\d+\.\d+)\s+sec.*\(([\d.]+)%\)", ln)
        if m and t_from <= float(m.group(1)) < t_to:
            losses.append(float(m.group(3)) / 100.0)
    return sum(losses) / len(losses) if losses else 0.0


def main() -> None:
    setLogLevel("info")
    sys.stdout.reconfigure(line_buffering=True)
    net, topo = build_net()
    out_dir = PROJECT_ROOT / "results" / "mininet_resilience"
    out_dir.mkdir(parents=True, exist_ok=True)
    rows: list = []
    try:
        net.start()
        print("*** Network up:", len(net.switches), "switches")

        state = NetworkState(output_dir=out_dir)
        driver = ProposedDriver(
            state, SRC_NODE, DST_NODE,
            config_path=str(PROJECT_ROOT / "config" / "decision.yaml"),
        )
        initial = list(driver.path)
        fu, fv = initial[0], initial[1]
        flap_lid = link_id_of(fu, fv)
        sw_u, sw_v = topo.node_mapping[fu][0], topo.node_mapping[fv][0]
        print("*** pair %s->%s  path %s  flapping link %s (OVS %s<->%s)"
              % (SRC_NODE, DST_NODE, "-".join(initial), flap_lid, sw_u, sw_v))

        src_host = net.get(topo.node_mapping[SRC_NODE][1])
        dst_host = net.get(topo.node_mapping[DST_NODE][1])
        src_ip, dst_ip = src_host.IP(), dst_host.IP()
        src_host.cmd("arp -s %s %s" % (dst_ip, dst_host.MAC()))
        dst_host.cmd("arp -s %s %s" % (src_ip, src_host.MAC()))

        install_path_rules(net, topo, initial, src_ip, dst_ip)
        installed = list(initial)
        total = int(FLAP_CYCLES * (DOWN_SECONDS + UP_SECONDS) + SETTLE_SECONDS + 10)
        start_udp_flow(src_host, dst_host, IPERF_RATE_MBPS, total, out_dir)

        # (at_seconds, action) schedule
        sched, clk = [], 4.0
        for i in range(FLAP_CYCLES):
            sched.append((clk, "down")); clk += DOWN_SECONDS
            sched.append((clk, "up_last" if i == FLAP_CYCLES - 1 else "up")); clk += UP_SECONDS
        flap_end = clk
        crossed = latched_ever = False

        t0 = time.time()
        link_up = True
        while time.time() - t0 < total - 6:
            elapsed = time.time() - t0
            while sched and elapsed >= sched[0][0]:
                _, act = sched.pop(0)
                if act == "down" and link_up:
                    net.configLinkStatus(sw_u, sw_v, "down")
                    state.set_link_status(flap_lid, is_up=False)
                    link_up = False
                    if flap_lid in _links(driver.path):
                        driver.on_link_failure(flap_lid, time.time())
                    print("   [%5.1fs] %s DOWN" % (elapsed, flap_lid))
                elif act in ("up", "up_last") and not link_up:
                    net.configLinkStatus(sw_u, sw_v, "up")
                    state.set_link_status(flap_lid, is_up=True)
                    link_up = True
                    if act == "up_last":
                        driver.on_link_recovered(flap_lid, time.time())
                    print("   [%5.1fs] %s UP%s" % (elapsed, flap_lid,
                                                   " (recovery watch)" if act == "up_last" else ""))

            now = time.time()
            driver.step(now_s=now)
            if reinstall_if_changed(net, topo, installed, driver.path, src_ip, dst_ip):
                print("   [%5.1fs] REROUTE %s -> %s" % (elapsed, "-".join(installed), "-".join(driver.path)))
                installed = list(driver.path)

            gate = driver.engine.path_cost.graph_builder._resilience_gate
            score = state.get_resilience_score(flap_lid, now=now)
            latched = bool(gate and gate.is_avoided(flap_lid))
            crossed = crossed or score >= AVOID_THRESHOLD
            latched_ever = latched_ever or latched
            rows.append({
                "t": round(elapsed, 1), "link_up": link_up,
                "resilience_score": round(score, 4), "gate_latched": latched,
                "path_uses_flap_link": flap_lid in _links(driver.path),
                "path": "-".join(driver.path),
            })
            time.sleep(POLL_SECONDS)

        stop_udp_flow(src_host, dst_host)
        clear_path_rules(net, topo, installed, src_ip, dst_ip)

        latched_up = [r for r in rows if r["gate_latched"] and r["link_up"]]
        settled = [r for r in rows if r["t"] > flap_end + 20]
        released = [r for r in settled if not r["gate_latched"]]
        server_log = out_dir / "iperf_server.log"
        checks = [
            ("resilience score crossed avoid_threshold", crossed),
            ("switch-back refused while the gate is latched (flow stays off the link, link up)",
             bool(latched_up) and all(not r["path_uses_flap_link"] for r in latched_up)),
            ("gate released after the flapping stopped and the score decayed below the reuse band",
             bool(released)),
            ("switch-back went through once released (not a permanent black-hole)",
             bool(released) and released[-1]["path_uses_flap_link"]),
        ]
        loss_flapping = _iperf_mean_loss(server_log, 4.0, flap_end)
        loss_settled = _iperf_mean_loss(server_log, flap_end + 20, total)
        print("   iperf flow loss: during flapping %.1f%%, after settle %.1f%%"
              % (100 * loss_flapping, 100 * loss_settled))

        with (out_dir / "flap_timeline.csv").open("w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
        passed = all(ok for _, ok in checks)
        report = ["# Mininet Link-Flap Check", "",
                  "Generated: %s" % datetime.now().isoformat(), "",
                  "- Pair %s->%s, flapping link %s, %d real down/up cycles then %ds settle"
                  % (SRC_NODE, DST_NODE, flap_lid, FLAP_CYCLES, SETTLE_SECONDS),
                  "- Live iperf UDP %d Mbps; one ProposedDriver, resilience avoidance ON (config default)"
                  % IPERF_RATE_MBPS,
                  "- avoid_threshold=%.2f, reuse_threshold=%.3f" % (AVOID_THRESHOLD, REUSE_THRESHOLD),
                  "- iperf flow loss: %.1f%% during flapping, %.1f%% after settle"
                  % (100 * loss_flapping, 100 * loss_settled),
                  "", "## Checks", ""]
        for name, ok in checks:
            report.append("- [%s] %s" % ("PASS" if ok else "FAIL", name))
        report += ["", "## Overall", "", "**%s**" % ("PASS" if passed else "FAIL"),
                   "", "See flap_timeline.csv and iperf_server.log for the full trace."]
        (out_dir / "flap_report.md").write_text("\n".join(report) + "\n", encoding="utf-8")
        print("\n*** OVERALL:", "PASS" if passed else "FAIL")
        for name, ok in checks:
            print("   [%s] %s" % ("PASS" if ok else "FAIL", name))
    finally:
        net.stop()


if __name__ == "__main__":
    main()
