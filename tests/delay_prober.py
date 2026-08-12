#!/usr/bin/env python3
"""
Test Delay Prober
"""
import pytest
from src.monitor.delay_prober import parse_ping_avg_rtt_ms, estimate_one_way_link_delay_ms

REAL_PING_OUTPUT = """PING 10.0.0.38 (10.0.0.38) 56(84) bytes of data.
64 bytes from 10.0.0.38: icmp_seq=1 ttl=64 time=0.398 ms
64 bytes from 10.0.0.38: icmp_seq=2 ttl=64 time=0.056 ms
64 bytes from 10.0.0.38: icmp_seq=3 ttl=64 time=0.055 ms

--- 10.0.0.38 ping statistics ---
3 packets transmitted, 3 received, 0% packet loss, time 2058ms
rtt min/avg/max/mdev = 0.055/0.169/0.398/0.161 ms
"""


def test_parse_ping_avg_rtt_from_real_output():
    assert parse_ping_avg_rtt_ms(REAL_PING_OUTPUT) == pytest.approx(0.169)


def test_parse_ping_avg_rtt_missing_summary_returns_none():
    assert parse_ping_avg_rtt_ms("ping: unknown host\n") is None


def test_parse_ping_avg_rtt_on_100pct_loss_output_returns_none():
    lossy_output = """PING 10.0.0.99 (10.0.0.99) 56(84) bytes of data.

--- 10.0.0.99 ping statistics ---
3 packets transmitted, 0 received, 100% packet loss, time 2039ms
"""
    assert parse_ping_avg_rtt_ms(lossy_output) is None


def test_estimate_one_way_link_delay_subtracts_host_overhead_and_halves():
    # Link configured at 10ms one-way -> round trip across the link alone is
    # 20ms, plus 2 host<->switch links (1ms each) crossed twice (there+back)
    # = 4ms overhead, so total round trip = 24ms.
    assert estimate_one_way_link_delay_ms(24.0, host_link_delay_ms=1.0) == pytest.approx(10.0)


def test_estimate_one_way_link_delay_never_negative():
    # If measured round trip is smaller than the assumed host overhead
    # (e.g. a very short/near-zero configured delay plus measurement noise),
    # the estimate should clamp at 0, not go negative.
    assert estimate_one_way_link_delay_ms(1.0, host_link_delay_ms=1.0) == 0.0
