#!/usr/bin/env python3
"""
Week 4 integration checks aligned with the decision-engine task board.
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from experiments.decision_engine_validation import DecisionEngineValidation


def test_decision_engine_integration() -> None:
    """Run the full Week 4 pipeline and verify all deliverables."""
    output_dir = Path("results/decision_engine")
    runner = DecisionEngineValidation(output_dir=output_dir)
    runner.run()

    expected_files = [
        output_dir / "threshold_unit_tests.txt",
        output_dir / "persistence_test.csv",
        output_dir / "path_cost_unit_tests.txt",
        output_dir / "minimum_gain_test.csv",
        output_dir / "decision_log.csv",
        output_dir / "change_budget_trace.csv",
        output_dir / "report.md",
    ]
    for path in expected_files:
        assert path.exists(), "Missing expected Stage 4 output: %s" % path

    threshold_text = (output_dir / "threshold_unit_tests.txt").read_text(encoding="utf-8")
    assert "first_trigger_sample=4" in threshold_text
    assert "transition=normal->violating" in threshold_text

    with (output_dir / "persistence_test.csv").open("r", encoding="utf-8") as handle:
        persistence_rows = list(csv.DictReader(handle))
    assert persistence_rows
    short_spike_accepts = [
        row for row in persistence_rows if row["scenario"] == "short_spike" and row["accepted"] == "True"
    ]
    sustained_accepts = [
        row for row in persistence_rows if row["scenario"] == "sustained_overload" and row["accepted"] == "True"
    ]
    assert not short_spike_accepts
    assert sustained_accepts

    path_cost_text = (output_dir / "path_cost_unit_tests.txt").read_text(encoding="utf-8")
    assert "ordering=lower_risk_is_better:True" in path_cost_text

    with (output_dir / "minimum_gain_test.csv").open("r", encoding="utf-8") as handle:
        gain_rows = list(csv.DictReader(handle))
    assert {row["accepted"] for row in gain_rows} == {"True", "False"}

    with (output_dir / "decision_log.csv").open("r", encoding="utf-8") as handle:
        decision_rows = list(csv.DictReader(handle))
    decision_types = {row["decision_type"] for row in decision_rows}
    assert "no_action" in decision_types
    assert "no_improvement" in decision_types
    assert "reroute" in decision_types

    with (output_dir / "change_budget_trace.csv").open("r", encoding="utf-8") as handle:
        budget_rows = list(csv.DictReader(handle))
    assert any(row["allowed"] == "False" for row in budget_rows)

    report_text = (output_dir / "report.md").read_text(encoding="utf-8")
    assert "Threshold detector first entered violation at sample 4." in report_text


if __name__ == "__main__":
    test_decision_engine_integration()
