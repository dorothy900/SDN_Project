# SDN Dissertation - Stability-Aware Traffic Engineering

Resilient and Stability-Aware SDN Traffic Engineering Framework for Dynamic Network Conditions.

## Environment Requirements

- Ubuntu 20.04+, Mininet, Open vSwitch
- **Two virtualenvs** (RYU pins old eventlet/dnspython that cannot coexist
  with the modern numpy/scipy/matplotlib stack the analysis uses):

  | venv | Python | for | install |
  |---|---|---|---|
  | `.venv` | 3.11+ | experiments, tests, figures (all `python3 ...` / `pytest` below) | `pip install -r requirements.txt` |
  | `ryu-env` | 3.9 | only `ryu-manager` for the live controller (`scripts/ryu/`) | `python3.9 -m venv ryu-env && ryu-env/bin/pip install -r requirements-ryu.txt` |

  Neither venv is committed or part of the project source; recreate them
  from the requirements files (and exclude both, plus `results/`, from any
  archived copy).

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

**Port/link statistics come from OVS directly.**
`StatisticsCollector.parse_ovs_port_stats()` (`src/monitor/statistics_collector.py`) queries
`ovs-ofctl dump-ports` directly and feeds rate → utilization → `NetworkState` → path-cost-based
routing. In the live deployment the RYU app (`scripts/ryu/stability_aware_te.py`) uses the
same math on OpenFlow `OFPPortStatsReply` counters. An early OpenDaylight REST path was evaluated
and dropped.

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
python3 -m experiments.cost_formula.sensitivity_analysis
# resilience-layer evidence / ROC calibration
python3 -m experiments.resilience.resilience_avoidance
python3 -m experiments.resilience.resilience_sensitivity
# -> results/pilot/sensitivity/{threshold_persistence_sweep,hold_down_sweep}.csv + sensitivity_report.md
```

## Project Structure

```
sdn-dissertation/
 ├── README.md                      # This file
 ├── requirements.txt               # experiments / tests / figures deps  (.venv)
 ├── requirements-ryu.txt           # RYU controller deps                 (ryu-env)
 ├── experiment.py                  # main entry point: experiment.py --stage {1..6|full}
 ├── topology.py                    # Mininet GEANT topology definition
 ├── .gitignore
 │
 ├── docs/                          # compliance_check.md (technical log),
 │                                  # methodology_narrative_outline.md
 │
 ├── scripts/                       # Deployment & real-network verification
 │   ├── start_topology.sh
 │   ├── ryu/                       # controller-side
 │   │   ├── stability_aware_te.py          # the full src/ stack embedded in a RYU controller
 │   │   └── *_probe.py                     # RYU/topology connectivity probes
 │   └── mininet/                   # real Mininet/OVS checks (run with sudo)
 │       ├── _common.py                     # shared net build / rule push / iperf helpers
 │       ├── path_verification.py           # real rule push, full GeantTopology
 │       ├── failure_recovery_demo.py       # real link failure -> reroute -> recovery
 │       ├── abnormal_loss_check.py         # real tc-netem loss + iperf: the resilience loss signal
 │       ├── link_flap_check.py             # real link flap + iperf: the resilience flap signal
 │       └── *_check.py                     # cost-formula independence / calibration on real hardware
 │
 ├── config/                        # decision.yaml (thresholds, weights, resilience gate),
 │                                  # policies.yaml (traffic classes), topology.yaml
 │
 ├── src/                           # Core source code (controller-agnostic algorithm)
 │   ├── monitor/                   # telemetry + per-link trackers, fronted by NetworkState
 │   │   ├── statistics_collector.py · network_state.py · topology_state.py · link_monitor.py
 │   │   ├── history_store.py · link_churn_tracker.py · link_flap_tracker.py
 │   │   ├── delay_jitter_tracker.py · loss_jitter_tracker.py · link_capacity.py · models.py
 │   │   └── ...
 │   ├── routing/                   # graph_builder.py (7-weight cost) · resilience_gate.py
 │   │   ├── congestion_model.py · path is chosen here
 │   │   └── static_shortest_path.py · dynamic_baseline.py · flow_installer.py
 │   ├── decision/                  # decision_engine.py orchestrates the 6 stability gates
 │   │   └── threshold_detector · persistence_checker · change_budget · path_cost · decision_logger
 │   └── stability/                 # stability_manager · failure_handler · recovery_manager · traffic_policy
 │
 ├── experiments/                   # offline simulation harness + all experiments
 │   ├── common/                    # shared: simulation_common (static/dynamic/proposed driver
 │   │                              #   harness), traffic_generator, sndlib_demand, independence_stats
 │   ├── validation/                # Stage 1-6 drivers: does the real src/ code reproduce the
 │   │                              #   design? (topology/network_state/decision_engine checks,
 │   │                              #   baseline_comparison, stability, pilot_experiments)
 │   ├── scenarios/                 # Experiments A-E + generalization / per-seed / per-sample variants
 │   │   ├── {increasing_load,congestion,failure_recovery,stale_stats,priority_policy}.py
 │   │   └── *_generalization.py    #   each scenario re-run over 23 real GEANT pairs x 5 seeds
 │   ├── cost_formula/              # weight_search_comparison, pareto_weight_analysis,
 │   │                              #   sensitivity_analysis, *_independence* (7-weight formula justification)
 │   └── resilience/                # resilience_avoidance (evidence), resilience_sensitivity (ROC)
 │
 ├── figures/                       # data-figure generation, run from the repo root:
 │   ├── make_figures.py            #   python3 -m figures.make_figures  -> results/figures/*.png
 │   └── make_topology_figure.py    #   the real GEANT topology map
 │
 ├── evaluation/                    # result parsing / metrics
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
 └── results/                       # ALL run output -- git-ignored, regenerated by the
                                    # commands above. Not part of the source: may be a
                                    # real directory or a symlink to one kept outside the
                                    # project. Exclude it (and the two venvs) from any
                                    # archived / submitted copy; the figure bundle and the
                                    # reproduce commands are the deliverable, not this dir.
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
