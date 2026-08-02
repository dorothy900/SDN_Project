#!/usr/bin/env python3
"""
Week 3 integration checks aligned with the baseline-routing task board.
"""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from experiments.run_baseline_comparison import BaselineComparison


def test_stage3_integration() -> None:
    """Run the full Week 3 pipeline and verify all deliverables."""
    output_dir = Path("results/stage3")
    runner = BaselineComparison(output_dir=output_dir)
    runner.run(repeat=3)

    candidate_path_file = output_dir / "candidate_paths.json"
    static_log_file = output_dir / "static_baseline_test.log"
    flow_dump_file = output_dir / "flow_dump_before_after.txt"
    dynamic_file = output_dir / "dynamic_baseline_events.csv"
    summary_file = output_dir / "baseline_summary.csv"
    repeated_file = output_dir / "baseline_summary_repeated.csv"
    wrapup_file = output_dir / "week3_wrapup_notes.md"

    for path in [
        candidate_path_file,
        static_log_file,
        flow_dump_file,
        dynamic_file,
        summary_file,
        repeated_file,
        wrapup_file,
    ]:
        assert path.exists(), f"Missing expected Stage 3 output: {path}"

    candidate_data = json.loads(candidate_path_file.read_text(encoding="utf-8"))
    assert candidate_data, "candidate_paths.json should not be empty"
    assert any(entry["path_count"] >= 2 for entry in candidate_data.values())

    static_lines = [
        line.strip()
        for line in static_log_file.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert static_lines, "static_baseline_test.log should contain repeated selections"
    assert all("consistent=True" in line for line in static_lines), "static baseline must stay deterministic"

    flow_dump = flow_dump_file.read_text(encoding="utf-8")
    assert "=== BEFORE ===" in flow_dump and "=== AFTER ===" in flow_dump
    assert "ovs-ofctl add-flow" in flow_dump

    with dynamic_file.open("r", encoding="utf-8") as handle:
        dynamic_rows = list(csv.DictReader(handle))
    assert dynamic_rows, "dynamic_baseline_events.csv should contain reroute decisions"
    assert any(row["decision"] == "reroute" for row in dynamic_rows)
    assert all(float(row["current_path_utilization"]) > 0.7 for row in dynamic_rows)

    with summary_file.open("r", encoding="utf-8") as handle:
        summary_rows = list(csv.DictReader(handle))
    assert {row["baseline"] for row in summary_rows} == {
        "static_shortest_path",
        "dynamic_link_cost",
    }

    with repeated_file.open("r", encoding="utf-8") as handle:
        repeated_rows = list(csv.DictReader(handle))
    assert len(repeated_rows) == 2
    assert all(float(row["avg_delay_ms_ci95"]) >= 0.0 for row in repeated_rows)


if __name__ == "__main__":
    test_stage3_integration()
