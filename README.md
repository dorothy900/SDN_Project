# SDN Dissertation - Stability-Aware Traffic Engineering

Resilient and Stability-Aware SDN Traffic Engineering Framework for Dynamic Network Conditions.

## Environment Requirements

- Ubuntu 20.04+
- Python 3.8+
- Mininet
- Open vSwitch
- OpenDaylight (optional)
- networkx, matplotlib, numpy, pyyaml, requests

Install dependencies:

```bash
pip install -r requirements.txt
```

## Quick Start

### 1. Environment Setup & Topology Verification (Stage 1)

```bash
# Verify topology structure
python3 run_experiment.py --stage 1

# View results
cat results/topology/topology_validation.txt
```

### 2. Traffic Monitoring (Stage 2)

```bash
# Run stage 2
python3 run_experiment.py --stage 2

# Expected outputs in results/network_state/:
#   - rate_validation.csv
#   - history_window_test.csv
```

**Port/link statistics come from OVS directly, not from OpenDaylight.**
`StatisticsCollector.parse_ovs_port_stats()` (`src/monitor/statistics_collector.py`) queries
`ovs-ofctl dump-ports` directly and is the complete, working path that feeds rate → utilization →
`NetworkState` → path-cost-based routing. `ODLClient.get_port_statistics()`
(`src/monitor/odl_client.py`) is an alternative, controller-mediated path to the same data that was
never finished (it returns `[]` even on a successful response) and isn't called anywhere in the
current pipeline — see the note at the top of `odl_client.py` for why it's left that way.

### 3. Baseline Routing (Stage 3)

```bash
# Run baseline comparison
python3 run_experiment.py --stage 3

# Expected outputs in results/baseline_comparison/:
#   - baseline_summary_repeated.csv
```

### 4. Decision Engine (Stage 4)

```bash
# Test stability-aware decisions
python3 run_experiment.py --stage 4

# Expected outputs in results/decision_engine/:
#   - decision_log.csv
#   - change_budget_test.csv
```

### 5. Stability & Failure Recovery (Stage 5)

```bash
# Test stability mechanisms
python3 run_experiment.py --stage 5

# Expected outputs in results/stability/:
#   - hysteresis_trace.csv
#   - priority_policy_test.csv
```

### 6. Full Experiment (All Stages)

```bash
# Run all stages and scenarios
python3 run_experiment.py --stage full

# Run specific scenario
python3 run_experiment.py --stage full --scenario congestion
```

**`--stage full` runs Experiments A–D only (increasing load, congestion, failure/recovery, stale
stats).** Experiment E (priority-aware policy) and the parameter sensitivity analysis are separate,
opt-in runs — the default command above does not include them:

```bash
# Include Experiment E (priority policy) in the aggregated pilot report
python3 run_experiment.py --stage 6 --scenario all_plus_priority --repeat 5
# -> results/pilot/scenario5/, folded into pilot_summary.csv / full_results_repeated.csv

# Parameter sensitivity analysis (why the config/decision.yaml defaults are what they are)
python3 -m experiments.sensitivity_analysis
# -> results/pilot/sensitivity/{threshold_persistence_sweep,hold_down_sweep}.csv + sensitivity_report.md
```

## Project Structure

```
sdn-dissertation/
 ├── README.md                      # This file
 ├── requirements.txt               # Python dependencies
 ├── .gitignore
 │
 ├── scripts/                       # Deployment & real-network verification
 │   ├── start_odl.sh
 │   ├── start_topology.sh
 │   ├── mininet_path_verification.py       # real Mininet/OVS rule push, full GeantTopology
 │   └── mininet_failure_recovery_demo.py   # real link failure -> reroute -> recovery
 │
 ├── config/                        # Configuration
 │   ├── topology.yaml
 │   ├── links.yaml                 # legacy: duplicates decision.yaml's cost/threshold values, not read by any module
 │   ├── policies.yaml              # Traffic class priorities
 │   └── decision.yaml              # Rerouting parameters
 │
 ├── topology.py                    # Mininet topology definition
 │
 ├── src/                           # Core source code
 │   ├── monitor/                   # Network monitoring (Stages 1-2)
 │   │   ├── odl_client.py
 │   │   ├── statistics_collector.py
 │   │   ├── traffic_monitor.py
 │   │   ├── history_store.py
 │   │   ├── models.py
 │   │   ├── link_mapper.py
 │   │   ├── topology_state.py
 │   │   ├── link_monitor.py
 │   │   └── network_state.py
 │   │
 │   ├── routing/                   # Baseline routing (Stage 3)
 │   │   ├── graph_builder.py
 │   │   ├── static_shortest_path.py
 │   │   ├── dynamic_baseline.py
 │   │   └── flow_installer.py
 │   │
 │   ├── decision/                  # Decision engine (Stage 4)
 │   │   ├── threshold_detector.py
 │   │   ├── persistence_checker.py
 │   │   ├── path_cost.py
 │   │   ├── decision_engine.py
 │   │   ├── decision_logger.py
 │   │   └── change_budget.py
 │   │
 │   └── stability/                 # Stability mechanisms (Stage 5)
 │       ├── stability_manager.py
 │       ├── failure_handler.py
 │       ├── recovery_manager.py
 │       └── traffic_policy.py
 │
 ├── experiments/                   # Experiment automation (Stages 1-6)
 │   ├── topology_validation.py           # Stage 1
 │   ├── network_state_validation.py      # Stage 2
 │   ├── baseline_comparison.py           # Stage 3
 │   ├── decision_engine_validation.py    # Stage 4
 │   ├── stability_validation.py          # Stage 5
 │   ├── pilot_experiments.py             # Stage 6 orchestrator
 │   ├── sensitivity_analysis.py          # parameter sweeps, opt-in (see Quick Start)
 │   ├── simulation_common.py             # shared static/dynamic/proposed harness for Stage 6
 │   ├── traffic_generator.py
 │   ├── scenario_increasing_load.py      # Experiment A
 │   ├── scenario_congestion.py           # Experiment B
 │   ├── scenario_failure_recovery.py     # Experiment C
 │   ├── scenario_stale_stats.py          # Experiment D
 │   └── scenario_priority_policy.py      # Experiment E, opt-in (see Quick Start)
 │
 ├── evaluation/                    # Result analysis ("analysis" -- there is no src/analysis/)
 │   ├── parse_results.py
 │   └── calculate_metrics.py
 │
 ├── tests/                         # Unit + integration tests (pytest tests/ -v)
 │   ├── test_statistics_collector.py
 │   ├── test_threshold_detector.py
 │   ├── test_persistence_checker.py
 │   ├── test_path_cost.py
 │   ├── test_change_budget.py
 │   ├── test_stability_manager.py
 │   ├── test_decision_engine.py
 │   ├── test_calculate_metrics.py
 │   ├── test_network_state_integration.py
 │   ├── test_baseline_comparison_integration.py
 │   ├── test_decision_engine_integration.py
 │   ├── test_stability_integration.py
 │   └── test_pilot_integration.py
 │
 ├── archive/                       # superseded scripts, see note below
 │   ├── verify_topology_monitor.py
 │   ├── verify_topology_monitor_acceptance_criteria.py
 │   └── verify_network_state.py
 │
 ├── run_experiment.py              # Main entry point
 │
 └── results/                       # Output directory
     ├── topology/
     ├── network_state/
     ├── baseline_comparison/
     ├── decision_engine/
     ├── stability/
     └── pilot/
```

## Configuration

All parameters are in `config/`:
- `topology.yaml`: Topology & controller settings
- `links.yaml`: Link monitoring parameters
- `policies.yaml`: Traffic class priorities
- `decision.yaml`: Thresholds & stability parameters

## Testing

Run unit tests:

```bash
# All tests
pytest tests/ -v

# Specific test
pytest tests/test_threshold_detector.py -v
```

## Stability Mechanisms

1. **Threshold-based decision**: Only reroute when performance degrades significantly
2. **Hysteresis/persistence**: Ignore short-term fluctuations
3. **Hold-down timer**: Prevent rapid back-and-forth changes
4. **Change budget**: Limit reroute frequency
5. **Minimum improvement**: Only change paths when new path is clearly better

## Evaluation Metrics

**Network Performance**:
- End-to-end delay
- Throughput
- Packet loss

**Routing Stability**:
- Number of reroutes
- Number of flow updates

**Controller Efficiency**:
- Decision time

## Traffic Classes & Scenarios

**Classes**:
1. High: VoIP, Video (reroute immediately)
2. Medium: Web
3. Low: File Transfer

**Scenarios**:
1. Increasing Traffic Load
2. Local Link Congestion
3. Link Failure & Recovery
4. Stale Statistics
5. Priority-Aware Traffic Policy (opt-in, `--scenario all_plus_priority` — see Quick Start)

## Legacy scripts

`archive/verify_topology_monitor.py`, `archive/verify_topology_monitor_acceptance_criteria.py`,
and `archive/verify_network_state.py` are standalone scripts from early development (originally
named `verify_week1.py` / `verify_detailed_week1.py` / `verify_week2.py`). Their real logic
(topology loading, rate calculation, history window, link status, network-state interface) has
since been ported into `experiments/topology_validation.py` and
`experiments/network_state_validation.py`, which `run_experiment.py --stage 1` / `--stage 2`
actually call. The `archive/` scripts still run and still pass, but they are not part of the
pipeline — treat `experiments/*.py` + `pytest tests/` as the authoritative path, not these.
