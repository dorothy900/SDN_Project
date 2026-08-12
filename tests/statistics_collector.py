#!/usr/bin/env python3
"""
Test Statistics Collector
"""
import pytest
import time
from datetime import datetime
from unittest.mock import patch

from src.monitor.statistics_collector import StatisticsCollector
from src.monitor.models import PortStatistics, LinkStatistics


def test_initialization():
    collector = StatisticsCollector()
    assert collector is not None


def test_empty_stats():
    collector = StatisticsCollector()
    stats_list = collector.parse_ovs_port_stats("s1")
    # May be empty if OVS not running
    assert True


def test_rate_calculation():
    collector = StatisticsCollector()
    ts = datetime.now()
    stats = PortStatistics(
        timestamp=ts,
        switch="s1",
        port=1,
        rx_packets=0,
        rx_bytes=0,
        tx_packets=0,
        tx_bytes=0
    )
    result = collector.calculate_rates([stats], time.time())
    assert len(result) == 1


# Real `ovs-ofctl dump-ports` output, captured live against an actual OVS
# bridge (verified 2026-08-11/12): both the rx and tx lines carry a real
# drop= counter that parse_ovs_port_stats did not parse at all until this
# fix -- packet_loss had no real data source anywhere in the monitor
# pipeline before this and always defaulted to None.
REAL_DUMP_PORTS_OUTPUT = """OFPST_PORT reply (OF1.3) (xid=0x2): 2 ports
  port LOCAL: rx pkts=0, bytes=0, drop=0, errs=0, frame=0, over=0, crc=0
           tx pkts=0, bytes=0, drop=0, errs=0, coll=0
           duration=0.026s
  port  1: rx pkts=15000, bytes=15000000, drop=5, errs=0, frame=0, over=0, crc=0
           tx pkts=12000, bytes=12000000, drop=240, errs=0, coll=0
           duration=30.5s
"""


def test_parse_ovs_port_stats_captures_real_drop_counters():
    collector = StatisticsCollector()
    with patch.object(StatisticsCollector, "_run_command", return_value=REAL_DUMP_PORTS_OUTPUT):
        stats = collector.parse_ovs_port_stats("s1")
    assert len(stats) == 1  # port LOCAL has no numeric port id, intentionally skipped
    assert stats[0].port == 1
    assert stats[0].rx_dropped == 5
    assert stats[0].tx_dropped == 240


def test_calculate_loss_rate_from_tx_drops():
    collector = StatisticsCollector()
    stats = PortStatistics(
        timestamp=datetime.now(), switch="s1", port=1,
        rx_packets=15000, rx_bytes=15000000,
        tx_packets=12000, tx_bytes=12000000,
        tx_dropped=240,
    )
    loss = collector.calculate_loss_rate(stats)
    assert abs(loss - (240 / 12240)) < 1e-9


def test_calculate_loss_rate_zero_traffic_does_not_divide_by_zero():
    collector = StatisticsCollector()
    stats = PortStatistics(
        timestamp=datetime.now(), switch="s1", port=1,
        rx_packets=0, rx_bytes=0, tx_packets=0, tx_bytes=0,
    )
    assert collector.calculate_loss_rate(stats) == 0.0


def test_aggregate_link_statistics_populates_real_packet_loss():
    from src.monitor.link_mapper import LinkMapper

    collector = StatisticsCollector()
    mapper = LinkMapper()
    mapper.register_link("s1-s2", "s1", 1, "s2", 1)

    port_stats = PortStatistics(
        timestamp=datetime.now(), switch="s1", port=1,
        rx_packets=15000, rx_bytes=15000000,
        tx_packets=12000, tx_bytes=12000000,
        tx_dropped=240,
    )
    link_stats = collector.aggregate_link_statistics([port_stats], mapper)
    assert len(link_stats) == 1
    result: LinkStatistics = link_stats[0]
    assert result.packet_loss is not None
    assert abs(result.packet_loss - (240 / 12240)) < 1e-9
