#!/usr/bin/env python3
"""
Test Qdisc Stats
"""
import pytest
from src.monitor.qdisc_stats import QdiscLossTracker, parse_tc_qdisc_stats

# Real `tc -s qdisc show dev s5-eth3` output, captured live 2026-08-12
# (results/loss_saturation_check/) on a real 20Mbit-capped GEANT link during
# a 60Mbit burst -- this is the exact case where OVS's own drop= counter
# read 0 while tc's own counter shows the real 29104 dropped packets.
REAL_TC_OUTPUT_BEFORE = """qdisc htb 5: root refcnt 7 r2q 10 default 0x1 direct_packets_stat 0 direct_qlen 1000
 Sent 0 bytes 0 pkt (dropped 0, overlimits 0 requeues 0)
 backlog 0b 0p requeues 0
qdisc netem 10: parent 5:1 limit 1000 delay 10ms seed 5043634804817384990
 Sent 0 bytes 0 pkt (dropped 0, overlimits 0 requeues 0)
 backlog 0b 0p requeues 0
"""

REAL_TC_OUTPUT_AFTER = """qdisc htb 5: root refcnt 7 r2q 10 default 0x1 direct_packets_stat 0 direct_qlen 1000
 Sent 20791732 bytes 13752 pkt (dropped 29104, overlimits 13316 requeues 0)
 backlog 12096b 8p requeues 0
qdisc netem 10: parent 5:1 limit 1000 delay 10ms seed 5043634804817384990
 Sent 20791732 bytes 13752 pkt (dropped 29104, overlimits 0 requeues 0)
 backlog 12096b 8p requeues 0
"""


def test_parses_real_tc_output():
    snapshot = parse_tc_qdisc_stats(REAL_TC_OUTPUT_AFTER)
    assert snapshot.sent_bytes == 20791732
    assert snapshot.sent_packets == 13752
    assert snapshot.dropped_packets == 29104


def test_unparseable_output_returns_none():
    assert parse_tc_qdisc_stats("tc: command not found") is None


def test_loss_tracker_first_sample_returns_none_no_delta_yet():
    tracker = QdiscLossTracker()
    assert tracker.calculate_loss_rate("s5-eth3", REAL_TC_OUTPUT_BEFORE) is None


def test_loss_tracker_computes_real_loss_rate_from_the_captured_case():
    """
    The exact real-world case this module exists for: OVS reported drop=0
    for this same interface at this same moment (results/loss_saturation_check/),
    while tc's own counters show real, substantial loss.
    """
    tracker = QdiscLossTracker()
    tracker.calculate_loss_rate("s5-eth3", REAL_TC_OUTPUT_BEFORE)
    loss_rate = tracker.calculate_loss_rate("s5-eth3", REAL_TC_OUTPUT_AFTER)
    # attempted = delta_sent(13752) + delta_dropped(29104) = 42856
    expected = 29104 / (13752 + 29104)
    assert loss_rate == pytest.approx(expected)
    assert loss_rate > 0.6  # most of this burst's traffic was actually dropped


def test_loss_tracker_tracks_interfaces_independently():
    tracker = QdiscLossTracker()
    tracker.calculate_loss_rate("s5-eth3", REAL_TC_OUTPUT_BEFORE)
    tracker.calculate_loss_rate("s5-eth3", REAL_TC_OUTPUT_AFTER)
    # A different, never-before-seen interface has no prior snapshot yet.
    assert tracker.calculate_loss_rate("s1-eth1", REAL_TC_OUTPUT_AFTER) is None


def test_loss_tracker_zero_delta_traffic_does_not_divide_by_zero():
    tracker = QdiscLossTracker()
    tracker.calculate_loss_rate("s5-eth3", REAL_TC_OUTPUT_BEFORE)
    assert tracker.calculate_loss_rate("s5-eth3", REAL_TC_OUTPUT_BEFORE) == 0.0
