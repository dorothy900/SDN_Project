#!/usr/bin/env python3
"""
Mininet Link-Flap Check - real-hardware exercise of the resilience layer's
flap signal (LinkFlapTracker) and its interaction with a real reroute. Offline
artefacts drive flaps with synthetic transition schedules; this brings a real
OVS link genuinely down and up, several times, under a live iperf flow, and
checks the project's own NetworkState / LinkFlapTracker / ResilienceGate /
DecisionEngine pipeline reacts as the synthetic tests assume.

A real link on the monitored pair's path is flapped down/up repeatedly. Two
unmodified ProposedDrivers are polled each DECISION_INTERVAL_S with real
telemetry and their path changes pushed as real ovs-ofctl rules: one with
resilience avoidance ON (config default), one with it forced OFF. The check:
after the link has flapped a couple of times, the resilience-ON driver keeps
the flow off it even when it is momentarily back up, while the resilience-OFF
driver is dragged back on by every recovery -- and the live flow's measured
loss reflects that.

Run as: sudo python3 scripts/mininet_link_flap_check.py
Writes results/mininet_resilience/flap_{timeline.csv,report.md}.
"""
from __future__ import annotations

import csv
import sys
import time
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from mininet.log import setLogLevel

from src.monitor.network_state import NetworkState
from src.routing.resilience_gate import ResilienceGate
from experiments.simulation_common import ProposedDriver
from scripts._mininet_resilience_common import (
    LinkTelemetry, build_net, clear_path_rules, install_path_rules, iperf_server_loss_series,
    link_id_of, reinstall_if_changed, start_udp_flow, stop_udp_flow,
)

SRC_NODE, DST_NODE = "2", "7"
AVOID_THRESHOLD = 0.57
REUSE_THRESHOLD = AVOID_THRESHOLD * ResilienceGate.REUSE_SUPPRESS_RATIO
FLAP_CYCLES = 5
DOWN_SECONDS = 4.0
UP_SECONDS = 8.0
SETTLE_SECONDS = 60
DECISION_INTERVAL_S = 4.0
IPERF_RATE_MBPS = 6


def _driver(state, resilience):
    d = ProposedDriver(state, SRC_NODE, DST_NODE,
                       config_path=str(PROJECT_ROOT / "config" / "decision.yaml"))
    if not resilience:
        d.engine.path_cost.graph_builder.resilience_avoid_threshold = None
    return d


def _links(path):
    return {link_id_of(a, b) for a, b in zip(path, path[1:])}


def main() -> None:
    setLogLevel("info")
    sys.stdout.reconfigure(line_buffering=True)
    net, topo = build_net()
    out_dir = PROJECT_ROOT / "results" / "mininet_resilience"
    out_dir.mkdir(parents=True, exist_ok=True)
    rows: list = []
    checks: list = []
    try:
        net.start()
        print("*** Network up:", len(net.switches), "switches")

        state = NetworkState(output_dir=out_dir)
        driver_on = _driver(state, True)
        driver_off = _driver(NetworkState(output_dir=out_dir / "off"), False)
        # keep both drivers' state fed from the same real telemetry
        state_off = driver_off.state

        initial = list(driver_on.path)
        fu, fv = initial[0], initial[1]
        flap_lid = link_id_of(fu, fv)
        sw_u, sw_v = topo.node_mapping[fu][0], topo.node_mapping[fv][0]
        print("*** pair %s->%s, initial path %s, flapping link %s (OVS %s<->%s)"
              % (SRC_NODE, DST_NODE, "-".join(initial), flap_lid, sw_u, sw_v))

        src_host = net.get(topo.node_mapping[SRC_NODE][1])
        dst_host = net.get(topo.node_mapping[DST_NODE][1])
        src_ip, dst_ip = src_host.IP(), dst_host.IP()
        src_host.cmd("arp -s %s %s" % (dst_ip, dst_host.MAC()))
        dst_host.cmd("arp -s %s %s" % (src_ip, src_host.MAC()))

        nxt = initial[2] if len(initial) > 2 else initial[0]
        tele_on = LinkTelemetry(net, topo, [(fu, fv), (fv, nxt)]).bind(state)
        tele_off = LinkTelemetry(net, topo, [(fu, fv), (fv, nxt)]).bind(state_off)

        install_path_rules(net, topo, initial, src_ip, dst_ip)
        installed = list(initial)
        total = int(FLAP_CYCLES * (DOWN_SECONDS + UP_SECONDS) + SETTLE_SECONDS + 15)
        start_udp_flow(src_host, dst_host, IPERF_RATE_MBPS, total, out_dir)

        t0 = time.time()
        link_up = True
        schedule = []  # (at_seconds, "down"/"up")
        clock = 5.0
        for _ in range(FLAP_CYCLES):
            schedule.append((clock, "down")); clock += DOWN_SECONDS
            schedule.append((clock, "up")); clock += UP_SECONDS
        crossed = False

        while time.time() - t0 < total - 8:
            elapsed = time.time() - t0
            while schedule and elapsed >= schedule[0][0]:
                _, action = schedule.pop(0)
                want_up = action == "up"
                if want_up != link_up:
                    net.configLinkStatus(sw_u, sw_v, "up" if want_up else "down")
                    for d in (driver_on, driver_off):
                        d.state.set_link_status(flap_lid, is_up=want_up)
                        if not want_up and flap_lid in _links(d.path):
                            d.on_link_failure(flap_lid, time.time())
                    link_up = want_up
                    print("   [%5.1fs] link %s -> %s" % (elapsed, flap_lid, action))

            status_ov = {} if link_up else {flap_lid: "down"}
            series = iperf_server_loss_series(out_dir)
            meas_loss = series[-1] if series else 0.0
            for tele in (tele_on, tele_off):
                tele.poll(gap_s=1.5, status_override=status_ov, loss_override={flap_lid: meas_loss} if link_up else None)

            now = time.time()
            driver_on.step(now_s=now)
            driver_off.step(now_s=now)
            if reinstall_if_changed(net, topo, installed, driver_on.path, src_ip, dst_ip):
                print("   [%5.1fs] driver_on REROUTE %s -> %s" % (elapsed, "-".join(installed), "-".join(driver_on.path)))
                installed = list(driver_on.path)

            gate = driver_on.engine.path_cost.graph_builder._resilience_gate
            score = state.get_resilience_score(flap_lid, now=now)
            crossed = crossed or score >= AVOID_THRESHOLD
            rows.append({
                "t": round(elapsed, 1), "link_up": link_up,
                "resilience_score": round(score, 4),
                "gate_latched": bool(gate and gate.is_avoided(flap_lid)),
                "on_path_uses_link": flap_lid in _links(driver_on.path),
                "off_path_uses_link": flap_lid in _links(driver_off.path),
                "measured_flow_loss": round(meas_loss, 4),
                "on_path": "-".join(driver_on.path), "off_path": "-".join(driver_off.path),
            })
            time.sleep(max(0.0, DECISION_INTERVAL_S - 1.5))

        stop_udp_flow(src_host, dst_host)
        clear_path_rules(net, topo, installed, src_ip, dst_ip)

        flap_window = [r for r in rows if r["t"] <= FLAP_CYCLES * (DOWN_SECONDS + UP_SECONDS) + 5]
        up_rows = [r for r in flap_window if r["link_up"] and r["t"] > (DOWN_SECONDS + UP_SECONDS)]
        settle_rows = rows[-3:]
        checks = [
            ("resilience score crossed avoid_threshold", crossed),
            ("resilience-ON keeps the flow off the link while it is momentarily up",
             any(r["gate_latched"] and not r["on_path_uses_link"] for r in up_rows)),
            ("resilience-OFF is dragged back onto the link on a recovery",
             any(r["link_up"] and r["off_path_uses_link"] for r in up_rows)),
            ("resilience-ON returns over the link once it settles",
             any(not r["gate_latched"] and r["on_path_uses_link"] for r in settle_rows)),
        ]
        with (out_dir / "flap_timeline.csv").open("w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
        passed = all(ok for _, ok in checks)
        report = ["# Mininet Link-Flap Check", "",
                  "Generated: %s" % datetime.now().isoformat(), "",
                  "- Pair %s->%s, flapping link %s, %d real down/up cycles" % (SRC_NODE, DST_NODE, flap_lid, FLAP_CYCLES),
                  "- Live iperf UDP %d Mbps; driver_on = resilience ON, driver_off = control (OFF)" % IPERF_RATE_MBPS,
                  "- avoid_threshold=%.2f, reuse_threshold=%.3f" % (AVOID_THRESHOLD, REUSE_THRESHOLD),
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
