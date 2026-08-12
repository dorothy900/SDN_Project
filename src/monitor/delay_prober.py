#!/usr/bin/env python3
"""
Delay Prober - Parse real ping RTT output into a one-way link delay estimate.

Port byte/packet counters (what StatisticsCollector reads) cannot yield a
latency number -- there's no "delay" field anywhere in `ovs-ofctl
dump-ports`. Real delay measurement needs active probing instead: send a
real packet, time how long the round trip takes. This module is the pure,
Mininet-independent half of that (parsing `ping`'s own output) so it can be
unit-tested without a live network; the orchestration half (installing
rules so a ping crosses exactly one link, actually running it) lives in
scripts/mininet_delay_measurement.py, which needs the real Mininet API.
"""
from __future__ import annotations

import re
from typing import Optional

# Standard `ping -c N` summary line, e.g.:
#   rtt min/avg/max/mdev = 0.055/0.169/0.398/0.161 ms
_RTT_LINE = re.compile(r"rtt [\w/]+ = [\d.]+/([\d.]+)/[\d.]+/[\d.]+ ms")


def parse_ping_avg_rtt_ms(ping_output: str) -> Optional[float]:
    """Extract the average RTT (milliseconds) from real `ping` command output."""
    match = _RTT_LINE.search(ping_output)
    if not match:
        return None
    return float(match.group(1))


def estimate_one_way_link_delay_ms(
    round_trip_ms: float,
    host_link_delay_ms: float = 1.0,
) -> float:
    """
    Convert a host-to-host round-trip time into a one-way *link* delay
    estimate for the single inter-switch hop between them.

    Mininet's TCLink applies the same tc-netem delay to both directions of
    a link (confirmed: TCLink defaults both cls1/cls2 to TCIntf, and
    topology.py passes delay= as a shared link option, not per-interface),
    so a round trip across one link is 2x its one-way delay. The ping is
    host-to-host, not switch-to-switch, so the path also crosses the two
    host<->switch links twice each (there and back) -- topology.py
    configures those at a fixed host_link_delay_ms (default "1ms").
    Subtract that known, fixed overhead before halving.
    """
    host_overhead_ms = 2 * 2 * host_link_delay_ms  # 2 host links, round trip
    link_round_trip_ms = max(round_trip_ms - host_overhead_ms, 0.0)
    return link_round_trip_ms / 2.0
