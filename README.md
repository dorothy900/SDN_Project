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
python3 experiment.py --stage 1

# View results
cat results/topology/topology_validation.txt
```

### 2. Traffic Monitoring (Stage 2)

```bash
# Run stage 2
python3 experiment.py --stage 2

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
python3 experiment.py --stage 3

# Expected outputs in results/baseline_comparison/:
#   - baseline_summary_repeated.csv
```

### 4. Decision Engine (Stage 4)

```bash
# Test stability-aware decisions
python3 experiment.py --stage 4

# Expected outputs in results/decision_engine/:
#   - decision_log.csv
#   - change_budget_test.csv
```

### 5. Stability & Failure Recovery (Stage 5)

```bash
# Test stability mechanisms
python3 experiment.py --stage 5

# Expected outputs in results/stability/:
#   - hysteresis_trace.csv
#   - priority_policy_test.csv
```

### 6. Full Experiment (All Stages)

```bash
# Run all stages and scenarios
python3 experiment.py --stage full

# Run specific scenario
python3 experiment.py --stage full --scenario congestion
```

**`--stage full` runs Experiments A–D only (increasing load, congestion, failure/recovery, stale
stats).** Experiment E (priority-aware policy) and the parameter sensitivity analysis are separate,
opt-in runs — the default command above does not include them:

```bash
# Include Experiment E (priority policy) in the aggregated pilot report
python3 experiment.py --stage 6 --scenario all_plus_priority --repeat 5
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
 │   ├── topology_check.py           # Stage 1
 │   ├── network_state_check.py      # Stage 2
 │   ├── baseline_comparison.py           # Stage 3
 │   ├── decision_engine_check.py    # Stage 4
 │   ├── stability.py          # Stage 5
 │   ├── pilot_experiments.py             # Stage 6 orchestrator
 │   ├── sensitivity_analysis.py          # parameter sweeps, opt-in (see Quick Start)
 │   ├── simulation_common.py             # shared static/dynamic/proposed harness for Stage 6
 │   ├── traffic_generator.py
 │   ├── increasing_load.py      # Experiment A
 │   ├── congestion.py           # Experiment B
 │   ├── failure_recovery.py     # Experiment C
 │   ├── stale_stats.py          # Experiment D
 │   └── priority_policy.py      # Experiment E, opt-in (see Quick Start)
 │
 ├── evaluation/                    # Result analysis ("analysis" -- there is no src/analysis/)
 │   ├── parse_results.py
 │   └── calculate_metrics.py
 │
 ├── tests/                         # Unit + integration tests (pytest tests/ -v)
 │   ├── statistics_collector.py
 │   ├── threshold_detector.py
 │   ├── persistence_checker.py
 │   ├── path_cost.py
 │   ├── change_budget.py
 │   ├── stability_manager.py
 │   ├── decision_engine.py
 │   ├── calculate_metrics.py
 │   ├── flow_installer.py
 │   ├── network_state_integration.py
 │   ├── baseline_comparison_integration.py
 │   ├── decision_engine_integration.py
 │   ├── stability_integration.py
 │   └── pilot_integration.py
 │
 ├── experiment.py              # Main entry point
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
- `policies.yaml`: Traffic class priorities
- `decision.yaml`: Thresholds & stability parameters

## Testing

Run unit tests:

```bash
# All tests
pytest tests/ -v

# Specific test
pytest tests/threshold_detector.py -v
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
