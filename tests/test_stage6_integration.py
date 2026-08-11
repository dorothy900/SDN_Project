#!/usr/bin/env python3
"""
Week 6 integration checks aligned with the experiment-automation task board.
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from experiments.pilot_experiments import PilotExperimentRunner


def test_stage6_integration() -> None:
    """Run the Week 6 pilot pipeline and verify all deliverables."""
    output_dir = Path("results/pilot")
    runner = PilotExperimentRunner(output_dir=output_dir)
    runner.run(repeat=3, scenario="all")

    expected_paths = [
        output_dir / "traffic_generator.log",
        output_dir / "scenario1-2" / "increasing_load_run_3.csv",
        output_dir / "scenario1-2" / "congestion_run_3.csv",
        output_dir / "scenario3" / "failure_recovery_run_3.csv",
        output_dir / "scenario4" / "stale_stats_run_3.csv",
        output_dir / "pilot_summary.csv",
        output_dir / "full_results_repeated.csv",
        output_dir / "end_to_end_validation_report.md",
    ]
    for path in expected_paths:
        assert path.exists(), "Missing expected Stage 6 output: %s" % path

    traffic_log = (output_dir / "traffic_generator.log").read_text(encoding="utf-8")
    assert "flow-voip-1" in traffic_log
    assert "flow-video-1" in traffic_log

    with (output_dir / "pilot_summary.csv").open("r", encoding="utf-8") as handle:
        pilot_rows = list(csv.DictReader(handle))
    assert len(pilot_rows) == 12
    algorithms = {row["algorithm"] for row in pilot_rows}
    assert algorithms == {"static", "dynamic", "proposed"}

    with (output_dir / "full_results_repeated.csv").open("r", encoding="utf-8") as handle:
        repeated_rows = list(csv.DictReader(handle))
    assert len(repeated_rows) == 12
    assert all(float(row["avg_delay_ms_ci95"]) >= 0.0 for row in repeated_rows)

    stale_dynamic = [
        row for row in pilot_rows
        if row["scenario"] == "stale_statistics" and row["algorithm"] == "dynamic"
    ][0]
    stale_proposed = [
        row for row in pilot_rows
        if row["scenario"] == "stale_statistics" and row["algorithm"] == "proposed"
    ][0]
    assert int(stale_dynamic["reroute_count"]) > int(stale_proposed["reroute_count"])

    failure_static = [
        row for row in pilot_rows
        if row["scenario"] == "failure_recovery" and row["algorithm"] == "static"
    ][0]
    failure_dynamic = [
        row for row in pilot_rows
        if row["scenario"] == "failure_recovery" and row["algorithm"] == "dynamic"
    ][0]
    assert float(failure_dynamic["avg_throughput_mbps"]) > float(failure_static["avg_throughput_mbps"])

    report_text = (output_dir / "end_to_end_validation_report.md").read_text(encoding="utf-8")
    assert "Week 6 scenarios executed from one command" in report_text
    assert "Algorithms remain comparable" in report_text


if __name__ == "__main__":
    test_stage6_integration()
