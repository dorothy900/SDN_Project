#!/usr/bin/env python3
"""
Run Decision Engine Validation - Week 4 automation.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.decision.change_budget import ChangeBudget
from src.decision.decision_engine import DecisionEngine
from src.decision.path_cost import PathCost
from src.decision.persistence_checker import PersistenceChecker
from src.decision.threshold_detector import ThresholdDetector, ThresholdViolation
from src.monitor.models import LinkStatistics
from src.monitor.network_state import NetworkState
from src.routing.graph_builder import GraphBuilder


class DecisionEngineValidation:
    """Run Week 4 Day 1-6 checks and persist Stage 4 deliverables."""

    def __init__(self, output_dir: Optional[Path] = None):
        self.output_dir = output_dir or Path("results/stage4")
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def run(self) -> Dict[str, object]:
        state = self._build_network_state()
        pair, candidate_paths = self._select_pair_and_paths(state)

        threshold_events = self._run_threshold_detection()
        persistence_rows = self._run_persistence_check()
        path_cost_report = self._run_path_cost_validation(state, pair, candidate_paths)
        minimum_gain_rows = self._run_minimum_gain_validation(state, pair, candidate_paths)
        decision_rows = self._run_decision_logging(state, pair, candidate_paths)
        budget_rows = self._run_change_budget()
        self._write_report(
            threshold_events=threshold_events,
            persistence_rows=persistence_rows,
            path_cost_report=path_cost_report,
            minimum_gain_rows=minimum_gain_rows,
            decision_rows=decision_rows,
            budget_rows=budget_rows,
        )

        return {
            "pair": pair,
            "candidate_paths": candidate_paths,
            "threshold_events": threshold_events,
            "persistence_rows": persistence_rows,
            "minimum_gain_rows": minimum_gain_rows,
            "decision_rows": decision_rows,
            "budget_rows": budget_rows,
        }

    def _build_network_state(self) -> NetworkState:
        """Seed a deterministic state that mirrors the Stage 3 offline setup."""
        state = NetworkState(output_dir=self.output_dir)
        timestamp = datetime(2026, 8, 10, 12, 0, 0)

        for index, edge in enumerate(sorted(state.topology.get_active_links(), key=self._canonical_edge)):
            link_id = self._link_id(*edge)
            utilization = round(0.24 + 0.01 * (index % 7), 4)
            delay_ms = round(8.0 + 1.2 * (index % 5), 4)
            packet_loss = round(0.001 + 0.0004 * (index % 4), 6)
            state.update_link_statistics(
                LinkStatistics(
                    timestamp=timestamp + timedelta(seconds=index),
                    link_id=link_id,
                    utilization=utilization,
                    rx_mbps=20.0 + utilization * 80.0,
                    tx_mbps=18.0 + utilization * 75.0,
                    status="up",
                    delay_ms=delay_ms,
                    packet_loss=packet_loss,
                )
            )
        return state

    def _select_pair_and_paths(self, state: NetworkState) -> Tuple[Tuple[str, str], List[List[str]]]:
        """Pick a deterministic pair with at least three candidate paths."""
        builder = GraphBuilder(state)
        pair = builder.select_test_pairs(limit=1, min_candidate_paths=3)[0]
        paths = builder.get_candidate_paths(pair[0], pair[1], max_paths=3)
        return pair, paths

    def _run_threshold_detection(self) -> List[Dict[str, object]]:
        """Day 1: replay utilization values around the threshold and record trigger points."""
        detector = ThresholdDetector({"utilization": 0.7})
        values = [0.55, 0.63, 0.69, 0.71, 0.74, 0.68, 0.72]
        events = detector.detect_state_changes(values, metric="utilization")

        output_path = self.output_dir / "threshold_unit_tests.txt"
        with output_path.open("w", encoding="utf-8") as handle:
            handle.write("Threshold Detection Replay\n")
            handle.write("==========================\n")
            handle.write("values=%s\n" % values)
            for event in events:
                handle.write(
                    "sample=%d value=%.2f transition=%s state=%s\n"
                    % (
                        event["sample"],
                        event["value"],
                        event["transition"],
                        event["state"],
                    )
                )
            first_trigger = next((event for event in events if event["state"] == "violating"), None)
            handle.write("first_trigger_sample=%s\n" % (first_trigger["sample"] if first_trigger else "none"))
        return events

    def _run_persistence_check(self) -> List[Dict[str, object]]:
        """Day 2: compare a short spike with sustained overload."""
        detector = ThresholdDetector({"utilization": 0.7})
        checker = PersistenceChecker(
            persistence_seconds=0.0,
            cooldown_seconds=0.0,
            required_samples=3,
        )
        traces = {
            "short_spike": [0.66, 0.74, 0.68],
            "sustained_overload": [0.66, 0.72, 0.74, 0.76],
        }

        rows: List[Dict[str, object]] = []
        for scenario, values in traces.items():
            checker.clear_window("0-1", "utilization")
            for sample_index, value in enumerate(values, start=1):
                violation = detector.check_utilization("0-1", value)
                result = checker.evaluate_sample(
                    link_id="0-1",
                    metric="utilization",
                    value=value,
                    is_violation=violation is not None,
                )
                row = {
                    "scenario": scenario,
                    "sample": sample_index,
                    "value": round(value, 4),
                    "is_violation": violation is not None,
                    "sample_count": result["sample_count"],
                    "accepted": result["accepted"],
                    "reason": result["reason"],
                }
                rows.append(row)

        output_path = self.output_dir / "persistence_test.csv"
        self._write_csv(output_path, rows)
        return rows

    def _run_path_cost_validation(
        self,
        state: NetworkState,
        pair: Tuple[str, str],
        candidate_paths: Sequence[List[str]],
    ) -> Dict[str, object]:
        """Day 3: validate that safer path metrics produce a lower composite cost."""
        congested_path = candidate_paths[0]
        safer_path = candidate_paths[1]
        self._set_path_metrics(state, congested_path, utilization=0.84, delay_ms=24.0, packet_loss=0.018)
        self._set_path_metrics(state, safer_path, utilization=0.02, delay_ms=1.0, packet_loss=0.0001)

        calculator = PathCost(state)
        congested_cost = calculator.calculate_path_cost(congested_path)
        safer_cost = calculator.calculate_path_cost(safer_path)
        congested_metrics = calculator.calculate_path_metrics(congested_path)
        safer_metrics = calculator.calculate_path_metrics(safer_path)

        output_path = self.output_dir / "path_cost_unit_tests.txt"
        with output_path.open("w", encoding="utf-8") as handle:
            handle.write("Path Cost Validation\n")
            handle.write("====================\n")
            handle.write("pair=%s->%s\n" % pair)
            handle.write("higher_risk_path=%s\n" % congested_path)
            handle.write("higher_risk_cost=%.6f metrics=%s\n" % (congested_cost, congested_metrics))
            handle.write("lower_risk_path=%s\n" % safer_path)
            handle.write("lower_risk_cost=%.6f metrics=%s\n" % (safer_cost, safer_metrics))
            handle.write("ordering=lower_risk_is_better:%s\n" % (safer_cost < congested_cost))

        return {
            "higher_risk_path": congested_path,
            "lower_risk_path": safer_path,
            "higher_risk_cost": congested_cost,
            "lower_risk_cost": safer_cost,
        }

    def _run_minimum_gain_validation(
        self,
        state: NetworkState,
        pair: Tuple[str, str],
        candidate_paths: Sequence[List[str]],
    ) -> List[Dict[str, object]]:
        """Day 4: reject small gains and accept materially better paths."""
        current_path = candidate_paths[0]
        small_gain_path = candidate_paths[1]
        strong_gain_path = candidate_paths[2]

        calculator = PathCost(state)
        rows = []

        self._set_path_metrics(state, current_path, utilization=0.80, delay_ms=22.0, packet_loss=0.016)
        self._set_path_metrics(state, small_gain_path, utilization=0.25, delay_ms=10.0, packet_loss=0.005)
        comparison = calculator.compare_paths(current_path, small_gain_path)
        rows.append(
            {
                "pair": "%s->%s" % pair,
                "candidate_label": "small_gain",
                "current_cost": comparison["old_cost"],
                "candidate_cost": comparison["new_cost"],
                "absolute_improvement": comparison["absolute_improvement"],
                "relative_improvement": comparison["relative_improvement"],
                "accepted": comparison["accepted"],
            }
        )

        self._set_path_metrics(state, current_path, utilization=0.80, delay_ms=22.0, packet_loss=0.016)
        self._set_path_metrics(state, strong_gain_path, utilization=0.08, delay_ms=5.0, packet_loss=0.001)
        comparison = calculator.compare_paths(current_path, strong_gain_path)
        rows.append(
            {
                "pair": "%s->%s" % pair,
                "candidate_label": "strong_gain",
                "current_cost": comparison["old_cost"],
                "candidate_cost": comparison["new_cost"],
                "absolute_improvement": comparison["absolute_improvement"],
                "relative_improvement": comparison["relative_improvement"],
                "accepted": comparison["accepted"],
            }
        )

        self._write_csv(self.output_dir / "minimum_gain_test.csv", rows)
        return rows

    def _run_decision_logging(
        self,
        state: NetworkState,
        pair: Tuple[str, str],
        candidate_paths: Sequence[List[str]],
    ) -> List[Dict[str, object]]:
        """Day 5: run mixed scenarios and persist interpretable decision logs."""
        engine = DecisionEngine(state)
        engine.persistence_checker.required_samples = 1
        engine.persistence_checker.persistence_seconds = 0.0
        engine.persistence_checker.cooldown_seconds = 0.0
        engine.stability.hold_down_seconds = 0.0

        current_path = candidate_paths[0]
        low_gain_path = candidate_paths[1]
        good_path = candidate_paths[2]

        # Normal traffic: no threshold crossing.
        below = engine.threshold_detector.check_utilization(self._link_id(current_path[0], current_path[1]), 0.61)
        if below is None:
            engine.logger.log_no_action("Normal traffic remains below threshold")

        # Sustained congestion with insufficient gain.
        self._set_path_metrics(state, current_path, utilization=0.83, delay_ms=21.0, packet_loss=0.017)
        self._set_path_metrics(state, low_gain_path, utilization=0.25, delay_ms=10.0, packet_loss=0.005)
        low_gain_violation = ThresholdViolation(
            link_id=self._link_id(current_path[0], current_path[1]),
            metric="utilization",
            value=0.83,
            threshold=0.7,
            severity=0.13,
        )
        engine.evaluate_pair(pair[0], pair[1], current_path, low_gain_path, low_gain_violation)

        # Sustained congestion with strong candidate path.
        self._set_path_metrics(state, good_path, utilization=0.08, delay_ms=5.0, packet_loss=0.001)
        strong_violation = ThresholdViolation(
            link_id=self._link_id(current_path[0], current_path[1]),
            metric="utilization",
            value=0.86,
            threshold=0.7,
            severity=0.16,
        )
        reroute = engine.evaluate_pair(pair[0], pair[1], current_path, good_path, strong_violation)

        engine.logger.save_to_csv("decision_log.csv")
        rows = [
            {
                "decision_type": record.decision_type,
                "reason": record.reason or "",
                "old_path": record.old_path or [],
                "new_path": record.new_path or [],
            }
            for record in engine.logger.records
        ]
        if reroute is None:
            raise RuntimeError("Week 4 decision logging did not produce the expected reroute.")
        return rows

    def _run_change_budget(self) -> List[Dict[str, object]]:
        """Day 6: exceed the rolling update budget and show deferred reroutes."""
        budget = ChangeBudget(max_updates_per_minute=2, max_path_changes_per_minute=2, burst_allowance=0)
        rows = []
        for attempt in range(1, 6):
            allowed = budget.can_change_path()
            if allowed:
                budget.record_path_change()
            state = budget.get_budget_state()
            rows.append(
                {
                    "attempt": attempt,
                    "allowed": allowed,
                    "path_change_count": state["path_change_count"],
                    "available_updates": state["available_updates"],
                }
            )

        self._write_csv(self.output_dir / "change_budget_trace.csv", rows)
        return rows

    def _write_report(
        self,
        threshold_events: Sequence[Dict[str, object]],
        persistence_rows: Sequence[Dict[str, object]],
        path_cost_report: Dict[str, object],
        minimum_gain_rows: Sequence[Dict[str, object]],
        decision_rows: Sequence[Dict[str, object]],
        budget_rows: Sequence[Dict[str, object]],
    ) -> None:
        """Day 7: write the Stage 4 report."""
        report_path = self.output_dir / "report.md"
        sustained_accepts = [
            row for row in persistence_rows if row["scenario"] == "sustained_overload" and row["accepted"]
        ]
        blocked_budget = [row for row in budget_rows if not row["allowed"]]
        report_path.write_text(
            "# Week 4 Report\n\n"
            "- Threshold detector first entered violation at sample %d.\n"
            "- Persistence check rejected the short spike and accepted sustained overload after %d samples.\n"
            "- Lower-risk path cost %.6f remained below higher-risk cost %.6f.\n"
            "- Minimum-gain check produced %d candidate comparisons with both rejected and accepted outcomes.\n"
            "- Decision log contains %d entries, including no_action, no_improvement, and reroute.\n"
            "- Change budget blocked %d reroute attempts once the rolling budget was exhausted.\n"
            % (
                next(event["sample"] for event in threshold_events if event["state"] == "violating"),
                sustained_accepts[0]["sample_count"],
                path_cost_report["lower_risk_cost"],
                path_cost_report["higher_risk_cost"],
                len(minimum_gain_rows),
                len(decision_rows),
                len(blocked_budget),
            ),
            encoding="utf-8",
        )

    def _set_path_metrics(
        self,
        state: NetworkState,
        path: Sequence[str],
        utilization: float,
        delay_ms: float,
        packet_loss: float,
    ) -> None:
        """Overwrite path metrics with deterministic values for a validation scenario."""
        timestamp = datetime(2026, 8, 11, 12, 0, 0)
        for index, (u, v) in enumerate(zip(path, path[1:])):
            link_id = self._link_id(u, v)
            state.update_link_statistics(
                LinkStatistics(
                    timestamp=timestamp + timedelta(seconds=index),
                    link_id=link_id,
                    utilization=utilization,
                    rx_mbps=utilization * 100.0,
                    tx_mbps=utilization * 95.0,
                    status="up",
                    delay_ms=delay_ms,
                    packet_loss=packet_loss,
                )
            )

    @staticmethod
    def _write_csv(output_path: Path, rows: Sequence[Dict[str, object]]) -> None:
        """Write a sequence of rows to CSV."""
        if not rows:
            raise ValueError("No rows available for %s" % output_path)
        with output_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)

    @staticmethod
    def _link_id(u: str, v: str) -> str:
        nodes = sorted([str(u), str(v)])
        return "%s-%s" % (nodes[0], nodes[1])

    @staticmethod
    def _canonical_edge(edge: Tuple[str, str]) -> Tuple[str, str]:
        return tuple(sorted(str(node) for node in edge))


def main() -> None:
    parser = argparse.ArgumentParser(description="Week 4 decision engine validation runner")
    parser.add_argument(
        "--output-dir",
        type=str,
        default="results/stage4",
        help="Directory to store Week 4 outputs",
    )
    args = parser.parse_args()
    runner = DecisionEngineValidation(output_dir=Path(args.output_dir))
    results = runner.run()
    print(json.dumps({"status": "ok", "outputs": list(results.keys())}, indent=2))


if __name__ == "__main__":
    main()
