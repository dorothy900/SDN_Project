#!/usr/bin/env python3
"""
Week 5 integration checks aligned with the stability-control task board.
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from experiments.stability_validation import StabilityValidation


def test_stage5_integration() -> None:
    """Run the full Week 5 pipeline and verify all deliverables."""
    output_dir = Path("results/stage5")
    runner = StabilityValidation(output_dir=output_dir)
    runner.run()

    expected_files = [
        output_dir / "hysteresis_trace.csv",
        output_dir / "hold_down_test.csv",
        output_dir / "failure_recovery_test.csv",
        output_dir / "recovery_window_test.csv",
        output_dir / "priority_policy_test.csv",
        output_dir / "stability_integration_trace.csv",
        output_dir / "stability_integration_report.md",
        output_dir / "week5_wrapup_notes.md",
    ]
    for path in expected_files:
        assert path.exists(), "Missing expected Stage 5 output: %s" % path

    with (output_dir / "hysteresis_trace.csv").open("r", encoding="utf-8") as handle:
        hysteresis_rows = list(csv.DictReader(handle))
    transitions = [row["transition"] for row in hysteresis_rows if row["transition"] != "steady"]
    assert transitions == ["enter_congestion", "release_congestion", "enter_congestion"]

    with (output_dir / "hold_down_test.csv").open("r", encoding="utf-8") as handle:
        hold_rows = list(csv.DictReader(handle))
    allowed_attempts = [row for row in hold_rows if row["allowed"] == "True"]
    assert [row["attempt"] for row in allowed_attempts] == ["1", "4"]

    with (output_dir / "failure_recovery_test.csv").open("r", encoding="utf-8") as handle:
        failure_rows = list(csv.DictReader(handle))
    assert failure_rows[0]["emergency_reroute"] == "True"
    assert failure_rows[0]["reroute_executed"] == "True"

    with (output_dir / "recovery_window_test.csv").open("r", encoding="utf-8") as handle:
        recovery_rows = list(csv.DictReader(handle))
    assert any(row["status"] == "ignored_unstable_restore" for row in recovery_rows)
    assert any(row["status"] == "eligible_for_switchback" and row["eligible"] == "True" for row in recovery_rows)

    with (output_dir / "priority_policy_test.csv").open("r", encoding="utf-8") as handle:
        policy_rows = list(csv.DictReader(handle))
    first_trigger = {}
    for row in policy_rows:
        if row["first_reroute_sample"] and row["service_type"] not in first_trigger:
            first_trigger[row["service_type"]] = int(row["first_reroute_sample"])
    assert first_trigger["VoIP"] <= first_trigger["Web"] < first_trigger["File Transfer"]
    assert first_trigger["Video"] <= first_trigger["Web"]

    with (output_dir / "stability_integration_trace.csv").open("r", encoding="utf-8") as handle:
        integration_rows = list(csv.DictReader(handle))
    stable_reroutes = sum(1 for row in integration_rows if row["reroute"] == "True")
    assert stable_reroutes == 1

    report_text = (output_dir / "stability_integration_report.md").read_text(encoding="utf-8")
    assert "Combined stability control rerouted 1 time(s)." in report_text


if __name__ == "__main__":
    test_stage5_integration()
