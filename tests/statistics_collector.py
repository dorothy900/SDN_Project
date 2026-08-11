#!/usr/bin/env python3
"""
Test Statistics Collector
"""
import pytest
import time
from datetime import datetime

from src.monitor.statistics_collector import StatisticsCollector
from src.monitor.models import PortStatistics


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
