#!/usr/bin/env python3
"""
Run Pilot Experiments - Week 6 end-to-end automation.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from evaluation.calculate_metrics import MetricsCalculator
from evaluation.parse_results import ResultParser
from experiments.congestion import CongestionScenario
from experiments.failure_recovery import FailureRecoveryScenario
from experiments.increasing_load import IncreasingLoadScenario
from experiments.priority_policy import PriorityPolicyScenario
from experiments.stale_stats import StaleStatsScenario
from experiments.traffic_generator import TrafficGenerator


class PilotExperimentRunner:
    """Run Week 6 scenarios, metrics parsing, repeated trials, and final report."""

    def __init__(self, output_dir: Optional[Path] = None):
        self.output_dir = output_dir or Path("results/pilot")
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.parser = ResultParser()
        self.calculator = MetricsCalculator()
        self.generator = TrafficGenerator(output_dir=self.output_dir)

    def run(self, repeat: int = 3, scenario: str = "all") -> Dict[str, object]:
        """Execute the selected Week 6 scenarios and generate all pilot outputs."""
        flows = self.generator.generate_concurrent_flows()
        self.generator.write_log(self.output_dir / "traffic_generator.log")

        selected = self._scenario_selection(scenario)
        all_summary_rows: List[Dict[str, object]] = []
        scenario_outputs: List[Path] = []

        for run_index in range(1, repeat + 1):
            if "scenario1-2" in selected:
                scenario_outputs.append(IncreasingLoadScenario(self.output_dir / "scenario1-2").run(flows, run_index=run_index))
                scenario_outputs.append(CongestionScenario(self.output_dir / "scenario1-2").run(flows, run_index=run_index))
            if "scenario3" in selected:
                scenario_outputs.append(FailureRecoveryScenario(self.output_dir / "scenario3").run(flows, run_index=run_index))
            if "scenario4" in selected:
                scenario_outputs.append(StaleStatsScenario(self.output_dir / "scenario4").run(flows, run_index=run_index))
            if "scenario5" in selected:
                scenario_outputs.append(PriorityPolicyScenario(self.output_dir / "scenario5").run(flows, run_index=run_index))

        for output in scenario_outputs:
            parsed = self.parser.parse_csv(str(output))
            summaries = self._summaries_for_rows(parsed)
            all_summary_rows.extend(summaries)

        pilot_summary = self._deduplicate_latest_summary(all_summary_rows)
        repeated_rows = self.calculator.aggregate_repeated_runs(all_summary_rows)
        self._write_csv(self.output_dir / "pilot_summary.csv", pilot_summary)
        self._write_csv(self.output_dir / "full_results_repeated.csv", repeated_rows)
        self._write_final_report(pilot_summary, repeated_rows, repeat)

        return {
            "traffic_log": str(self.output_dir / "traffic_generator.log"),
            "summary_rows": pilot_summary,
            "repeated_rows": repeated_rows,
        }

    def _summaries_for_rows(self, rows: Sequence[Dict[str, object]]) -> List[Dict[str, object]]:
        """Group raw rows by scenario/algorithm/run and summarize each group."""
        grouped: Dict[tuple, List[Dict[str, object]]] = {}
        for row in rows:
            key = (row["scenario"], row["algorithm"], row["run"])
            grouped.setdefault(key, []).append(row)

        summaries: List[Dict[str, object]] = []
        for (scenario, algorithm, run_index), items in sorted(grouped.items()):
            summary = self.calculator.calculate_summary(items, scenario=str(scenario), algorithm=str(algorithm))
            summary["run"] = int(run_index)
            summaries.append(summary)
        return summaries

    @staticmethod
    def _deduplicate_latest_summary(rows: Sequence[Dict[str, object]]) -> List[Dict[str, object]]:
        """Keep the latest run summary for each scenario/algorithm pair for pilot_summary.csv."""
        latest: Dict[tuple, Dict[str, object]] = {}
        for row in rows:
            key = (row["scenario"], row["algorithm"])
            if key not in latest or int(row["run"]) > int(latest[key]["run"]):
                latest[key] = row
        return [latest[key] for key in sorted(latest)]

    @staticmethod
    def _scenario_selection(scenario: str) -> List[str]:
        if scenario == "all":
            return ["scenario1-2", "scenario3", "scenario4"]
        if scenario == "scenario1-2":
            return ["scenario1-2"]
        if scenario == "scenario3":
            return ["scenario3"]
        if scenario == "scenario4":
            return ["scenario4"]
        if scenario == "congestion":
            return ["scenario1-2"]
        if scenario == "failure_recovery":
            return ["scenario3"]
        if scenario == "stale_stats":
            return ["scenario4"]
        if scenario == "scenario5":
            return ["scenario5"]
        if scenario == "priority_policy":
            return ["scenario5"]
        if scenario == "all_plus_priority":
            return ["scenario1-2", "scenario3", "scenario4", "scenario5"]
        raise ValueError("Unsupported scenario selection: %s" % scenario)

    def _write_final_report(
        self,
        pilot_summary: Sequence[Dict[str, object]],
        repeated_rows: Sequence[Dict[str, object]],
        repeat: int,
    ) -> None:
        """Write the Week 6 end-to-end validation report."""
        comparison_lines = []
        for row in sorted(pilot_summary, key=lambda entry: (str(entry["scenario"]), str(entry["algorithm"]))):
            comparison_lines.append(
                "- %s / %s: delay=%.3fms throughput=%.3fMbps loss=%.4f reroutes=%d updates=%d decision_time=%.3fms"
                % (
                    row["scenario"],
                    row["algorithm"],
                    row["avg_delay_ms"],
                    row["avg_throughput_mbps"],
                    row["avg_packet_loss"],
                    row["reroute_count"],
                    row["flow_update_count"],
                    row["decision_time_avg_ms"],
                )
            )

        report_path = self.output_dir / "end_to_end_validation_report.md"
        report_path.write_text(
            "# End-to-end Validation Report\n\n"
            "- Week 6 scenarios executed from one command with repeat=%d.\n"
            "- Raw scenario outputs were parsed into pilot-level metrics.\n"
            "- Repeated runs were aggregated with mean and 95%% confidence interval.\n"
            "- Algorithms remain comparable across increasing load, congestion, failure/recovery, and stale statistics scenarios.\n\n"
            "## Latest Pilot Summary\n\n%s\n\n"
            "## Repeated Aggregation\n\n"
            "- Aggregated rows: %d\n"
            % (repeat, "\n".join(comparison_lines), len(repeated_rows)),
            encoding="utf-8",
        )

    @staticmethod
    def _write_csv(output_path: Path, rows: Sequence[Dict[str, object]]) -> None:
        if not rows:
            raise ValueError("No rows available for %s" % output_path)
        with output_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="Week 6 pilot experiment runner")
    parser.add_argument("--repeat", type=int, default=3, help="Number of repeated runs")
    parser.add_argument(
        "--scenario",
        type=str,
        default="all",
        help="Scenario selection: all | scenario1-2 | scenario3 | scenario4",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default="results/pilot",
        help="Directory to store Week 6 outputs",
    )
    args = parser.parse_args()
    runner = PilotExperimentRunner(output_dir=Path(args.output_dir))
    results = runner.run(repeat=args.repeat, scenario=args.scenario)
    print(json.dumps({"status": "ok", "outputs": list(results.keys())}, indent=2))


if __name__ == "__main__":
    main()
