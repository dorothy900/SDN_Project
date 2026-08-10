#!/usr/bin/env python3
"""
Run Sensitivity Analysis - parameter justification for config/decision.yaml.

The thresholds in config/decision.yaml (utilization=0.7, persistence samples,
hold-down duration, ...) were set once at project scaffolding and never
empirically revisited. This script sweeps the parameters that most directly
shape "Proposed"'s behavior and re-runs it, via the real DecisionEngine
(src/decision/decision_engine.py) against the real simulation harness
(experiments/simulation_common.py), so the trade-offs behind the chosen
defaults are backed by actual measured data rather than asserted.

Two sweeps:
  A. utilization threshold x persistence-sample-count, replayed end-to-end
     through the real DecisionEngine against the Increasing Load ramp
     (Experiment A's trace) -- shows how early/late Proposed reacts and how
     much churn results.
  B. hold-down duration, exercised directly against StabilityManager (the
     same real class Stage 5's T-011 validates) with a steady stream of
     reroute-worthy attempts -- shows how many of them each setting allows
     versus blocks.
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from src.stability.stability_manager import StabilityManager

from .simulation_common import (
    PRIMARY_PAIR,
    SAMPLE_INTERVAL_S,
    ProposedDriver,
    build_network_state,
    link_id,
    make_drivers,
    set_link_condition,
)

THRESHOLD_GRID = [0.6, 0.7, 0.8]
PERSISTENCE_GRID = [1, 3, 5]
HOLD_DOWN_GRID = [0, 5, 10, 20, 30]

# Experiment A's own ramp: 10% -> 90% of capacity over 12 samples.
RAMP_SAMPLES = 12

CURRENT_DEFAULT_THRESHOLD = 0.7
CURRENT_DEFAULT_PERSISTENCE = 3
CURRENT_DEFAULT_HOLD_DOWN = 10.0


class SensitivityAnalysis:
    """Sweep key decision-engine parameters against real scenario traces."""

    def __init__(self, output_dir: Optional[Path] = None):
        self.output_dir = output_dir or Path("results/pilot/sensitivity")
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def run(self) -> Dict[str, object]:
        threshold_rows = self._run_threshold_persistence_sweep()
        hold_down_rows = self._run_hold_down_sweep()
        self._write_csv(self.output_dir / "threshold_persistence_sweep.csv", threshold_rows)
        self._write_csv(self.output_dir / "hold_down_sweep.csv", hold_down_rows)
        self._write_report(threshold_rows, hold_down_rows)
        return {"threshold_persistence_sweep": threshold_rows, "hold_down_sweep": hold_down_rows}

    def _run_threshold_persistence_sweep(self) -> List[Dict[str, object]]:
        rows: List[Dict[str, object]] = []
        src, dst = PRIMARY_PAIR

        for threshold in THRESHOLD_GRID:
            for persistence in PERSISTENCE_GRID:
                state = build_network_state(self.output_dir, seed=0)
                drivers = make_drivers(state, src, dst, threshold=threshold, persistence_required_samples=persistence)
                proposed: ProposedDriver = drivers["proposed"]
                hotspot_link = link_id(proposed.path[0], proposed.path[1])

                first_reroute_sample: Optional[int] = None
                reroute_count = 0
                total_updates = 0
                for sample in range(1, RAMP_SAMPLES + 1):
                    load_factor = 0.10 + (0.90 - 0.10) * (sample - 1) / (RAMP_SAMPLES - 1)
                    set_link_condition(state, hotspot_link, utilization=load_factor)
                    result = proposed.step(
                        now_s=sample * SAMPLE_INTERVAL_S,
                        hotspot_link=hotspot_link,
                        hotspot_utilization=load_factor,
                    )
                    if result["reroute"]:
                        reroute_count += 1
                        total_updates += int(result["flow_updates"])
                        if first_reroute_sample is None:
                            first_reroute_sample = sample

                rows.append(
                    {
                        "utilization_threshold": threshold,
                        "persistence_required_samples": persistence,
                        "is_current_default": threshold == CURRENT_DEFAULT_THRESHOLD
                        and persistence == CURRENT_DEFAULT_PERSISTENCE,
                        "first_reroute_sample": first_reroute_sample if first_reroute_sample is not None else "",
                        "reroute_count": reroute_count,
                        "rule_updates": total_updates,
                    }
                )
        return rows

    def _run_hold_down_sweep(self) -> List[Dict[str, object]]:
        """
        Exercise StabilityManager.allow_reroute()/record_reroute() directly,
        the same real class Stage 5's T-011 already validates, rather than
        through the full path-selection pipeline: after a real reroute moves
        traffic off the congested link, find_best_path() generally re-finds
        that same new path on the next check, so a single-hotspot-link trace
        never produces a second distinct "better path" to hold down against
        -- hold-down's effect would look identical at every setting for the
        wrong reason (nothing to suppress), not because hold-down doesn't
        matter. Driving the same real stability primitive directly against a
        steady stream of reroute-worthy attempts isolates what hold-down
        alone contributes.
        """
        rows: List[Dict[str, object]] = []
        pair = PRIMARY_PAIR
        attempt_times = [i * 5.0 for i in range(9)]  # 9 attempts, 5s apart, 40s span

        for hold_down_seconds in HOLD_DOWN_GRID:
            manager = StabilityManager(hold_down_seconds=float(hold_down_seconds))
            allowed = 0
            for t in attempt_times:
                if manager.allow_reroute(pair, emergency=False, now=t):
                    allowed += 1
                    manager.record_reroute(pair, emergency=False, now=t)

            rows.append(
                {
                    "hold_down_seconds": hold_down_seconds,
                    "is_current_default": hold_down_seconds == CURRENT_DEFAULT_HOLD_DOWN,
                    "attempts": len(attempt_times),
                    "reroutes_allowed": allowed,
                    "reroutes_blocked": len(attempt_times) - allowed,
                }
            )
        return rows

    def _write_report(
        self,
        threshold_rows: Sequence[Dict[str, object]],
        hold_down_rows: Sequence[Dict[str, object]],
    ) -> None:
        lines = ["# Sensitivity Analysis Report", ""]

        lines.append("## Utilization threshold x persistence-sample-count")
        lines.append("(Increasing Load ramp, 10%->90% over 12 samples)")
        lines.append("")
        for row in threshold_rows:
            marker = " <- current default" if row["is_current_default"] else ""
            lines.append(
                "- threshold=%.2f persistence=%d samples: first reroute at sample %s, %d reroute(s), %d rule update(s)%s"
                % (
                    row["utilization_threshold"], row["persistence_required_samples"],
                    row["first_reroute_sample"], row["reroute_count"], row["rule_updates"], marker,
                )
            )

        by_threshold: Dict[float, List[Dict[str, object]]] = {}
        for row in threshold_rows:
            by_threshold.setdefault(row["utilization_threshold"], []).append(row)
        higher_persistence_reacts_no_earlier = all(
            self._monotonic_non_decreasing([r["first_reroute_sample"] for r in sorted(
                by_threshold[t], key=lambda r: r["persistence_required_samples"]
            ) if r["first_reroute_sample"] != ""])
            for t in by_threshold
        )
        lines.append("")
        lines.append(
            "- Within each threshold, first-reroute sample is non-decreasing as persistence rises: %s "
            "(requiring more confirming samples never reacts earlier, as expected -- and can miss the "
            "event entirely within a bounded window, as threshold=0.80/persistence=3 or 5 do here)."
            % higher_persistence_reacts_no_earlier
        )

        lines.append("")
        lines.append("## Hold-down duration")
        lines.append("(StabilityManager.allow_reroute()/record_reroute() directly, 9 reroute-worthy attempts 5s apart over 40s)")
        lines.append("")
        for row in hold_down_rows:
            marker = " <- current default" if row["is_current_default"] else ""
            lines.append(
                "- hold_down=%ss: %d/%d attempts allowed, %d blocked%s"
                % (row["hold_down_seconds"], row["reroutes_allowed"], row["attempts"], row["reroutes_blocked"], marker)
            )

        allowed_counts = [row["reroutes_allowed"] for row in sorted(hold_down_rows, key=lambda r: r["hold_down_seconds"])]
        hold_down_reduces_churn = self._monotonic_non_increasing(allowed_counts)
        lines.append("")
        lines.append(
            "- Attempts allowed is non-increasing as hold-down duration grows: %s "
            "(longer hold-down blocks at least as many of the same 9 attempts)."
            % hold_down_reduces_churn
        )

        lines.append("")
        lines.append("## Reading for the current defaults (threshold=0.70, persistence=3, hold_down=10s)")
        lines.append(
            "These sit in the middle of every grid tested here, not at either extreme: threshold=0.70 is "
            "neither the most reactive (0.60) nor the most tolerant (0.80) option; persistence=3 is neither "
            "immediate (1) nor slow (5); hold_down=10s sits mid-range across the 0/5/10/20/30 grid. The data "
            "above characterizes the actual trade-off each setting buys (reaction latency vs churn) rather than "
            "asserting the defaults are optimal -- see the numbers per row to judge whether a different point "
            "on this trade-off suits a specific deployment better."
        )

        (self.output_dir / "sensitivity_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    @staticmethod
    def _monotonic_non_increasing(values: Sequence[object]) -> bool:
        nums = [float(v) for v in values if v != ""]
        return all(nums[i] >= nums[i + 1] for i in range(len(nums) - 1))

    @staticmethod
    def _monotonic_non_decreasing(values: Sequence[object]) -> bool:
        nums = [float(v) for v in values if v != ""]
        return all(nums[i] <= nums[i + 1] for i in range(len(nums) - 1))

    @staticmethod
    def _write_csv(output_path: Path, rows: Sequence[Dict[str, object]]) -> None:
        if not rows:
            raise ValueError("No rows available for %s" % output_path)
        with output_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="Decision-engine parameter sensitivity analysis")
    parser.add_argument("--output-dir", type=str, default="results/pilot/sensitivity")
    args = parser.parse_args()

    runner = SensitivityAnalysis(output_dir=Path(args.output_dir))
    results = runner.run()
    print(json.dumps({"status": "ok", "rows": {k: len(v) for k, v in results.items()}}, indent=2))


if __name__ == "__main__":
    main()
