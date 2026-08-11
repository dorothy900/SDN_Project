#!/usr/bin/env python3
"""
Test Calculate Metrics / Parse Results

evaluation/ is what "analysis" actually means in this project (there is no
src/analysis/ -- that directory was empty scaffolding, never populated, and
has been removed). It backs every Stage 3/6 aggregate report but had no
dedicated unit tests; only exercised indirectly through full pipeline runs.
"""
from __future__ import annotations

import csv
from pathlib import Path

import pytest

from evaluation.calculate_metrics import MetricsCalculator
from evaluation.parse_results import ResultParser


def test_calculate_network_performance():
    calc = MetricsCalculator()
    data = [
        {"delay_ms": 10.0, "throughput_mbps": 50.0, "packet_loss": 0.01},
        {"delay_ms": 20.0, "throughput_mbps": 40.0, "packet_loss": 0.02},
    ]
    result = calc.calculate_network_performance(data)
    assert result["avg_delay_ms"] == 15.0
    assert result["avg_throughput_mbps"] == 45.0
    assert result["avg_packet_loss"] == 0.015


def test_calculate_routing_stability():
    calc = MetricsCalculator()
    data = [
        {"reroute": True, "flow_updates": 4},
        {"reroute": False, "flow_updates": 0},
        {"reroute": True, "flow_updates": 6},
    ]
    result = calc.calculate_routing_stability(data)
    assert result["reroute_count"] == 2
    assert result["flow_update_count"] == 10


def test_calculate_controller_efficiency():
    calc = MetricsCalculator()
    data = [{"decision_time_ms": 1.0}, {"decision_time_ms": 3.0}]
    result = calc.calculate_controller_efficiency(data)
    assert result["decision_time_avg_ms"] == 2.0


def test_calculate_summary_combines_all_families():
    calc = MetricsCalculator()
    data = [
        {"delay_ms": 10.0, "throughput_mbps": 50.0, "packet_loss": 0.01,
         "reroute": True, "flow_updates": 2, "decision_time_ms": 1.5},
    ]
    summary = calc.calculate_summary(data, scenario="increasing_load", algorithm="proposed")
    assert summary["scenario"] == "increasing_load"
    assert summary["algorithm"] == "proposed"
    assert summary["sample_count"] == 1
    assert summary["avg_delay_ms"] == 10.0
    assert summary["reroute_count"] == 1
    assert summary["decision_time_avg_ms"] == 1.5


def test_calculate_summary_raises_a_clear_error_on_empty_data():
    # Previously: statistics.mean([]) raised a bare StatisticsError with no
    # indication of which scenario/algorithm produced zero rows.
    calc = MetricsCalculator()
    with pytest.raises(ValueError, match="scenario_x.*algorithm_y"):
        calc.calculate_summary([], scenario="scenario_x", algorithm="algorithm_y")


def test_aggregate_repeated_runs_mean_and_ci95():
    calc = MetricsCalculator()
    summary_rows = [
        {
            "scenario": "s1", "algorithm": "proposed",
            "avg_delay_ms": 10.0, "avg_throughput_mbps": 50.0, "avg_packet_loss": 0.01,
            "reroute_count": 2, "flow_update_count": 16, "decision_time_avg_ms": 1.0,
        },
        {
            "scenario": "s1", "algorithm": "proposed",
            "avg_delay_ms": 12.0, "avg_throughput_mbps": 52.0, "avg_packet_loss": 0.02,
            "reroute_count": 2, "flow_update_count": 16, "decision_time_avg_ms": 1.2,
        },
    ]
    rows = calc.aggregate_repeated_runs(summary_rows)
    assert len(rows) == 1
    row = rows[0]
    assert row["scenario"] == "s1"
    assert row["algorithm"] == "proposed"
    assert row["trials"] == 2
    assert row["avg_delay_ms_mean"] == 11.0
    # For exactly 2 samples, sample stdev = |x1-x2|/sqrt(2), so ci95 reduces to
    # 1.96 * |x1-x2|/2 = 1.96 * |10.0-12.0|/2 = 1.96 exactly -- not a value
    # copied from a prior run's output.
    assert row["avg_delay_ms_ci95"] == 1.96
    # A metric with zero spread across repeats has a zero-width interval.
    assert row["reroute_count_ci95"] == 0.0


def test_ci95_single_value_is_zero():
    assert MetricsCalculator._ci95([5.0]) == 0.0


def test_parse_csv_coerces_types(tmp_path):
    csv_path = tmp_path / "sample.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["run", "sample", "delay_ms", "reroute", "service_type"])
        writer.writerow(["1", "3", "12.5", "True", "VoIP"])
        writer.writerow(["1", "4", "13.0", "False", "Web"])

    rows = ResultParser().parse_csv(str(csv_path))
    assert len(rows) == 2
    assert rows[0]["run"] == 1 and isinstance(rows[0]["run"], int)
    assert rows[0]["sample"] == 3 and isinstance(rows[0]["sample"], int)
    assert rows[0]["delay_ms"] == 12.5 and isinstance(rows[0]["delay_ms"], float)
    assert rows[0]["reroute"] is True
    assert rows[1]["reroute"] is False
    assert rows[0]["service_type"] == "VoIP"


def test_parse_directory_combines_all_csvs(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    for sub, name in [("a", "one.csv"), ("b", "two.csv")]:
        path = tmp_path / sub / name
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["run", "delay_ms"])
            writer.writerow(["1", "5.0"])

    rows = ResultParser().parse_directory(str(tmp_path))
    assert len(rows) == 2


def test_coerce_row_leaves_blank_numeric_field_as_empty_string():
    row = ResultParser._coerce_row({"delay_ms": "", "reroute": "True"})
    assert row["delay_ms"] == ""
    assert row["reroute"] is True
