#!/usr/bin/env python3
"""
Qdisc Stats - parse real `tc -s qdisc show` drop counters.

Found 2026-08-12 (results/loss_saturation_check/): OVS's own port counters
(the `drop=` field StatisticsCollector.calculate_loss_rate() reads from
`ovs-ofctl dump-ports`) do not see packets dropped by tc-netem/htb rate
shaping -- confirmed live: a 60Mbit burst on a real 20Mbit-capped link showed
`tc -s qdisc show` reporting 29104 real dropped packets while the OVS port
counter for the same interface, same moment, read `drop=0`. These are two
separate accounting layers (OVS's datapath vs. the kernel's queueing
discipline sitting below it), not two views of the same counter -- the
`drop=` field is structurally blind to shaping-induced loss, which is
exactly the mechanism this project's own topology (tc-based bandwidth caps,
see topology.py/src/monitor/link_capacity.py) produces under congestion.

This module reads the mechanism that actually sees this: `tc`'s own
per-qdisc "Sent X bytes Y pkt (dropped Z, ...)" line.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, Optional

_QDISC_LINE = re.compile(r"Sent (\d+) bytes (\d+) pkt \(dropped (\d+),")


@dataclass
class QdiscSnapshot:
    sent_bytes: int
    sent_packets: int
    dropped_packets: int


def parse_tc_qdisc_stats(tc_output: str) -> Optional[QdiscSnapshot]:
    """
    Parse the first "Sent ... dropped ..." line from `tc -s qdisc show dev
    <intf>` output. A shaped interface typically has more than one qdisc
    (e.g. htb for the rate cap, netem for delay, as a parent/child pair) --
    the first one is the outermost/root qdisc, where this project's rate
    shaping (and therefore its drops) actually happens; child qdiscs
    downstream of it were observed to re-report the same already-shaped
    counts, not add new ones.
    """
    match = _QDISC_LINE.search(tc_output)
    if not match:
        return None
    sent_bytes, sent_packets, dropped_packets = (int(group) for group in match.groups())
    return QdiscSnapshot(sent_bytes=sent_bytes, sent_packets=sent_packets, dropped_packets=dropped_packets)


class QdiscLossTracker:
    """
    Delta-based real loss rate from tc's own qdisc drop counters, mirroring
    StatisticsCollector's existing pattern for OVS byte-counter rates
    (cumulative counters -> keep the previous snapshot, divide the delta).
    """

    def __init__(self):
        self._previous: Dict[str, QdiscSnapshot] = {}

    def calculate_loss_rate(self, interface_name: str, tc_output: str) -> Optional[float]:
        """
        Real loss rate = dropped-packets-since-last-sample / attempted-
        packets-since-last-sample (attempted = successfully sent + dropped,
        the same "attempted" definition StatisticsCollector.calculate_loss_rate
        uses for OVS counters: tx_dropped / (tx_packets + tx_dropped)).

        Returns None on the first sample for a given interface (no prior
        snapshot to take a delta against yet) or if tc_output doesn't parse.
        """
        current = parse_tc_qdisc_stats(tc_output)
        if current is None:
            return None
        previous = self._previous.get(interface_name)
        self._previous[interface_name] = current
        if previous is None:
            return None

        delta_sent = max(current.sent_packets - previous.sent_packets, 0)
        delta_dropped = max(current.dropped_packets - previous.dropped_packets, 0)
        attempted = delta_sent + delta_dropped
        if attempted <= 0:
            return 0.0
        return delta_dropped / attempted
