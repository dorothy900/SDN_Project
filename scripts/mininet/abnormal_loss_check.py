#!/usr/bin/env python3
"""
Mininet Abnormal-Loss Check - real-hardware validation of the resilience
layer's *loss* signal (LossJitterTracker.get_abnormal_loss_score) and its live
trigger (DecisionEngine.evaluate_resilience_avoidance). Every offline artefact
drives this signal with injected residuals; this drives it with a real link
that genuinely drops packets.

A real GEANT link on the monitored flow's path is given `tc netem loss`, so a
live iperf UDP flow across it genuinely loses packets, while the link stays
administratively up and its utilisation stays normal. An unmodified
ProposedDriver is polled once per DECISION_INTERVAL_S with real telemetry
(rate/utilisation via ovs-ofctl counters, per-flow loss measured by iperf and
stamped in -- netem drops don't reliably reach the OVS tx-drop counter). The
check: proposed detects the degradation and reroutes the flow off the link;
the flow's measured loss drops; a control run with resilience disabled never
reroutes and eats the loss for the whole episode.

Two phases:
  1. abnormal_loss - clean for CLEAN_SECONDS, then LOSS_PCT injected (3-sigma
     shift term).
  2. chronic_loss  - LOSS_PCT from the start of the phase, no clean baseline
     (absolute-level term -- the P1 case).

Run as: sudo python3 scripts/mininet/abnormal_loss_check.py
Writes results/mininet_resilience/abnormal_loss_{timeline.csv,report.md}.
"""
from __future__ import annotations

import csv
import sys
import time
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from mininet.log import setLogLevel

from src.monitor.network_state import NetworkState
from src.routing.resilience_gate import ResilienceGate
from experiments.simulation_common import ProposedDriver
from scripts.mininet._common import (
    LinkTelemetry, build_net, clear_path_rules, install_path_rules, iperf_server_loss_series,
    link_id_of, path_intf, reinstall_if_changed, start_udp_flow, stop_udp_flow,
)

SRC_NODE, DST_NODE = "2", "7"
AVOID_THRESHOLD = 0.57
REUSE_THRESHOLD = AVOID_THRESHOLD * ResilienceGate.REUSE_SUPPRESS_RATIO
LOSS_PCT = 8.0
IPERF_RATE_MBPS = 6
DECISION_INTERVAL_S = 4.0
CLEAN_SECONDS = 20
DEGRADED_SECONDS = 50


def _run_phase(net, topo, phase, degrade_from_start, resilience, out_dir, rows):
    state = NetworkState(output_dir=out_dir)
    driver = ProposedDriver(
        state, SRC_NODE, DST_NODE,
        config_path=str(PROJECT_ROOT / "config" / "decision.yaml"),
    )
    if not resilience:
        driver.engine.path_cost.graph_builder.resilience_avoid_threshold = None

    initial = list(driver.path)
    bad_link = link_id_of(initial[0], initial[1])
    intf = path_intf(net, topo, initial[0], initial[1])
    src_host = net.get(topo.node_mapping[SRC_NODE][1])
    dst_host = net.get(topo.node_mapping[DST_NODE][1])
    src_ip, dst_ip = src_host.IP(), dst_host.IP()
    src_host.cmd("arp -s %s %s" % (dst_ip, dst_host.MAC()))
    dst_host.cmd("arp -s %s %s" % (src_ip, src_host.MAC()))

    tele = LinkTelemetry(net, topo, [(initial[0], initial[1]),
                                     (initial[1], initial[2] if len(initial) > 2 else initial[0])]).bind(state)

    print("\n=== phase %s  resilience=%s ===" % (phase, "ON" if resilience else "OFF"))
    print("   initial path %s, degrading link %s (%s)" % ("-".join(initial), bad_link, intf))
    install_path_rules(net, topo, initial, src_ip, dst_ip)
    total = CLEAN_SECONDS + DEGRADED_SECONDS + 10
    start_udp_flow(src_host, dst_host, IPERF_RATE_MBPS, total, out_dir)

    installed = list(initial)
    t0 = time.time()
    degrading = degrade_from_start
    if degrading:
        intf.config(loss=LOSS_PCT)

    while time.time() - t0 < total - 6:
        elapsed = time.time() - t0
        if not degrading and elapsed >= CLEAN_SECONDS:
            intf.config(loss=LOSS_PCT)
            degrading = True
            print("   [%5.1fs] injected %.0f%% netem loss on %s" % (elapsed, LOSS_PCT, bad_link))

        # measured per-flow loss over the last iperf second, stamped onto the link
        series = iperf_server_loss_series(out_dir)
        measured_loss = series[-1] if series else 0.0
        tele.poll(gap_s=2.0, loss_override={bad_link: measured_loss})

        now = time.time()
        driver.step(now_s=now)
        changed = reinstall_if_changed(net, topo, installed, driver.path, src_ip, dst_ip)
        if changed:
            print("   [%5.1fs] REROUTE %s -> %s" % (elapsed, "-".join(installed), "-".join(driver.path)))
            installed = list(driver.path)

        gate = driver.engine.path_cost.graph_builder._resilience_gate
        rows.append({
            "phase": phase, "resilience": "on" if resilience else "off",
            "t": round(elapsed, 1),
            "resilience_score": round(state.get_resilience_score(bad_link, now=now), 4),
            "gate_latched": bool(gate and gate.is_avoided(bad_link)),
            "measured_flow_loss": round(measured_loss, 4),
            "path": "-".join(driver.path),
            "path_uses_bad_link": bad_link in {link_id_of(a, b) for a, b in zip(driver.path, driver.path[1:])},
        })
        time.sleep(max(0.0, DECISION_INTERVAL_S - 2.0))

    stop_udp_flow(src_host, dst_host)
    intf.config(loss=0)
    clear_path_rules(net, topo, installed, src_ip, dst_ip)

    phase_rows = [r for r in rows if r["phase"] == phase and r["resilience"] == ("on" if resilience else "off")]
    degraded_rows = [r for r in phase_rows if r["t"] >= (0 if degrade_from_start else CLEAN_SECONDS) + 6]
    off_link = [r for r in degraded_rows if not r["path_uses_bad_link"]]
    return {
        "rerouted_off": bool(off_link),
        "final_off_link": phase_rows and not phase_rows[-1]["path_uses_bad_link"],
        "mean_loss_while_degraded": (
            sum(r["measured_flow_loss"] for r in degraded_rows) / len(degraded_rows) if degraded_rows else 0.0
        ),
        "score_crossed": any(r["resilience_score"] >= AVOID_THRESHOLD for r in phase_rows),
    }


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
        for phase, from_start in (("abnormal_loss", False), ("chronic_loss", True)):
            on = _run_phase(net, topo, phase, from_start, True, out_dir, rows)
            off = _run_phase(net, topo, phase, from_start, False, out_dir, rows)
            checks.append(("%s: resilience score crossed avoid_threshold" % phase, on["score_crossed"]))
            checks.append(("%s: proposed rerouted off the lossy link" % phase, on["rerouted_off"]))
            checks.append(("%s: proposed ended clear of the lossy link" % phase, on["final_off_link"]))
            checks.append(("%s: resilience ON flow loss < OFF flow loss" % phase,
                           on["mean_loss_while_degraded"] < off["mean_loss_while_degraded"] - 0.005))
            checks.append(("%s: resilience OFF never rerouted (control)" % phase, not off["rerouted_off"]))
            print("   [%s] ON mean flow loss while degraded = %.3f, OFF = %.3f"
                  % (phase, on["mean_loss_while_degraded"], off["mean_loss_while_degraded"]))

        with (out_dir / "abnormal_loss_timeline.csv").open("w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
        passed = all(ok for _, ok in checks)
        report = ["# Mininet Abnormal-Loss Check", "",
                  "Generated: %s" % datetime.now().isoformat(), "",
                  "- Pair %s->%s, real `tc netem loss %.0f%%` on the path's first hop" % (SRC_NODE, DST_NODE, LOSS_PCT),
                  "- Live iperf UDP %d Mbps; per-flow loss measured server-side and stamped onto the link" % IPERF_RATE_MBPS,
                  "- avoid_threshold=%.2f, reuse_threshold=%.3f" % (AVOID_THRESHOLD, REUSE_THRESHOLD),
                  "", "## Checks", ""]
        for name, ok in checks:
            report.append("- [%s] %s" % ("PASS" if ok else "FAIL", name))
        report += ["", "## Overall", "", "**%s**" % ("PASS" if passed else "FAIL"),
                   "", "See abnormal_loss_timeline.csv and iperf_server.log for the full trace."]
        (out_dir / "abnormal_loss_report.md").write_text("\n".join(report) + "\n", encoding="utf-8")
        print("\n*** OVERALL:", "PASS" if passed else "FAIL")
        for name, ok in checks:
            print("   [%s] %s" % ("PASS" if ok else "FAIL", name))
    finally:
        net.stop()


if __name__ == "__main__":
    main()
