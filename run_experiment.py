#!/usr/bin/env python3
"""
Run Experiment - Single Entry Point
Run complete experiment cycle:
1. Start environment
2. Verify topology
3. Run baseline comparison
4. Run all scenarios
5. Evaluate results
"""

import argparse
from pathlib import Path

from experiments.baseline_comparison import BaselineComparison
from experiments.decision_engine_validation import DecisionEngineValidation
from experiments.network_state_validation import NetworkStateValidation
from experiments.pilot_experiments import PilotExperimentRunner
from experiments.stability_validation import StabilityValidation
from experiments.topology_validation import TopologyValidation


def main():
    parser = argparse.ArgumentParser(description="SDN Dissertation Experiment Runner")
    parser.add_argument("--stage", type=str, default="full",
                        choices=["1", "2", "3", "4", "5", "6", "full"],
                        help="Stage to run (1-6 or full)")
    parser.add_argument("--scenario", type=str, default="all",
                        help="Scenario to run for stage 6")
    parser.add_argument("--repeat", type=int, default=3,
                        help="Number of repeated runs for stage 6 aggregation")

    args = parser.parse_args()

    print("="*60)
    print("SDN Dissertation - Stability-Aware Traffic Engineering")
    print("="*60)
    print(f"Running stage: {args.stage}")
    print()

    if args.stage in ["1", "full"]:
        print("Stage 1: Environment Setup & Topology Verification")
        print("-"*60)
        stage1()

    if args.stage in ["2", "full"]:
        print("\nStage 2: Traffic Monitoring")
        print("-"*60)
        stage2()

    if args.stage in ["3", "full"]:
        print("\nStage 3: Baseline Routing")
        print("-"*60)
        stage3()

    if args.stage in ["4", "full"]:
        print("\nStage 4: Decision Engine")
        print("-"*60)
        stage4()

    if args.stage in ["5", "full"]:
        print("\nStage 5: Stability & Failure Recovery")
        print("-"*60)
        stage5()

    if args.stage in ["6", "full"]:
        print("\nStage 6: Pilot Experiments")
        print("-"*60)
        stage6(args.scenario, args.repeat)

    print("\n" + "="*60)
    print("Experiment complete!")
    print("="*60)


def stage1():
    """Stage 1: Environment & Topology"""
    print("  - Loading Geant2012 topology from data/Geant2012.graphml")
    print("  - Generating Stage 1 deliverables")
    summary = TopologyValidation(output_dir=Path("results/stage1")).run()
    print("  - Verified: %d nodes, %d links, connected=%s" % (
        summary["node_count"], summary["edge_count"], summary["connected"],
    ))


def stage2():
    """Stage 2: Traffic Monitoring"""
    print("  - Rate calculation, utilization, history window, link status")
    print("  - Generating Stage 2 deliverables")
    NetworkStateValidation(output_dir=Path("results/stage2")).run()


def stage3():
    """Stage 3: Baseline Routing"""
    print("  - Static shortest path baseline")
    print("  - Dynamic link-cost baseline")
    print("  - Generating Stage 3 deliverables")
    BaselineComparison(output_dir=Path("results/stage3")).run(repeat=3)


def stage4():
    """Stage 4: Decision Engine"""
    print("  - Threshold detection enabled")
    print("  - Persistence checker active")
    print("  - Change budget operational")
    print("  - Generating Stage 4 deliverables")
    DecisionEngineValidation(output_dir=Path("results/stage4")).run()


def stage5():
    """Stage 5: Stability"""
    print("  - Hold-down timer active")
    print("  - Traffic policies loaded")
    print("  - Generating Stage 5 deliverables")
    StabilityValidation(output_dir=Path("results/stage5")).run()


def stage6(scenario, repeat: int = 3):
    """Stage 6: Experiments"""
    print(f"  - Running scenario: {scenario}")
    print(f"  - Repeated runs: {repeat}")
    print("  - Collecting results")
    PilotExperimentRunner(output_dir=Path("results/pilot")).run(repeat=repeat, scenario=scenario)


if __name__ == "__main__":
    main()
