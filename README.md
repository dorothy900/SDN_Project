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
cat results/stage1/topology_validation.txt
```

### 2. Traffic Monitoring (Stage 2)

```bash
# Run stage 2
python3 run_experiment.py --stage 2

# Expected outputs in results/stage2/:
#   - rate_validation.csv
#   - history_window_test.csv
```

### 3. Baseline Routing (Stage 3)

```bash
# Run baseline comparison
python3 run_experiment.py --stage 3

# Expected outputs in results/stage3/:
#   - baseline_summary_repeated.csv
```

### 4. Decision Engine (Stage 4)

```bash
# Test stability-aware decisions
python3 run_experiment.py --stage 4

# Expected outputs in results/stage4/:
#   - decision_log.csv
#   - change_budget_test.csv
```

### 5. Stability & Failure Recovery (Stage 5)

```bash
# Test stability mechanisms
python3 run_experiment.py --stage 5

# Expected outputs in results/stage5/:
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

## Project Structure

```
sdn-dissertation/
 ├── README.md                      # This file
 ├── requirements.txt               # Python dependencies
 ├── .gitignore
 │
 ├── scripts/                       # Shell scripts
 │   ├── start_odl.sh
 │   └── start_topology.sh
 │
 ├── config/                        # Configuration
 │   ├── topology.yaml
 │   ├── links.yaml
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
 ├── experiments/                   # Experiment automation (Stage 6)
 │   ├── traffic_generator.py
 │   ├── scenario_increasing_load.py
 │   ├── scenario_congestion.py
 │   ├── scenario_failure_recovery.py
 │   ├── scenario_stale_stats.py
 │   └── run_baseline_comparison.py
 │
 ├── evaluation/                    # Result analysis
 │   ├── parse_results.py
 │   └── calculate_metrics.py
 │
 ├── tests/                         # Unit tests
 │   ├── test_statistics_collector.py
 │   ├── test_threshold_detector.py
 │   ├── test_persistence_checker.py
 │   ├── test_path_cost.py
 │   ├── test_change_budget.py
 │   └── test_stability_manager.py
 │
 ├── run_experiment.py              # Main entry point
 │
 └── results/                       # Output directory
     ├── stage1/
     ├── stage2/
     ├── stage3/
     ├── stage4/
     ├── stage5/
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
