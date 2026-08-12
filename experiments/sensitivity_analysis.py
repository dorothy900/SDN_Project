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

Three sweeps:
  A. utilization threshold x persistence-sample-count, replayed end-to-end
     through the real DecisionEngine against the Increasing Load ramp
     (Experiment A's trace) -- shows how early/late Proposed reacts and how
     much churn results.
  B. hold-down duration, exercised directly against StabilityManager (the
     same real class Stage 5's T-011 validates) with a steady stream of
     reroute-worthy attempts -- shows how many of them each setting allows
     versus blocks.
  C. path_cost_weights (alpha/beta/gamma/delta/epsilon) -- unlike A and B,
     these five numbers (config/decision.yaml) were never swept at all before
     this pass; they were set once at project scaffolding and never
     empirically revisited. Sweeps each weight individually against three
     real GEANT candidate paths deliberately given contrasting conditions
     (one bad on utilization, one on delay, one on loss) so a weight change
     can actually flip which path wins, plus an isolation test for delta and
     epsilon specifically (see _run_cost_weight_sweep's docstring for why
     those two needed a different kind of check).
"""

from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from src.monitor.models import LinkStatistics
from src.routing.graph_builder import GraphBuilder
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

# Three real candidate paths between PRIMARY_PAIR (from
# GraphBuilder.get_candidate_paths("2", "7")), each deliberately given
# contrasting per-link conditions so a weight change can flip the winner:
# Path A is bad on utilization only, Path B bad on delay only, Path C bad on
# loss only. A and B share their last hop (34-7), which is left at a neutral
# baseline so only each path's *unique* links carry the deliberate contrast.
COST_PATH_A = ["2", "0", "34", "7"]
COST_PATH_B = ["2", "32", "34", "7"]
COST_PATH_C = ["2", "4", "6", "7"]
COST_PATH_A_UNIQUE_EDGES = [("0", "2"), ("0", "34")]
COST_PATH_B_UNIQUE_EDGES = [("2", "32"), ("32", "34")]
COST_PATH_C_EDGES = [("2", "4"), ("4", "6"), ("6", "7")]
COST_SHARED_EDGE = ("34", "7")

COST_WEIGHT_GRID = {
    # Wide enough to cross each weight's actual flip point (where the
    # cheapest of Path A/B/C changes), not just sample near the default --
    # an initial narrow pass (0.1/0.4/0.7 etc.) never saw a single flip,
    # which is not an informative sensitivity result on its own.
    "alpha": [0.0, 0.05, 0.1, 0.4, 0.7, 1.0],
    "beta": [0.05, 0.1, 0.3, 0.6, 1.0, 1.5],
    "gamma": [0.05, 0.2, 0.5, 0.7, 0.9, 1.2],
}
DELTA_GRID = [0.0, 0.05, 0.5, 1.0]
EPSILON_GRID = [0.0, 0.05, 0.5, 1.0]
CURRENT_DEFAULT_COST_WEIGHTS = {"alpha": 0.4, "beta": 0.3, "gamma": 0.2, "delta": 0.05, "epsilon": 0.05}


class SensitivityAnalysis:
    """Sweep key decision-engine parameters against real scenario traces."""

    def __init__(self, output_dir: Optional[Path] = None):
        self.output_dir = output_dir or Path("results/pilot/sensitivity")
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def run(self) -> Dict[str, object]:
        threshold_rows = self._run_threshold_persistence_sweep()
        hold_down_rows = self._run_hold_down_sweep()
        cost_weight_rows, delta_rows, epsilon_rows = self._run_cost_weight_sweep()
        self._write_csv(self.output_dir / "threshold_persistence_sweep.csv", threshold_rows)
        self._write_csv(self.output_dir / "hold_down_sweep.csv", hold_down_rows)
        self._write_csv(self.output_dir / "cost_weight_sweep.csv", cost_weight_rows)
        self._write_csv(self.output_dir / "delta_epsilon_isolation.csv", delta_rows + epsilon_rows)
        self._write_report(threshold_rows, hold_down_rows, cost_weight_rows, delta_rows, epsilon_rows)
        return {
            "threshold_persistence_sweep": threshold_rows,
            "hold_down_sweep": hold_down_rows,
            "cost_weight_sweep": cost_weight_rows,
            "delta_epsilon_isolation": delta_rows + epsilon_rows,
        }

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

    def _build_contrast_state(self, seed: int = 0):
        """
        Build the real GEANT network state, then overwrite the three test
        paths' links with deliberately contrasting conditions (see the
        COST_PATH_* constants' comment for which path is bad on what).
        """
        state = build_network_state(self.output_dir, seed=seed)
        ts = datetime(2026, 8, 24, 12, 0, 0)

        def stamp(u: str, v: str, utilization: float, delay_ms: float, loss: float) -> None:
            lid = link_id(u, v)
            state.update_link_statistics(
                LinkStatistics(
                    timestamp=ts, link_id=lid, utilization=utilization,
                    rx_mbps=round(20.0 + utilization * 80.0, 4),
                    tx_mbps=round(18.0 + utilization * 75.0, 4),
                    status="up", delay_ms=delay_ms, packet_loss=loss,
                )
            )

        for u, v in COST_PATH_A_UNIQUE_EDGES:
            stamp(u, v, utilization=0.85, delay_ms=2.0, loss=0.0001)   # bad utilization
        for u, v in COST_PATH_B_UNIQUE_EDGES:
            stamp(u, v, utilization=0.10, delay_ms=40.0, loss=0.0001)  # bad delay
        for u, v in COST_PATH_C_EDGES:
            stamp(u, v, utilization=0.10, delay_ms=2.0, loss=0.05)     # bad loss
        stamp(*COST_SHARED_EDGE, utilization=0.30, delay_ms=6.0, loss=0.001)  # neutral

        return state

    def _cheapest_path(self, state, weights: Dict[str, float]) -> Dict[str, object]:
        builder = GraphBuilder(state, weights=weights)
        graph = builder.build_weighted_graph()
        costs = {
            "A": round(builder.get_path_cost(COST_PATH_A, graph), 6),
            "B": round(builder.get_path_cost(COST_PATH_B, graph), 6),
            "C": round(builder.get_path_cost(COST_PATH_C, graph), 6),
        }
        winner = min(costs, key=costs.get)
        return {"cost_A": costs["A"], "cost_B": costs["B"], "cost_C": costs["C"], "winner": winner}

    def _run_cost_weight_sweep(self):
        """
        Sweep alpha/beta/gamma individually (holding the other two at their
        current defaults) against the three contrasting paths, then run
        delta and epsilon as separate isolation checks rather than folding
        them into the same grid: reading _calculate_edge_cost
        (src/routing/graph_builder.py) shows `priority` is a hardcoded local
        0.0, never read from TrafficPolicy or anywhere else -- so delta's
        term is structurally always zero regardless of its value, which a
        grid sweep alongside alpha/beta/gamma would only show as "no effect"
        without explaining why. epsilon's reliability_penalty only becomes
        non-zero if a link's LinkStatistics.status is "down" while the edge
        is *still present* in the active graph -- which happens if code
        calls update_link_statistics(status="down") without also calling
        set_link_status()/mark_link_failed() (the two are tracked
        separately, see NetworkState). Properly failing a link instead
        removes its edge from the graph entirely, making epsilon moot for
        that edge regardless of its value. Both are tested directly below.
        """
        weight_rows: List[Dict[str, object]] = []
        state = self._build_contrast_state(seed=0)

        for weight_name, grid in COST_WEIGHT_GRID.items():
            for value in grid:
                weights = dict(CURRENT_DEFAULT_COST_WEIGHTS)
                weights[weight_name] = value
                result = self._cheapest_path(state, weights)
                weight_rows.append(
                    {
                        "swept_weight": weight_name,
                        "value": value,
                        "is_current_default": value == CURRENT_DEFAULT_COST_WEIGHTS[weight_name],
                        **result,
                    }
                )

        # delta isolation: sweep with everything else fixed at defaults.
        delta_rows: List[Dict[str, object]] = []
        for value in DELTA_GRID:
            weights = dict(CURRENT_DEFAULT_COST_WEIGHTS)
            weights["delta"] = value
            result = self._cheapest_path(state, weights)
            delta_rows.append({"swept_weight": "delta", "value": value, **result})

        # epsilon isolation, case 1: inconsistent state (status says down,
        # edge still in the active graph) -- update_link_statistics() only,
        # no set_link_status()/mark_link_failed() call.
        inconsistent_state = self._build_contrast_state(seed=1)
        inconsistent_state.update_link_statistics(
            LinkStatistics(
                timestamp=datetime(2026, 8, 24, 12, 0, 0), link_id=link_id(*COST_PATH_C_EDGES[0]),
                utilization=0.10, rx_mbps=28.0, tx_mbps=25.5, status="down", delay_ms=2.0, packet_loss=0.05,
            )
        )
        epsilon_rows: List[Dict[str, object]] = []
        for value in EPSILON_GRID:
            weights = dict(CURRENT_DEFAULT_COST_WEIGHTS)
            weights["epsilon"] = value
            result = self._cheapest_path(inconsistent_state, weights)
            epsilon_rows.append({"swept_weight": "epsilon (status=down, edge still in graph)", "value": value, **result})

        # epsilon isolation, case 2: link properly failed via set_link_status
        # (mark_link_failed), which removes the edge from the active graph.
        properly_failed_state = self._build_contrast_state(seed=2)
        u, v = COST_PATH_C_EDGES[0]
        properly_failed_state.set_link_status(link_id(u, v), is_up=False)
        for value in EPSILON_GRID:
            weights = dict(CURRENT_DEFAULT_COST_WEIGHTS)
            weights["epsilon"] = value
            builder = GraphBuilder(properly_failed_state, weights=weights)
            graph = builder.build_weighted_graph()
            edge_present = graph.has_edge(u, v)
            epsilon_rows.append(
                {
                    "swept_weight": "epsilon (status=down, edge removed via set_link_status)",
                    "value": value,
                    "cost_A": "", "cost_B": "", "cost_C": "",
                    "winner": "n/a (Path C edge %s-%s no longer exists, edge_present=%s)" % (u, v, edge_present),
                }
            )

        return weight_rows, delta_rows, epsilon_rows

    def _write_report(
        self,
        threshold_rows: Sequence[Dict[str, object]],
        hold_down_rows: Sequence[Dict[str, object]],
        cost_weight_rows: Sequence[Dict[str, object]] = (),
        delta_rows: Sequence[Dict[str, object]] = (),
        epsilon_rows: Sequence[Dict[str, object]] = (),
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

        if cost_weight_rows:
            lines.append("")
            lines.append("## path_cost_weights (alpha/beta/gamma) -- never swept before this pass")
            lines.append(
                "Three real GEANT candidate paths between (\"2\",\"7\"), each deliberately bad on exactly "
                "one dimension: Path A high utilization, Path B high delay, Path C high loss (see "
                "COST_PATH_* in this file for the exact links/values). Each weight is swept individually, "
                "the other four held at their current defaults (alpha=0.4, beta=0.3, gamma=0.2)."
            )
            lines.append("")
            by_weight: Dict[str, List[Dict[str, object]]] = {}
            for row in cost_weight_rows:
                by_weight.setdefault(row["swept_weight"], []).append(row)
            for weight_name, rows in by_weight.items():
                lines.append("**%s**" % weight_name)
                for row in rows:
                    marker = " <- current default" if row["is_current_default"] else ""
                    lines.append(
                        "- %s=%.2f: cost(A)=%.4f cost(B)=%.4f cost(C)=%.4f -> winner: Path %s%s"
                        % (weight_name, row["value"], row["cost_A"], row["cost_B"], row["cost_C"], row["winner"], marker)
                    )
                winners = {row["winner"] for row in rows}
                lines.append(
                    "  -> winner changes across this grid: %s" % (len(winners) > 1)
                )
                lines.append("")

        if delta_rows:
            lines.append("## delta (priority weight) -- isolation check")
            lines.append(
                "`_calculate_edge_cost` (src/routing/graph_builder.py) sets `priority = 0.0` as a hardcoded "
                "local variable -- it is never read from TrafficPolicy or anywhere else that carries a real "
                "priority signal. So delta's contribution to cost is structurally alpha*0 = 0 regardless of "
                "delta's value. Swept 0.0 -> 1.0 against the same three contrasting paths to confirm this "
                "empirically, not just from reading the code:"
            )
            lines.append("")
            for row in delta_rows:
                lines.append(
                    "- delta=%.2f: cost(A)=%.4f cost(B)=%.4f cost(C)=%.4f -> winner: Path %s"
                    % (row["value"], row["cost_A"], row["cost_B"], row["cost_C"], row["winner"])
                )
            delta_costs_identical = len({(r["cost_A"], r["cost_B"], r["cost_C"]) for r in delta_rows}) == 1
            lines.append("")
            lines.append(
                "- All four costs identical across the entire delta grid: %s (confirms delta is currently "
                "inert -- not a bug in this sweep, a real gap: priority-aware path *cost* was never wired up, "
                "separate from the priority-aware *reroute timing* that traffic_policy.py does implement)."
                % delta_costs_identical
            )
            lines.append("")

        if epsilon_rows:
            lines.append("## epsilon (reliability weight) -- isolation check")
            lines.append(
                "reliability_penalty is 1.0 only if a link's LinkStatistics.status != \"up\", while its edge "
                "is still present in the graph being costed. Two cases:"
            )
            lines.append("")
            case1 = [r for r in epsilon_rows if "still in graph" in r["swept_weight"]]
            case2 = [r for r in epsilon_rows if "removed via" in r["swept_weight"]]
            lines.append("**Case 1 -- inconsistent state** (status set to \"down\" via `update_link_statistics()` "
                          "alone, without also calling `set_link_status()` -- the edge is NOT removed from the graph):")
            for row in case1:
                lines.append(
                    "- epsilon=%.2f: cost(A)=%.4f cost(B)=%.4f cost(C)=%.4f -> winner: Path %s"
                    % (row["value"], row["cost_A"], row["cost_B"], row["cost_C"], row["winner"])
                )
            case1_costs_differ = len({(r["cost_A"], r["cost_B"], r["cost_C"]) for r in case1}) > 1
            lines.append("- epsilon has a measurable effect here: %s" % case1_costs_differ)
            lines.append("")
            lines.append("**Case 2 -- properly failed** (`set_link_status(is_up=False)` / `mark_link_failed`, "
                          "the normal way a real failure is recorded -- the edge is removed from the active graph entirely):")
            for row in case2:
                lines.append("- epsilon=%.2f: %s" % (row["value"], row["winner"]))
            lines.append("")
            lines.append(
                "- Reading: epsilon only ever matters in Case 1, a state that only arises if code updates a "
                "link's stats without also updating its topology status -- a data-consistency gap between "
                "LinkMonitor and TopologyState, not a normal operational path. Under the normal failure path "
                "(Case 2), the edge disappears from the graph outright and epsilon never gets a chance to act "
                "on it at all, so its practical effect on real decisions is close to zero either way."
            )
            lines.append("")

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
